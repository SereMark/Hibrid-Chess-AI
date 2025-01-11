import os, time, numpy as np, torch, chess, torch.nn.functional as F, threading
from torch.utils.data import DataLoader, TensorDataset
from torch.amp import autocast, GradScaler
from multiprocessing import Pool, cpu_count, Manager
from PyQt5.QtCore import pyqtSignal
from src.base.base_worker import BaseWorker
from src.models.model import ChessModel
from src.utils.chess_utils import get_total_moves, get_move_mapping, convert_board_to_tensor, get_game_result
from src.utils.common_utils import initialize_random_seeds, format_time_left, wait_if_paused, initialize_optimizer, initialize_scheduler, update_progress_time_left
from src.utils.mcts import MCTS
from src.utils.checkpoint_manager import CheckpointManager

def play_and_collect_wrapper(args):
    (model_state_dict, device_type, simulations, c_puct, temperature, games_per_process, stop_event, pause_event, seed, stats_queue, move_mapping, total_moves) = args

    initialize_random_seeds(seed)
    device = torch.device(device_type)
    model = ChessModel(total_moves).to(device)
    try:
        model.load_state_dict(model_state_dict)
    except Exception as e:
        stats_queue.put({"error": f"Failed to load state_dict in worker: {str(e)}"})
        return ([], [], [], [], [])
    model.to(device)
    model.eval()
    inputs_list = []
    policy_targets_list = []
    value_targets_list = []
    results_list = []
    game_lengths_list = []
    avg_mcts_visits_list = []

    @torch.no_grad()
    def policy_value_fn(board):
        board_tensor = convert_board_to_tensor(board)
        board_tensor = torch.from_numpy(board_tensor).float().unsqueeze(0).to(device)
        policy_logits, value_out = model(board_tensor)
        policy = torch.softmax(policy_logits, dim=1).cpu().numpy()[0]
        value_float = value_out.cpu().item()
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return {}, value_float
        action_probs = {}
        total_prob = 0
        for m in legal_moves:
            idx = move_mapping.get_index_by_move(m)
            if idx is not None and idx < len(policy):
                prob = max(policy[idx], 1e-8)
                action_probs[m] = prob
                total_prob += prob
            else:
                action_probs[m] = 1e-8
        if total_prob > 0:
            for k in action_probs:
                action_probs[k] /= total_prob
        else:
            uniform_prob = 1.0 / len(legal_moves)
            for k in action_probs:
                action_probs[k] = uniform_prob
        return action_probs, value_float

    for _ in range(games_per_process):
        if stop_event.is_set():
            break
        wait_if_paused(pause_event)
        board = chess.Board()
        mcts = MCTS(policy_value_fn, c_puct, simulations)
        mcts.set_root_node(board)
        states, mcts_probs, current_players = [], [], []
        move_count = 0
        max_moves = 200
        total_visits = 0
        num_moves = 0
        while not board.is_game_over() and move_count < max_moves:
            action_probs = mcts.get_move_probs(temperature)
            if not action_probs:
                break
            moves_list = list(action_probs.keys())
            probs_array = np.array(list(action_probs.values()), dtype=np.float32)
            probs_array /= np.sum(probs_array)
            chosen_move = np.random.choice(moves_list, p=probs_array)
            board_tensor = convert_board_to_tensor(board)
            states.append(board_tensor)
            prob_arr = np.zeros(total_moves, dtype=np.float32)
            for mv, pr in action_probs.items():
                idx = move_mapping.get_index_by_move(mv)
                if idx is not None and 0 <= idx < total_moves:
                    prob_arr[idx] = pr
            mcts_probs.append(prob_arr)
            current_players.append(board.turn)
            board.push(chosen_move)
            mcts.update_with_move(chosen_move)
            if mcts.root:
                total_visits += mcts.root.n_visits
                num_moves += 1
            move_count += 1
        result = get_game_result(board)
        if board.is_checkmate():
            last_player = not board.turn
            winners = [
                result if player == last_player else -result for player in current_players
            ]
        else:
            winners = [0.0 for _ in current_players]
        game_length = len(states)
        visits_avg = (total_visits / num_moves) if num_moves > 0 else 0
        inputs_list.extend(states)
        policy_targets_list.extend(mcts_probs)
        value_targets_list.extend(winners)
        results_list.append(result)
        game_lengths_list.append(game_length)
        avg_mcts_visits_list.append(visits_avg)
    total_games = len(results_list)
    wins = results_list.count(1.0)
    losses = results_list.count(-1.0)
    draws = results_list.count(0.0)
    avg_length = (
        (sum(game_lengths_list) / len(game_lengths_list))
        if game_lengths_list
        else 0
    )
    avg_visits = (
        (sum(avg_mcts_visits_list) / len(avg_mcts_visits_list))
        if avg_mcts_visits_list
        else 0
    )
    st = {
        "total_games": total_games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "avg_game_length": avg_length,
        "avg_mcts_visits": avg_visits,
    }
    stats_queue.put(st)
    return (
        inputs_list,
        policy_targets_list,
        value_targets_list,
        results_list,
        game_lengths_list,
    )

class ReinforcementWorker(BaseWorker):
    stats_update = pyqtSignal(dict)

    def __init__(self, model_path, num_iterations, num_games_per_iteration, simulations, c_puct, temperature, num_epochs, batch_size, num_threads, save_checkpoints, checkpoint_interval, checkpoint_type, checkpoint_interval_minutes, checkpoint_batch_interval, random_seed=42, optimizer_type="adamw", learning_rate=0.0001, weight_decay=1e-4, scheduler_type="cosineannealingwarmrestarts"):
        super().__init__()
        self.save_checkpoints = save_checkpoints
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_type = checkpoint_type
        self.checkpoint_interval_minutes = checkpoint_interval_minutes
        self.checkpoint_batch_interval = checkpoint_batch_interval
        self.checkpoint_dir = os.path.join("models", "checkpoints", "reinforcement")
        self.random_seed = random_seed
        self.batch_size = batch_size
        self.optimizer_type = optimizer_type
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.scheduler_type = scheduler_type
        self.num_workers = num_threads
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        initialize_random_seeds(self.random_seed)
        self.model_path = model_path
        self.num_iterations = num_iterations
        self.num_games_per_iteration = num_games_per_iteration
        self.simulations = simulations
        self.c_puct = c_puct
        self.temperature = temperature
        self.num_epochs = num_epochs
        self.num_threads = num_threads
        self.stats_fn = self.stats_update.emit
        self.lock = threading.Lock()
        self.results = []
        self.game_lengths = []
        self.total_games_played = 0
        self.scaler = (GradScaler(device="cuda") if self.device.type == "cuda" else GradScaler())
        self.current_epoch = 1
        self.batch_idx = None
        self.start_iteration = 0
        self.move_mapping = get_move_mapping()
        self.total_moves = get_total_moves()
        self.model = ChessModel(self.total_moves).to(self.device)
        self.optimizer = initialize_optimizer(
            self.model,
            self.optimizer_type,
            self.learning_rate,
            self.weight_decay,
            logger=self.logger,
        )
        self.scheduler = None
        self.checkpoint_manager = CheckpointManager(
            checkpoint_dir=self.checkpoint_dir,
            checkpoint_type=self.checkpoint_type,
            checkpoint_interval=self.checkpoint_interval,
            logger=self.logger,
        )
        self.total_batches_processed = 0
        self.model_state_dict = None
        self._compute_total_steps()

    def _compute_total_steps(self):
        games_per_iteration = self.num_games_per_iteration
        epochs = self.num_epochs
        if self.batch_size:
            batches_per_epoch = (games_per_iteration // self.batch_size) * epochs
        else:
            batches_per_epoch = (games_per_iteration // 128) * epochs
        self.total_steps = self.num_iterations * games_per_iteration + self.num_iterations * batches_per_epoch

    def run_task(self):
        self.logger.info("Initializing reinforcement worker with model and optimizer.")
        if self.model_path and os.path.exists(self.model_path):
            checkpoint = self.checkpoint_manager.load(self.model_path, self.device, self.model, self.optimizer, self.scheduler)
            if checkpoint:
                self.start_iteration = checkpoint.get('iteration', 0)
                self.current_epoch = checkpoint.get('epoch', 1)
                self.batch_idx = checkpoint.get('batch_idx', None)
                self.results = checkpoint.get('training_stats', {}).get('results', [])
                self.game_lengths = checkpoint.get('training_stats', {}).get('game_lengths', [])
                self.total_games_played = checkpoint.get('training_stats', {}).get('total_games_played', 0)
                self.total_batches_processed = checkpoint.get('batch_idx', 0)
                self.logger.info(f"Resuming from iteration {self.start_iteration}, epoch {self.current_epoch}, batch {self.batch_idx}.")
            else:
                self.logger.info("No valid checkpoint found. Starting from scratch.")
        else:
            self.logger.info("No checkpoint path provided or file does not exist. Starting from scratch.")
        self.start_time = time.time()
        for iteration in range(self.start_iteration, self.num_iterations):
            if self._is_stopped.is_set():
                break
            iteration_start = time.time()
            self.logger.info(f"Starting iteration {iteration + 1}/{self.num_iterations}.")
            self.current_epoch = 1
            self.model.eval()
            self_play_data = self._generate_self_play_data()
            self.model.train()
            self._train_on_self_play_data(self_play_data, iteration)
            if self.save_checkpoints:
                checkpoint_data = {
                    'model_state_dict': {k: v.cpu() for k, v in self.model.state_dict().items()},
                    'optimizer_state_dict': {k: v for k, v in self.optimizer.state_dict().items()},
                    'scheduler_state_dict': {k: v for k, v in self.scheduler.state_dict().items()} if self.scheduler else None,
                    'iteration': iteration + 1,
                    'training_stats': {
                        'total_games_played': self.total_games_played,
                        'results': self.results,
                        'game_lengths': self.game_lengths
                    },
                    'batch_idx': self.total_batches_processed,
                    'epoch': self.current_epoch
                }
                if self.checkpoint_type == "iteration" and self.checkpoint_manager.should_save(iteration=iteration + 1):
                    self.checkpoint_manager.save(checkpoint_data)
                if self.checkpoint_type == "batch" and self.checkpoint_manager.should_save(batch_idx=self.total_batches_processed):
                    self.checkpoint_manager.save(checkpoint_data)
            iteration_time = time.time() - iteration_start
            self.logger.info(f"Iteration {iteration + 1} finished in {format_time_left(iteration_time)}.")
            if self.stats_fn:
                self.stats_fn(
                    {
                        "iteration": iteration + 1,
                        "total_games_played": self.total_games_played,
                    }
                )
        final_dir = os.path.join("models", "saved_models")
        final_path = os.path.join(final_dir, "final_model.pth")
        os.makedirs(final_dir, exist_ok=True)
        checkpoint = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "scheduler_state_dict": self.scheduler.state_dict() if self.scheduler else None,
            "training_stats": {
                "total_games_played": self.total_games_played,
                "results": self.results,
                "game_lengths": self.game_lengths,
            }
        }
        try:
            torch.save(checkpoint, final_path)
            self.logger.info(f"Final model saved at {final_path}")
        except Exception as e:
            self.logger.error(f"Error saving final model: {str(e)}")
        self.task_finished.emit()
        self.finished.emit()

    def _generate_self_play_data(self):
        num_processes = min(self.num_threads, cpu_count())
        games_per_process = self.num_games_per_iteration // num_processes
        remainder = self.num_games_per_iteration % num_processes
        manager = Manager()
        stop_event = manager.Event()
        pause_event = manager.Event()
        if self._is_stopped.is_set():
            stop_event.set()
        if not self._is_paused.is_set():
            pause_event.clear()
        else:
            pause_event.set()
        stats_queue = manager.Queue()
        seeds = [self.random_seed + i for i in range(num_processes)]
        args = []
        for i in range(num_processes):
            gpp = games_per_process + (1 if i < remainder else 0)
            args.append(
                (
                    self.model_state_dict if self.model_state_dict else {k: v.cpu() for k, v in self.model.state_dict().items()},
                    self.device.type,
                    self.simulations,
                    self.c_puct,
                    self.temperature,
                    gpp,
                    stop_event,
                    pause_event,
                    seeds[i],
                    stats_queue,
                    self.move_mapping,
                    self.total_moves
                )
            )
        with Pool(processes=num_processes) as pool:
            results = pool.map(play_and_collect_wrapper, args)
        while not stats_queue.empty():
            st = stats_queue.get()
            if "error" in st:
                self.logger.error(st["error"])
                continue
            if self.stats_fn:
                self.stats_fn(st)
            with self.lock:
                self.total_batches_processed += st.get("total_games", 0)
            update_progress_time_left(
                self.progress_update,
                self.time_left_update,
                self.start_time,
                self.total_batches_processed,
                self.total_steps,
            )
        inputs_list = []
        policy_targets_list = []
        value_targets_list = []
        for res in results:
            inputs_list.extend(res[0])
            policy_targets_list.extend(res[1])
            value_targets_list.extend(res[2])
            self.results.extend(res[3])
            self.game_lengths.extend(res[4])
        total_positions = len(inputs_list)
        if total_positions == 0:
            self.logger.warning("No self-play data generated this iteration.")
            return (
                torch.empty(0, device=self.device),
                torch.empty(0, device=self.device),
                torch.empty(0, device=self.device),
            )
        self.total_games_played += self.num_games_per_iteration
        inputs = torch.from_numpy(np.array(inputs_list, dtype=np.float32)).to(self.device)
        policy_targets = torch.from_numpy(np.array(policy_targets_list, dtype=np.float32)).to(
            self.device
        )
        value_targets = torch.tensor(
            value_targets_list, dtype=torch.float32, device=self.device
        )
        return inputs, policy_targets, value_targets

    def _train_on_self_play_data(self, self_play_data, iteration):
        inputs, policy_targets, value_targets = self_play_data
        if inputs.numel() == 0:
            return
        dataset = TensorDataset(inputs.cpu(), policy_targets.cpu(), value_targets.cpu())
        loader = DataLoader(
            dataset,
            batch_size=self.batch_size,
            shuffle=True,
            pin_memory=(self.device.type == "cuda"),
            num_workers=min(os.cpu_count(), 8),
        )
        total_steps_for_training = self.num_epochs * len(loader)
        try:
            self.scheduler = initialize_scheduler(
                self.optimizer,
                self.scheduler_type,
                total_steps=total_steps_for_training,
                logger=self.logger,
            )
        except ValueError as ve:
            self.logger.error(f"Scheduler init error: {str(ve)}")
            self.scheduler = None
        start_epoch = self.current_epoch
        epoch_start = time.time()
        accumulation_steps = max(256 // self.batch_size, 1)
        accumulate_count = 0
        for epoch in range(start_epoch, self.num_epochs + 1):
            if self._is_stopped.is_set():
                break
            self.logger.info(f"Epoch {epoch}/{self.num_epochs}, iteration {iteration + 1}.")
            self.current_epoch = epoch
            train_iterator = iter(loader)
            if epoch == start_epoch and self.batch_idx is not None:
                skip_batches = self.batch_idx
                if skip_batches >= len(loader):
                    continue
                for _ in range(skip_batches):
                    try:
                        next(train_iterator)
                    except StopIteration:
                        break
            total_loss = 0.0
            local_steps = 0
            inner_start = time.time()
            for batch_idx, (batch_inputs, batch_policy_targets, batch_value_targets) in enumerate(train_iterator, 1):
                if self._is_stopped.is_set():
                    break
                wait_if_paused(self._is_paused)
                batch_inputs = batch_inputs.to(self.device, non_blocking=True)
                batch_policy_targets = batch_policy_targets.to(self.device, non_blocking=True)
                batch_value_targets = batch_value_targets.to(self.device, non_blocking=True)
                smoothing = 0.1
                confidence = 1.0 - smoothing
                n_classes = batch_policy_targets.size(1)
                smoothed_labels = batch_policy_targets * confidence + (1 - batch_policy_targets) * (smoothing / (n_classes - 1))
                with autocast(device_type=self.device.type):
                    policy_preds, value_preds = self.model(batch_inputs)
                    pol_loss = -(smoothed_labels * torch.log_softmax(policy_preds, dim=1)).mean()
                    val_loss = F.mse_loss(value_preds.view(-1), batch_value_targets)
                    loss = (pol_loss + val_loss) / accumulation_steps

                self.scaler.scale(loss).backward()
                accumulate_count += 1

                if (accumulate_count % accumulation_steps == 0) or (batch_idx == len(loader)):
                    total_norm = 0
                    for p in self.model.parameters():
                        if p.grad is not None:
                            param_norm = p.grad.data.norm(2)
                            total_norm += param_norm.item() ** 2
                    total_norm = total_norm ** 0.5
                    self.logger.info(f"Batch {batch_idx}: Gradient Norm = {total_norm:.4f}")

                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                    self.optimizer.zero_grad()
                    accumulate_count = 0
                if self.scheduler:
                    self.scheduler.step(epoch - 1 + batch_idx / len(loader))

                total_loss += (pol_loss.item() + val_loss.item()) * batch_inputs.size(0)
                del batch_inputs, batch_policy_targets, batch_value_targets, policy_preds, value_preds, loss
                torch.cuda.empty_cache()

                self.batch_idx = batch_idx
                with self.lock:
                    self.total_batches_processed += 1
                    local_steps += 1
                update_progress_time_left(
                    self.progress_update,
                    self.time_left_update,
                    self.start_time,
                    self.total_batches_processed,
                    self.total_steps,
                )
                if (
                    self.save_checkpoints
                    and self.checkpoint_type == "batch"
                    and self.checkpoint_manager.should_save(batch_idx=self.total_batches_processed)
                ):
                    checkpoint_data = {
                        'model_state_dict': {k: v.cpu() for k, v in self.model.state_dict().items()},
                        'optimizer_state_dict': {k: v for k, v in self.optimizer.state_dict().items()},
                        'scheduler_state_dict': {k: v for k, v in self.scheduler.state_dict().items()} if self.scheduler else None,
                        'epoch': epoch,
                        'batch_idx': self.total_batches_processed,
                        'iteration': iteration + 1,
                        'training_stats': {
                            'total_games_played': self.total_games_played,
                            'results': self.results,
                            'game_lengths': self.game_lengths,
                        }
                    }
                    self.checkpoint_manager.save(checkpoint_data)
                if (
                    self.save_checkpoints
                    and self.checkpoint_type == "iteration"
                    and self.checkpoint_manager.should_save(iteration=self.total_batches_processed)
                ):
                    checkpoint_data = {
                        'model_state_dict': {k: v.cpu() for k, v in self.model.state_dict().items()},
                        'optimizer_state_dict': {k: v for k, v in self.optimizer.state_dict().items()},
                        'scheduler_state_dict': {k: v for k, v in self.scheduler.state_dict().items()} if self.scheduler else None,
                        'epoch': epoch,
                        'batch_idx': self.total_batches_processed,
                        'iteration': self.total_batches_processed,
                        'training_stats': {
                            'total_games_played': self.total_games_played,
                            'results': self.results,
                            'game_lengths': self.game_lengths,
                        }
                    }
                    self.checkpoint_manager.save(checkpoint_data)
            avg_loss = total_loss / len(loader.dataset) if len(loader.dataset) > 0 else 0.0
            duration = time.time() - epoch_start
            self.logger.info(f"Epoch {epoch}/{self.num_epochs} average loss: {avg_loss:.4f} in {format_time_left(duration)}.")
            if self.stats_fn:
                self.stats_fn(
                    {
                        "iteration": iteration + 1,
                        "epoch": epoch,
                        "avg_loss": avg_loss,
                        "total_games_played": self.total_games_played,
                    }
                )
            epoch_start = time.time()
import os, time, numpy as np, torch, chess, torch.nn.functional as F, threading
from torch.utils.data import DataLoader, TensorDataset
from torch.amp import autocast, GradScaler
from multiprocessing import Pool, cpu_count, Manager
from PyQt5.QtCore import pyqtSignal
from src.base.base_worker import BaseWorker
from src.base.base_trainer import TrainerBase
from src.models.model import ChessModel
from src.utils.chess_utils import get_total_moves, get_move_mapping, convert_board_to_tensor
from src.utils.common_utils import initialize_random_seeds, format_time_left, log_message, should_stop, wait_if_paused
from src.utils.mcts import MCTS

def _play_and_collect_wrapper(args):
    (
        model_state_dict,
        device_type,
        simulations,
        c_puct,
        temperature,
        games_per_process,
        stop_event,
        pause_event,
        seed,
        stats_queue,
        move_mapping,
        total_moves
    ) = args

    initialize_random_seeds(seed)

    device = torch.device(device_type)

    model = ChessModel(num_moves=total_moves)
    model.load_state_dict(model_state_dict)
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
        board_tensor = torch.from_numpy(board_tensor).float().unsqueeze(0).to(
            device
        )
        policy_logits, value = model(board_tensor)
        policy = torch.softmax(policy_logits, dim=1).cpu().numpy()[0]
        value = value.cpu().item()
        legal_moves = list(board.legal_moves)
        action_probs = {}
        total_legal_prob = 0
        for move in legal_moves:
            move_index = move_mapping.get_index_by_move(move)
            if move_index is not None and move_index < len(policy):
                prob = max(policy[move_index], 1e-8)
                action_probs[move] = prob
                total_legal_prob += prob
            else:
                action_probs[move] = 1e-8
        if total_legal_prob > 0:
            action_probs = {
                k: v / total_legal_prob for k, v in action_probs.items()
            }
        else:
            action_probs = {move: 1.0 / len(legal_moves) for move in legal_moves}
        return action_probs, value

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
            moves = list(action_probs.keys())
            probs = np.array(list(action_probs.values()))
            probs /= np.sum(probs)
            move = np.random.choice(moves, p=probs)
            board_tensor = convert_board_to_tensor(board)
            states.append(board_tensor)
            prob_array = np.zeros(total_moves, dtype=np.float32)
            for m, p in action_probs.items():
                move_index = move_mapping.get_index_by_move(m)
                if (
                    move_index is not None
                    and 0 <= move_index < total_moves
                ):
                    prob_array[move_index] = p
            mcts_probs.append(prob_array)
            current_players.append(board.turn)
            board.push(move)
            mcts.update_with_move(move)
            if mcts.root:
                total_visits += mcts.root.n_visits
                num_moves += 1
            move_count += 1

        result = ReinforcementWorker.get_game_result(board)
        if board.is_checkmate():
            last_player = not board.turn
            winners = [
                result if player == last_player else -result
                for player in current_players
            ]
        else:
            winners = [0.0 for _ in current_players]
        game_length = len(states)
        avg_mcts_visits = (
            total_visits / num_moves if num_moves > 0 else 0
        )

        inputs_list.extend(states)
        policy_targets_list.extend(mcts_probs)
        value_targets_list.extend(winners)
        results_list.append(result)
        game_lengths_list.append(game_length)
        avg_mcts_visits_list.append(avg_mcts_visits)

    total_games = len(results_list)
    wins = results_list.count(1.0)
    losses = results_list.count(-1.0)
    draws = results_list.count(0.0)
    avg_game_length = (
        sum(game_lengths_list) / len(game_lengths_list)
        if game_lengths_list
        else 0
    )
    avg_mcts_visits = (
        sum(avg_mcts_visits_list) / len(avg_mcts_visits_list)
        if avg_mcts_visits_list
        else 0
    )
    stats = {
        "total_games": total_games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "avg_game_length": avg_game_length,
        "avg_mcts_visits": avg_mcts_visits,
    }
    stats_queue.put(stats)

    return (
        inputs_list,
        policy_targets_list,
        value_targets_list,
        results_list,
        game_lengths_list,
    )

class ReinforcementWorker(BaseWorker, TrainerBase):
    stats_update = pyqtSignal(dict)

    def __init__(
        self,
        model_path: str,
        num_iterations: int,
        num_games_per_iteration: int,
        simulations: int,
        c_puct: float,
        temperature: float,
        num_epochs: int,
        batch_size: int,
        automatic_batch_size: bool,
        num_threads: int,
        save_checkpoints: bool,
        checkpoint_interval: int,
        checkpoint_type: str,
        checkpoint_interval_minutes: int,
        checkpoint_batch_interval: int,
        checkpoint_path: str = None,
        random_seed: int = 42,
        optimizer_type: str = "adamw",
        learning_rate: float = 0.0005,
        weight_decay: float = 2e-4,
        scheduler_type: str = "cosineannealingwarmrestarts",
    ):
        super().__init__()
        TrainerBase.__init__(
            self,
            save_checkpoints=save_checkpoints,
            checkpoint_interval=checkpoint_interval,
            checkpoint_type=checkpoint_type,
            checkpoint_interval_minutes=checkpoint_interval_minutes,
            checkpoint_batch_interval=checkpoint_batch_interval,
            checkpoint_dir=os.path.join("models", "checkpoints", "self_play"),
            log_fn=self.log_update.emit,
            progress_fn=self.progress_update.emit,
            time_left_fn=self.time_left_update.emit,
            stop_event=self._is_stopped,
            pause_event=self._is_paused,
            random_seed=random_seed,
            automatic_batch_size=automatic_batch_size,
            batch_size=batch_size,
            model_class=ChessModel,
            optimizer_type=optimizer_type,
            learning_rate=learning_rate,
            weight_decay=weight_decay,
            scheduler_type=scheduler_type,
            num_workers=num_threads,
            device=None,
        )
        self.model_path = model_path
        self.num_iterations = num_iterations
        self.num_games_per_iteration = num_games_per_iteration
        self.simulations = simulations
        self.c_puct = c_puct
        self.temperature = temperature
        self.num_epochs = num_epochs
        self.num_threads = num_threads
        self.checkpoint_path = checkpoint_path
        self.stats_fn = self.stats_update.emit
        self.start_time = None
        self.total_games_played = 0
        self.results = []
        self.game_lengths = []
        self.lock = threading.Lock()
        self.scaler = (
            GradScaler(device="cuda")
            if self.device.type == "cuda"
            else GradScaler()
        )
        self.current_epoch = 1
        self.batch_idx = None
        self.start_iteration = 0

        self.move_mapping = get_move_mapping()
        self.total_moves = get_total_moves()
        self.model = ChessModel(num_moves=self.total_moves)
        if os.path.exists(self.model_path):
            checkpoint = torch.load(
                self.model_path,
                map_location=self.device,
                weights_only=False,
            )
            self.model.load_state_dict(checkpoint["model_state_dict"])
            log_message("Model loaded successfully.", self.log_fn)
        else:
            log_message("Model file not found. Starting from scratch.", self.log_fn)
        self.model.to(self.device)
        self.model.eval()

    @staticmethod
    @torch.no_grad()
    def get_game_result(board):
        result = board.result()
        if result == "1-0":
            return 1.0
        elif result == "0-1":
            return -1.0
        else:
            return 0.0

    def run_task(self):
        self.log_update.emit("Starting self-play training...")
        self.train()
        if self._is_stopped.is_set():
            self.log_update.emit("Self-play training stopped by user request.")

    def train(self):
        self._initialize()
        for iteration in range(self.start_iteration, self.num_iterations):
            if should_stop(self.stop_event):
                break
            iteration_start_time = time.time()
            log_message(
                f"\n=== Iteration {iteration + 1}/{self.num_iterations} ===",
                self.log_fn,
            )
            self.current_epoch = 1
            self.model.eval()
            self_play_data = self._generate_self_play_data()
            self.model.train()
            self._train_on_self_play_data(self_play_data, iteration)
            self.batch_idx = None
            if self.should_save_checkpoint(
                iteration=iteration + 1
            ):
                self.save_checkpoint(
                    epoch=self.current_epoch,
                    iteration=iteration + 1,
                )
            iteration_time = time.time() - iteration_start_time
            log_message(
                f"Iteration {iteration + 1} completed in {format_time_left(iteration_time)}",
                self.log_fn,
            )
        self._save_final_model()

    def _initialize(self):
        log_message("Initializing model and optimizer...", self.log_fn)
        num_moves = self.total_moves
        self.initialize_model(num_moves=num_moves)
        if os.path.exists(self.model_path):
            checkpoint = torch.load(
                self.model_path,
                map_location=self.device,
                weights_only=False,
            )
            self.model.load_state_dict(checkpoint["model_state_dict"])
            log_message("Model loaded successfully.", self.log_fn)
        else:
            log_message("Model file not found. Starting from scratch.", self.log_fn)

        self.initialize_optimizer()

        if self.scheduler_type.lower() != "none":
            if self.scheduler_type.lower() == "onecyclelr":
                total_steps = (
                    self.num_iterations
                    * self.num_epochs
                    * (self.num_games_per_iteration // self.batch_size)
                )
            else:
                total_steps = None
            self.initialize_scheduler(total_steps=total_steps)

        if (
            self.checkpoint_path
            and os.path.exists(self.checkpoint_path)
        ):
            checkpoint = self.load_checkpoint(
                self.checkpoint_path, map_location=self.device
            )
            if checkpoint:
                self.start_iteration = checkpoint.get("iteration", 0)
                self.total_batches_processed = checkpoint.get(
                    "total_batches_processed", 0
                )
                self.current_epoch = checkpoint.get("epoch", 1)
                self.batch_idx = checkpoint.get("batch_idx", None)
                training_stats = checkpoint.get("training_stats", {})
                self.total_games_played = training_stats.get(
                    "total_games_played", 0
                )
                self.results = training_stats.get("results", [])
                self.game_lengths = training_stats.get(
                    "game_lengths", []
                )
                log_message(
                    f"Resuming from checkpoint at iteration {self.start_iteration}.",
                    self.log_fn,
                )
        else:
            log_message(
                "No checkpoint found. Starting training from scratch.", self.log_fn
            )
            self.start_iteration = 0
            self.current_epoch = 1

        self.start_time = time.time()
        self.model_state_dict = {
            k: v.cpu() for k, v in self.model.state_dict().items()
        }

    def _generate_self_play_data(self):
        num_processes = min(self.num_threads, cpu_count())
        games_per_process = self.num_games_per_iteration // num_processes
        remainder = self.num_games_per_iteration % num_processes
        manager = Manager()
        stop_event = manager.Event()
        pause_event = manager.Event()
        if self.stop_event.is_set():
            stop_event.set()
        if not self.pause_event.is_set():
            pause_event.clear()
        else:
            pause_event.set()

        stats_queue = manager.Queue()

        seeds = [self.random_seed + i for i in range(num_processes)]

        args = []
        for i in range(num_processes):
            games = games_per_process + (1 if i < remainder else 0)
            args.append(
                (
                    self.model_state_dict,
                    self.device.type,
                    self.simulations,
                    self.c_puct,
                    self.temperature,
                    games,
                    stop_event,
                    pause_event,
                    seeds[i],
                    stats_queue,
                    self.move_mapping,
                    self.total_moves,
                )
            )
        with Pool(processes=num_processes) as pool:
            results = pool.map(_play_and_collect_wrapper, args)

        while not stats_queue.empty():
            stats = stats_queue.get()
            if self.stats_fn:
                self.stats_fn(stats)

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
            return (
                torch.empty(0, device=self.device),
                torch.empty(0, device=self.device),
                torch.empty(0, device=self.device),
            )
        inputs = torch.from_numpy(
            np.array(inputs_list, dtype=np.float32)
        ).to(self.device)
        policy_targets = torch.from_numpy(
            np.array(policy_targets_list, dtype=np.float32)
        ).to(self.device)
        value_targets = torch.tensor(
            value_targets_list, dtype=torch.float32, device=self.device
        )
        self.total_games_played += self.num_games_per_iteration
        return inputs, policy_targets, value_targets

    def _train_on_self_play_data(self, self_play_data, iteration):
        inputs, policy_targets, value_targets = self_play_data
        if inputs.numel() == 0:
            log_message(
                "No self-play data generated. Skipping training.", self.log_fn
            )
            return
        dataset = TensorDataset(
            inputs.cpu(), policy_targets.cpu(), value_targets.cpu()
        )
        loader = DataLoader(
            dataset,
            batch_size=self.batch_size,
            shuffle=True,
            pin_memory=(self.device.type == "cuda"),
            num_workers=min(os.cpu_count(), 8),
        )
        start_epoch = self.current_epoch
        for epoch in range(start_epoch, self.num_epochs + 1):
            if should_stop(self.stop_event):
                break
            log_message(
                f"Epoch {epoch}/{self.num_epochs} started.", self.log_fn
            )
            self.current_epoch = epoch
            train_iterator = iter(loader)
            if epoch == start_epoch and self.batch_idx is not None:
                skip_batches = self.batch_idx
                if skip_batches >= len(loader):
                    log_message(
                        f"Skip batches ({skip_batches}) exceed total batches ({len(loader)}). Skipping entire epoch.",
                        self.log_fn,
                    )
                    continue
                for _ in range(skip_batches):
                    try:
                        next(train_iterator)
                    except StopIteration:
                        break
            total_loss = 0.0
            for batch_idx, (batch_inputs, batch_policy_targets, batch_value_targets) in enumerate(
                train_iterator, 1
            ):
                self.total_batches_processed += 1
                if should_stop(self.stop_event):
                    break
                wait_if_paused(self.pause_event)
                batch_inputs = batch_inputs.to(
                    self.device, non_blocking=True
                )
                batch_policy_targets = batch_policy_targets.to(
                    self.device, non_blocking=True
                )
                batch_value_targets = batch_value_targets.to(
                    self.device, non_blocking=True
                )
                self.optimizer.zero_grad()
                with autocast(device_type=self.device.type):
                    policy_preds, value_preds = self.model(batch_inputs)
                    policy_loss = -(batch_policy_targets * torch.log_softmax(policy_preds, dim=1)).mean()
                    value_loss = F.mse_loss(
                        value_preds.view(-1), batch_value_targets
                    )
                    loss = policy_loss + value_loss
                self.scaler.scale(loss).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
                total_loss += loss.item()
                del batch_inputs, batch_policy_targets, batch_value_targets
                torch.cuda.empty_cache()
                self.batch_idx = batch_idx
                if self.should_save_checkpoint(
                    iteration=iteration + 1,
                    batch_idx=self.total_batches_processed,
                ):
                    self.save_checkpoint(
                        epoch=self.current_epoch,
                        batch_idx=self.total_batches_processed,
                        iteration=iteration + 1,
                    )
            avg_loss = total_loss / len(loader)
            log_message(
                f"Epoch {epoch}/{self.num_epochs}, Loss: {avg_loss:.4f}",
                self.log_fn,
            )

    def _save_final_model(self):
        final_model_dir = os.path.join("models", "saved_models")
        final_model_path = os.path.join(
            final_model_dir, "final_model.pth"
        )
        os.makedirs(final_model_dir, exist_ok=True)
        checkpoint = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "scheduler_state_dict": (
                self.scheduler.state_dict()
                if hasattr(self, "scheduler") and self.scheduler
                else None
            ),
            "training_stats": {
                "total_games_played": self.total_games_played,
                "results": self.results,
                "game_lengths": self.game_lengths,
            },
        }
        torch.save(checkpoint, final_model_path)
        log_message("Final model saved.", self.log_fn)
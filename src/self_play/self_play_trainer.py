import os, threading, time, numpy as np, torch, torch.nn.functional as F, torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from src.self_play.self_play import SelfPlay
from src.models.model import ChessModel
from torch.amp import autocast, GradScaler
from multiprocessing import Pool, cpu_count, Event, Manager
from src.utils.chess_utils import TOTAL_MOVES, initialize_move_mappings, estimate_batch_size
from src.utils.common_utils import (
    initialize_random_seeds,
    format_time_left,
    log_message,
    should_stop,
    wait_if_paused,
)

def _play_and_collect_wrapper(args):
    (
        model_state_dict,
        device,
        simulations,
        c_puct,
        temperature,
        games_per_process,
        stop_event,
        pause_event,
        seed,
        stats_queue
    ) = args

    initialize_move_mappings()
    initialize_random_seeds(seed)

    inputs_list = []
    policy_targets_list = []
    value_targets_list = []
    results_list = []
    game_lengths_list = []

    self_play = SelfPlay(
        model_state_dict=model_state_dict,
        device=device,
        n_simulations=simulations,
        c_puct=c_puct,
        temperature=temperature,
        stats_fn=stats_queue.put
    )
    for _ in range(games_per_process):
        if stop_event.is_set():
            break
        wait_if_paused(pause_event)
        (
            states,
            mcts_probs,
            winners,
            game_length,
            result,
        ) = self_play.play_game()
        inputs_list.extend(states)
        policy_targets_list.extend(mcts_probs)
        value_targets_list.extend(winners)
        results_list.append(result)
        game_lengths_list.append(game_length)
    return (
        inputs_list,
        policy_targets_list,
        value_targets_list,
        results_list,
        game_lengths_list,
    )

class SelfPlayTrainer:
    def __init__(
        self,
        model_path,
        output_dir,
        num_iterations,
        num_games_per_iteration,
        simulations,
        c_puct,
        temperature,
        num_epochs,
        batch_size,
        automatic_batch_size,
        num_threads,
        stop_event,
        pause_event,
        save_checkpoints=True,
        checkpoint_interval=1,
        checkpoint_type='iteration',
        checkpoint_interval_minutes=60,
        checkpoint_batch_interval=1000,
        checkpoint_path=None,
        random_seed=42,
        log_fn=None,
        progress_fn=None,
        time_left_fn=None,
        stats_fn=None,
    ):
        self.model_path = model_path
        self.output_dir = output_dir
        self.num_iterations = num_iterations
        self.num_games_per_iteration = num_games_per_iteration
        self.simulations = simulations
        self.c_puct = c_puct
        self.temperature = temperature
        self.num_epochs = num_epochs
        self.batch_size = batch_size
        self.automatic_batch_size = automatic_batch_size
        self.num_threads = num_threads
        self.stop_event = stop_event or Event()
        self.pause_event = pause_event or Event()
        self.pause_event.set()
        self.log_fn = log_fn
        self.progress_fn = progress_fn
        self.time_left_fn = time_left_fn
        self.stats_fn = stats_fn
        self.checkpoint_path = checkpoint_path
        self.random_seed = random_seed
        self.start_time = None
        self.total_games_played = 0
        self.results = []
        self.game_lengths = []
        self.lock = threading.Lock()
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.scaler = GradScaler(enabled=(self.device.type == 'cuda'))

        self.save_checkpoints = save_checkpoints
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_type = checkpoint_type
        self.checkpoint_interval_minutes = checkpoint_interval_minutes
        self.checkpoint_batch_interval = checkpoint_batch_interval
        self.last_checkpoint_time = time.time()
        self.total_batches_processed = 0
        self.current_epoch = 1
        self.batch_idx = None
        self.start_iteration = 0

    def should_save_checkpoint(self, iteration, epoch, batch_idx):
        if not self.save_checkpoints:
            return False
        if self.checkpoint_type == 'iteration':
            return iteration % self.checkpoint_interval == 0 and batch_idx is None
        elif self.checkpoint_type == 'epoch':
            return epoch % self.checkpoint_interval == 0 and batch_idx is None
        elif self.checkpoint_type == 'batch':
            return self.total_batches_processed % self.checkpoint_batch_interval == 0
        elif self.checkpoint_type == 'time':
            current_time = time.time()
            elapsed_minutes = (current_time - self.last_checkpoint_time) / 60
            if elapsed_minutes >= self.checkpoint_interval_minutes:
                self.last_checkpoint_time = current_time
                return True
            return False
        return False

    def save_checkpoint(self, iteration, epoch, batch_idx):
        checkpoint_dir = os.path.join('models', 'checkpoints')
        os.makedirs(checkpoint_dir, exist_ok=True)
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        if self.checkpoint_type == 'iteration':
            checkpoint_name = f'checkpoint_iteration_{iteration}_{timestamp}.pth'
        elif self.checkpoint_type == 'epoch':
            checkpoint_name = f'checkpoint_iteration_{iteration}_epoch_{epoch}_{timestamp}.pth'
        elif self.checkpoint_type == 'batch':
            checkpoint_name = f'checkpoint_iteration_{iteration}_epoch_{epoch}_batch_{batch_idx}_{timestamp}.pth'
        elif self.checkpoint_type == 'time':
            checkpoint_name = f'checkpoint_time_{timestamp}.pth'
        temp_path = os.path.join(checkpoint_dir, f'.temp_{checkpoint_name}')
        final_path = os.path.join(checkpoint_dir, checkpoint_name)
        try:
            checkpoint_data = {
                'model_state_dict': self.model.state_dict(),
                'optimizer_state_dict': self.optimizer.state_dict(),
                'iteration': iteration,
                'epoch': epoch,
                'batch_idx': batch_idx,
                'total_batches_processed': self.total_batches_processed,
                'training_stats': {
                    'total_games_played': self.total_games_played,
                    'results': self.results,
                    'game_lengths': self.game_lengths,
                },
            }
            torch.save(checkpoint_data, temp_path)
            os.replace(temp_path, final_path)
            log_message(f"Checkpoint saved: {checkpoint_name}", self.log_fn)
        except Exception as e:
            log_message(f"Error saving checkpoint: {str(e)}", self.log_fn)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            raise

    def train(self):
        self._initialize()
        for iteration in range(self.start_iteration, self.num_iterations):
            if should_stop(self.stop_event):
                break
            iteration_start_time = time.time()
            log_message(f"\n=== Iteration {iteration + 1}/{self.num_iterations} ===", self.log_fn)
            self.model.eval()
            self_play_data = self._generate_self_play_data()
            self.model.train()
            self._train_on_self_play_data(self_play_data, iteration)
            self.batch_idx = None
            if self.should_save_checkpoint(iteration + 1, self.current_epoch, None):
                self.save_checkpoint(iteration + 1, self.current_epoch, None)
            iteration_time = time.time() - iteration_start_time
            log_message(f"Iteration {iteration + 1} completed in {format_time_left(iteration_time)}", self.log_fn)
        self._save_final_model()

    def _initialize(self):
        initialize_move_mappings()

        log_message("Initializing model and optimizer...", self.log_fn)
        self.model = ChessModel(num_moves=TOTAL_MOVES).to(self.device)
        if os.path.exists(self.model_path):
            checkpoint = torch.load(self.model_path, map_location=self.device)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            log_message("Model loaded successfully.", self.log_fn)
        else:
            log_message("Model file not found. Starting from scratch.", self.log_fn)
        self.optimizer = optim.AdamW(self.model.parameters(), lr=0.0005, weight_decay=2e-4)
        if self.checkpoint_path and os.path.exists(self.checkpoint_path):
            checkpoint = torch.load(self.checkpoint_path, map_location=self.device)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            self.start_iteration = checkpoint.get('iteration', 0)
            self.total_batches_processed = checkpoint.get('total_batches_processed', 0)
            self.current_epoch = checkpoint.get('epoch', 1)
            self.batch_idx = checkpoint.get('batch_idx', None)
            self.total_games_played = checkpoint['training_stats'].get('total_games_played', 0)
            self.results = checkpoint['training_stats'].get('results', [])
            self.game_lengths = checkpoint['training_stats'].get('game_lengths', [])
            log_message(f"Resuming from checkpoint at iteration {self.start_iteration}.", self.log_fn)
        else:
            log_message("No checkpoint found. Starting training from scratch.", self.log_fn)
            self.start_iteration = 0
            self.current_epoch = 1
        if self.automatic_batch_size:
            self.batch_size = estimate_batch_size(self.model, self.device)
            log_message(f"Automatic batch size estimation: Using batch size {self.batch_size}", self.log_fn)
        else:
            log_message(f"Using manual batch size: {self.batch_size}", self.log_fn)
        self.start_time = time.time()

        initialize_random_seeds(self.random_seed)

        self.model_state_dict = {k: v.cpu() for k, v in self.model.state_dict().items()}

    def _generate_self_play_data(self):
        num_processes = min(self.num_threads, cpu_count())
        games_per_process = self.num_games_per_iteration // num_processes
        manager = Manager()
        stop_event = manager.Event()
        pause_event = manager.Event()
        stats_queue = manager.Queue()
        if should_stop(self.stop_event):
            stop_event.set()
        if self.pause_event.is_set():
            pause_event.set()

        seeds = [self.random_seed + i for i in range(num_processes)]

        args = [
            (
                self.model_state_dict,
                self.device.type,
                self.simulations,
                self.c_puct,
                self.temperature,
                games_per_process,
                stop_event,
                pause_event,
                seeds[i],
                stats_queue
            )
            for i in range(num_processes)
        ]
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
        inputs = torch.from_numpy(np.array(inputs_list, dtype=np.float32)).to(self.device)
        policy_targets = torch.from_numpy(np.array(policy_targets_list, dtype=np.float32)).to(self.device)
        value_targets = torch.tensor(value_targets_list, dtype=torch.float32, device=self.device)
        self.total_games_played += self.num_games_per_iteration
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
            pin_memory=(self.device.type == 'cuda'),
            num_workers=min(os.cpu_count(), 8),
        )
        start_epoch = self.current_epoch
        for epoch in range(start_epoch, self.num_epochs + 1):
            if should_stop(self.stop_event):
                break
            log_message(f"Epoch {epoch}/{self.num_epochs} started.", self.log_fn)
            self.current_epoch = epoch
            train_iterator = iter(loader)
            if epoch == start_epoch and self.batch_idx is not None:
                skip_batches = self.batch_idx
                if skip_batches >= len(loader):
                    log_message(f"Skip batches ({skip_batches}) exceed total batches ({len(loader)}). Skipping entire epoch.", self.log_fn)
                    continue
                for _ in range(skip_batches):
                    try:
                        next(train_iterator)
                    except StopIteration:
                        break
            else:
                self.batch_idx = None
            total_loss = 0
            for batch_idx, (batch_inputs, batch_policy_targets, batch_value_targets) in enumerate(train_iterator, 1):
                self.total_batches_processed += 1
                if should_stop(self.stop_event):
                    break
                wait_if_paused(self.pause_event)
                batch_inputs = batch_inputs.to(self.device, non_blocking=True)
                batch_policy_targets = batch_policy_targets.to(self.device, non_blocking=True)
                batch_value_targets = batch_value_targets.to(self.device, non_blocking=True)
                self.optimizer.zero_grad()
                with autocast(device_type=self.device.type, enabled=(self.device.type == 'cuda')):
                    policy_preds, value_preds = self.model(batch_inputs)
                    policy_loss = -(batch_policy_targets * torch.log_softmax(policy_preds, dim=1)).mean()
                    value_loss = F.mse_loss(value_preds.view(-1), batch_value_targets)
                    loss = policy_loss + value_loss
                self.scaler.scale(loss).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
                total_loss += loss.item()
                del batch_inputs, batch_policy_targets, batch_value_targets
                torch.cuda.empty_cache()
                self.batch_idx = batch_idx
                if self.should_save_checkpoint(iteration + 1, self.current_epoch, self.total_batches_processed):
                    self.save_checkpoint(iteration + 1, self.current_epoch, self.total_batches_processed)
            avg_loss = total_loss / len(loader)
            log_message(f"Epoch {epoch}/{self.num_epochs}, Loss: {avg_loss:.4f}", self.log_fn)

    def _save_model(self, iteration):
        os.makedirs(self.output_dir, exist_ok=True)
        model_save_path = os.path.join(self.output_dir, f'model_iteration_{iteration + 1}.pth')
        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'iteration': iteration + 1,
            'epoch': self.current_epoch,
            'total_batches_processed': self.total_batches_processed,
            'training_stats': {
                'total_games_played': self.total_games_played,
                'results': self.results,
                'game_lengths': self.game_lengths,
            },
        }
        torch.save(checkpoint, model_save_path)
        log_message(f"Model saved at iteration {iteration + 1}.", self.log_fn)
        self.model_path = model_save_path

    def _save_final_model(self):
        final_model_dir = os.path.join('models', 'saved_models')
        final_model_path = os.path.join(final_model_dir, 'final_model.pth')
        os.makedirs(final_model_dir, exist_ok=True)
        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'training_stats': {
                'total_games_played': self.total_games_played,
                'results': self.results,
                'game_lengths': self.game_lengths,
            },
        }
        torch.save(checkpoint, final_model_path)
        log_message("Final model saved.", self.log_fn)
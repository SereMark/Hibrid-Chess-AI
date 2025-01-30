import os
import time
import torch
import numpy as np
from torch.amp import GradScaler
from multiprocessing import Pool, cpu_count
from torch.utils.data import DataLoader, TensorDataset

from src.models.transformer import TransformerChessModel
from src.utils.checkpoint_manager import CheckpointManager
from src.training.reinforcement.play_and_collect_worker import PlayAndCollectWorker
from src.utils.chess_utils import get_total_moves
from src.utils.train_utils import (
    initialize_optimizer,
    initialize_scheduler,
    initialize_random_seeds,
    train_epoch
)

class ReinforcementWorker:
    def __init__(
        self,
        model_path,
        num_iterations,
        num_games_per_iteration,
        simulations_per_move,
        c_puct,
        temperature,
        epochs_per_iteration,
        batch_size,
        num_selfplay_threads,
        checkpoint_interval,
        random_seed,
        optimizer_type,
        learning_rate,
        weight_decay,
        scheduler_type,
        accumulation_steps,
        num_workers,
        policy_weight,
        value_weight,
        grad_clip,
        momentum,
        wandb_flag,
        progress_callback=None,
        status_callback=None
    ):
        initialize_random_seeds(random_seed)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.wandb_flag = wandb_flag
        self.progress_callback = progress_callback
        self.status_callback = status_callback

        self.model = TransformerChessModel(get_total_moves()).to(self.device)
        self.optimizer = initialize_optimizer(self.model, optimizer_type, learning_rate, weight_decay, momentum)
        self.scheduler = None
        self.scaler = GradScaler(enabled=(self.device.type=="cuda"))

        self.accumulation_steps = accumulation_steps
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_manager = CheckpointManager(
            os.path.join("models", "checkpoints", "reinforcement"), 
            "iteration",
            checkpoint_interval
        )

        self.model_path = model_path
        self.num_iterations = num_iterations
        self.num_games_per_iteration = num_games_per_iteration
        self.simulations_per_move = simulations_per_move
        self.c_puct = c_puct
        self.temperature = temperature
        self.epochs_per_iteration = epochs_per_iteration
        self.batch_size = batch_size
        self.num_selfplay_threads = num_selfplay_threads
        self.random_seed = random_seed
        self.num_workers = num_workers
        self.optimizer_type = optimizer_type
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.grad_clip = grad_clip
        self.momentum = momentum
        self.scheduler_type = scheduler_type
        self.policy_weight = policy_weight
        self.value_weight = value_weight

        self.results = []
        self.game_lengths = []
        self.total_games_played = 0
        self.start_iteration = 1
        self.best_metric = float('inf')
        self.best_iteration = 0
        self.history = []
        self.loaded_checkpoint = None

        if self.model_path and os.path.exists(self.model_path):
            self.loaded_checkpoint = self.checkpoint_manager.load(
                self.model_path, 
                self.device, 
                self.model, 
                self.optimizer,
                self.scheduler
            )
            if self.loaded_checkpoint and "iteration" in self.loaded_checkpoint:
                self.start_iteration = self.loaded_checkpoint['iteration'] + 1

    def run(self):
        if self.wandb_flag:
            import wandb
            wandb.init(
                entity="chess_ai",
                project="chess_ai_app",
                name="reinforcement_training",
                config={k:v for k,v in self.__dict__.items() if not callable(v)},
                reinit=True
            )
            wandb.watch(self.model, log="parameters", log_freq=100)

        training_start = time.time()

        if self.start_iteration > self.num_iterations:
            raise ValueError(
                f"Checkpoint iteration ({self.start_iteration}) is higher than the set total iterations ({self.num_iterations})."
            )

        for iteration in range(self.start_iteration, self.num_iterations + 1):
            if self.status_callback:
                self.status_callback(f"🔁 Iteration {iteration}/{self.num_iterations} 🎮 Generating self-play data...")

            num_processes = min(self.num_selfplay_threads, cpu_count())
            games_per_process, remainder = divmod(self.num_games_per_iteration, num_processes)

            seeds = [
                self.random_seed + iteration * 1000 + i + int(time.time())
                for i in range(num_processes)
            ]
            model_state = {k: v.cpu() for k, v in self.model.state_dict().items()}
            tasks = []
            for i in range(num_processes):
                local_game_count = games_per_process + (1 if i < remainder else 0)
                tasks.append((
                    model_state,
                    self.device.type,
                    self.simulations_per_move,
                    self.c_puct,
                    self.temperature,
                    local_game_count,
                    seeds[i],
                ))

            with Pool(processes=num_processes) as pool:
                worker_results = pool.starmap(PlayAndCollectWorker.run_process, tasks)

            all_inputs, all_policy, all_value = [], [], []
            pgn_games = []
            stats = {
                'wins': 0,
                'losses': 0,
                'draws': 0,
                'game_lengths': [],
                'results': []
            }

            for res in worker_results:
                inp, pol, val, worker_stats, pgn = res
                all_inputs.extend(inp)
                all_policy.extend(pol)
                all_value.extend(val)
                pgn_games.extend(pgn)
                stats['wins'] += worker_stats['wins']
                stats['losses'] += worker_stats['losses']
                stats['draws'] += worker_stats['draws']
                stats['game_lengths'].extend(worker_stats['game_lengths'])
                stats['results'].extend(worker_stats['results'])

            stats['total_games'] = len(stats['results'])
            if stats['game_lengths']:
                stats['avg_game_length'] = float(np.mean(stats['game_lengths']))
            else:
                stats['avg_game_length'] = 0.0

            if self.wandb_flag:
                import wandb
                wandb.log({
                    "iteration": iteration,
                    "total_games": stats['total_games'],
                    "wins": stats['wins'],
                    "losses": stats['losses'],
                    "draws": stats['draws'],
                    "avg_game_length": stats['avg_game_length'],
                })
                if stats['results']:
                    wandb.log({"results_histogram": wandb.Histogram(stats['results'])})
                if stats['game_lengths']:
                    wandb.log({"game_length_histogram": wandb.Histogram(stats['game_lengths'])})

            current_metric = float('inf')
            if all_inputs:
                inputs_tensor = torch.from_numpy(np.array(all_inputs, dtype=np.float32))
                policy_tensor = torch.from_numpy(np.array(all_policy, dtype=np.float32))
                value_tensor = torch.tensor(all_value, dtype=torch.float32)

                data_loader = DataLoader(
                    TensorDataset(inputs_tensor, policy_tensor, value_tensor),
                    batch_size=self.batch_size,
                    shuffle=True,
                    pin_memory=(self.device.type=="cuda"),
                    num_workers=self.num_workers,
                    persistent_workers=(self.num_workers>0)
                )

                total_steps = (self.num_iterations - iteration + 1) * len(data_loader)
                if self.scheduler is None:
                    self.scheduler = initialize_scheduler(self.optimizer, self.scheduler_type, total_steps)
                    if self.loaded_checkpoint and 'scheduler_state_dict' in self.loaded_checkpoint:
                        self.scheduler.load_state_dict(self.loaded_checkpoint['scheduler_state_dict'])

                for ep in range(1, self.epochs_per_iteration + 1):
                    train_metrics = train_epoch(
                        model=self.model,
                        loader=data_loader,
                        device=self.device,
                        scaler=self.scaler,
                        optimizer=self.optimizer,
                        scheduler=self.scheduler,
                        epoch=ep,
                        max_epoch=self.epochs_per_iteration,
                        accum_steps=self.accumulation_steps,
                        compute_acc=False,
                        p_w=self.policy_weight,
                        v_w=self.value_weight,
                        max_grad=self.grad_clip,
                        prog_cb=self.progress_callback,
                        status_cb=self.status_callback,
                        use_wandb=self.wandb_flag
                    )

                current_metric = (
                    self.policy_weight * train_metrics["policy_loss"] +
                    self.value_weight * train_metrics["value_loss"]
                )

                if current_metric < self.best_metric:
                    self.best_metric = current_metric
                    self.best_iteration = iteration
                    self.checkpoint_manager.save(
                        self.model, self.optimizer, self.scheduler, iteration, None
                    )

            if self.checkpoint_interval > 0 and iteration % self.checkpoint_interval == 0:
                self.checkpoint_manager.save(
                    self.model, self.optimizer, self.scheduler, iteration
                )

            os.makedirs("data/games/self-play", exist_ok=True)
            for idx, game in enumerate(pgn_games, start=1):
                filename = f"data/games/self-play/game_{int(time.time())}_{idx}.pgn"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(str(game))

        self.checkpoint_manager.save(
            self.model,
            self.optimizer,
            self.scheduler,
            self.num_iterations,
            os.path.join("models", "saved_models", "reinforcement_model.pth")
        )

        if self.wandb_flag:
            import wandb
            wandb.run.summary.update({
                "best_metric": self.best_metric,
                "best_iteration": self.best_iteration,
                "training_time": time.time() - training_start
            })
            try:
                wandb.finish()
            except Exception as e:
                self.status_callback(f"⚠️ Error finishing wandb run: {e}")
        return True
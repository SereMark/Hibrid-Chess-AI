import os
import time
import json
import h5py
import wandb
import chess
import chess.pgn
import numpy as np
import concurrent.futures
from tqdm.auto import tqdm
from collections import defaultdict

from src.utils.config import Config
from src.utils.chess import BoardHistory, board_to_input, get_move_map

def create_position_dict():
    return {"win": 0, "draw": 0, "loss": 0, "eco": "", "name": ""}

def create_positions_dict():
    return defaultdict(create_position_dict)

class DataPipeline:
    def __init__(self, config: Config):
        self.config = config
        
        self.max_games = config.get('data.max_games', 10000)
        self.min_elo = config.get('data.min_elo', 1500)
        self.max_elo = config.get('data.max_elo', 3500)
        self.batch = config.get('data.batch', 1024)
        
        self.raw_pgn = config.get('data.raw_pgn', 'data/lichess_db_standard_rated_2025-03.pgn')
        
        self.augment_flip = config.get('data.augment_flip', True)
        
        self.generate_opening_book = config.get('data.generate_opening_book', True)
        self.opening_max_moves = config.get('data.opening_max_moves', 15)
        self.opening_min_games = config.get('data.opening_min_games', 10)
        self.opening_book = {}
        
        self.positions = defaultdict(create_positions_dict)
        self.move_map = get_move_map()
        
        self.output_dir = config.get('data.output_dir', '/content/drive/MyDrive/chess_ai/data')
        os.makedirs(self.output_dir, exist_ok=True)
        self.games_processed = 0
        self.dataset_size = 0
        self.elo_list = []
        self.game_lens = []
        self.time_controls = defaultdict(int)
        
    def setup(self):
        print("Setting up data pipeline...")
        
        if self.config.get('wandb.enabled', True):
            try:
                wandb.init(
                    project=self.config.get('wandb.project', 'chess_ai'),
                    name=f"data_{self.config.mode}_{time.strftime('%Y%m%d_%H%M%S')}",
                    config={
                        "mode": self.config.mode,
                        "max_games": self.max_games,
                        "min_elo": self.min_elo,
                        "max_elo": self.max_elo,
                        "batch": self.batch,
                        "augment_flip": self.augment_flip
                    }
                )
            except Exception as e:
                print(f"Error initializing wandb: {e}")
                
        try:
            local_raw_pgn = '/content/drive/MyDrive/chess_ai/data/lichess_db_standard_rated_2025-03.pgn'
            os.makedirs(os.path.dirname(local_raw_pgn), exist_ok=True)
            
            if os.path.exists(local_raw_pgn):
                self.raw_pgn = local_raw_pgn
                print(f"Using PGN file: {self.raw_pgn}")
            else:
                print(f"Using original PGN path: {self.raw_pgn}")
        except Exception as e:
            print(f"Error checking PGN file: {e}")
            
        os.makedirs(self.output_dir, exist_ok=True)
        return True
    
    def process_game(self, game):
        if "Variant" in game.headers or game.headers.get("WhiteTitle") == "BOT" or game.headers.get("BlackTitle") == "BOT":
            return [], [], [], {}, 0
            
        try:
            we = int(game.headers.get("WhiteElo", 0))
            be = int(game.headers.get("BlackElo", 0))
            if we < self.min_elo or be < self.min_elo or we > self.max_elo or be > self.max_elo:
                return [], [], [], {}, 0
        except (ValueError, TypeError):
            return [], [], [], {}, 0
            
        result_map = {"1-0": 1.0, "0-1": -1.0, "1/2-1/2": 0.0}
        result_str = game.headers.get("Result")
        gr = result_map.get(result_str)
        if gr is None:
            return [], [], [], {}, 0
            
        inputs, policies, values = [], [], []
        
        opening_positions = []
        board = game.board()
        node = game
        move_count = 0
        
        board_history = BoardHistory(max_history=7)
        board_history.add_board(board.copy())
        
        while node.variations:
            next_node = node.variation(0)
            move = next_node.move
            move_count += 1
            
            if move not in board.legal_moves:
                break
            
            if self.generate_opening_book and move_count <= self.opening_max_moves:
                opening_positions.append((board.fen(), move, board.turn))
                
            inp = board_to_input(board, board_history)
            
            mid = self.move_map.idx_by_move(move)
            if mid is None:
                board.push(move)
                board_history.add_board(board.copy())
                node = next_node
                continue
                
            final_value = gr if board.turn else -gr
            
            inputs.append(inp)
            policies.append(mid)
            values.append(final_value)
            
            if self.augment_flip:
                flipped_history = BoardHistory(max_history=7)
                
                for hist_board in board_history.get_history():
                    flipped_hist_board = hist_board.mirror()
                    flipped_history.add_board(flipped_hist_board)
                
                flipped_board = board.mirror()
                flipped_move = chess.Move(
                    chess.square_mirror(move.from_square),
                    chess.square_mirror(move.to_square),
                    promotion=move.promotion
                )
                
                flipped_id = self.move_map.idx_by_move(flipped_move)
                if flipped_id is not None:
                    flipped_input = board_to_input(flipped_board, flipped_history)
                    inputs.append(flipped_input)
                    policies.append(flipped_id)
                    values.append(-final_value)
            
            board.push(move)
            board_history.add_board(board.copy())
            node = next_node
        
        stats = {
            "tc": game.headers.get("TimeControl", ""),
            "elo": [we, be]
        }
        
        if self.generate_opening_book and opening_positions:
            try:
                result_for_white = "win" if result_str == "1-0" else "loss" if result_str == "0-1" else "draw"
                for fen, move, turn in opening_positions:
                    try:
                        move_uci = move.uci()
                    except Exception as e:
                        continue
                    
                    if fen not in self.opening_book:
                        self.opening_book[fen] = {}
                        
                    if move_uci not in self.opening_book[fen]:
                        self.opening_book[fen][move_uci] = {"win": 0, "draw": 0, "loss": 0, "eco": "", "name": ""}
                    
                    if turn:
                        result_key = result_for_white
                    else:
                        result_key = "win" if result_for_white == "loss" else "loss" if result_for_white == "win" else "draw"
                    
                    self.opening_book[fen][move_uci][result_key] += 1
                    
                    eco = game.headers.get("ECO", "")
                    opening_name = game.headers.get("Opening", "")
                    if eco and not self.opening_book[fen][move_uci]["eco"]:
                        self.opening_book[fen][move_uci]["eco"] = eco
                    if opening_name and not self.opening_book[fen][move_uci]["name"]:
                        self.opening_book[fen][move_uci]["name"] = opening_name
            except Exception as e:
                print(f"Warning: Error while processing opening book entry: {e}")
        
        return inputs, policies, values, stats, 1
    
    def process_batch(self, games):
        all_inputs, all_policies, all_values = [], [], []
        stats = {
            "processed": 0,
            "skipped": 0,
            "elo_list": [],
            "time_controls": defaultdict(int)
        }
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = list(executor.map(self.process_game, games))
            
        for inputs, policies, values, game_stats, processed in results:
            if processed:
                all_inputs.extend(inputs)
                all_policies.extend(policies)
                all_values.extend(values)
                stats["processed"] += 1
                stats["elo_list"].extend(game_stats.get("elo", []))
                if game_stats.get("tc"):
                    stats["time_controls"][game_stats["tc"]] += 1
            else:
                stats["skipped"] += 1
                
        return (all_inputs, all_policies, all_values), stats
    
    def write_batch(self, h5_file, batch_inputs, batch_policies, batch_values):
        batch_size = len(batch_inputs)
        if batch_size == 0:
            return
            
        end_index = self.dataset_size + batch_size
        
        try:
            i_np = np.array(batch_inputs, dtype=np.float32)
            p_np = np.array(batch_policies, dtype=np.int64)
            v_np = np.array(batch_values, dtype=np.float32)
            
            h5_file["inputs"].resize((end_index, 184, 8, 8))
            h5_file["policy_targets"].resize((end_index,))
            h5_file["value_targets"].resize((end_index,))
            
            h5_file["inputs"][self.dataset_size:end_index, :, :, :] = i_np
            h5_file["policy_targets"][self.dataset_size:end_index] = p_np
            h5_file["value_targets"][self.dataset_size:end_index] = v_np
            
            mean_val = float(np.mean(v_np))
            std_val = float(np.std(v_np))
            
            if wandb.run is not None:
                wandb.log({
                    "batch_size": batch_size,
                    "mean_value": mean_val,
                    "std_value": std_val,
                    "dataset_size": end_index
                })
                
            self.dataset_size = end_index
            
            return {
                "mean_val": mean_val,
                "std_val": std_val
            }
        except Exception as e:
            print(f"Error writing batch to h5: {e}")
            return None
    
    def create_splits(self, h5_path):
        with h5py.File(h5_path, "r") as hf:
            n = hf["inputs"].shape[0]
            if n == 0:
                return
                
            rng = np.random.RandomState(42)
            indices = rng.permutation(n)
            
            train_end = int(n * 0.8)
            val_end = int(n * 0.9)
            
            splits = {
                "train": indices[:train_end],
                "val": indices[train_end:val_end],
                "test": indices[val_end:]
            }
            
            for split, idx in splits.items():
                save_path = os.path.join(self.output_dir, f"{split}_indices.npy")
                np.save(save_path, idx)
                
            if wandb.run is not None:
                wandb.log({
                    "train_size": len(splits["train"]),
                    "val_size": len(splits["val"]),
                    "test_size": len(splits["test"])
                })
                
            print(f"Created dataset splits: train={len(splits['train'])}, "
                  f"val={len(splits['val'])}, test={len(splits['test'])}")
    
    def run(self):
        self.setup()
        start_time = time.time()
        
        h5_path = os.path.join(self.output_dir, "dataset.h5")
        with h5py.File(h5_path, "w") as h5_file:
            h5_file.create_dataset(
                "inputs", (0, 184, 8, 8),
                maxshape=(None, 184, 8, 8),
                dtype=np.float32,
                compression="gzip",
                compression_opts=1
            )
            
            h5_file.create_dataset(
                "policy_targets", (0,),
                maxshape=(None,),
                dtype=np.int64,
                compression="gzip",
                compression_opts=1
            )
            
            h5_file.create_dataset(
                "value_targets", (0,),
                maxshape=(None,),
                dtype=np.float32,
                compression="gzip",
                compression_opts=1
            )
            
            try:
                games_batch = []
                skipped_games = 0
                total_games_read = 0
                
                print(f"Processing up to {self.max_games} games from {self.raw_pgn}")
                
                with open(self.raw_pgn, "r", errors="ignore") as f:
                    pbar = tqdm(total=self.max_games, desc="Processing games")
                    
                    while self.games_processed < self.max_games:
                        game = chess.pgn.read_game(f)
                        if game is None:
                            break
                            
                        games_batch.append(game)
                        total_games_read += 1
                        
                        if len(games_batch) >= 50:
                            (batch_inputs, batch_policies, batch_values), stats = self.process_batch(games_batch)
                            
                            self.games_processed += stats["processed"]
                            skipped_games += stats["skipped"]
                            self.elo_list.extend(stats["elo_list"])
                            
                            for tc, count in stats["time_controls"].items():
                                self.time_controls[tc] += count
                            
                            if batch_inputs:
                                self.write_batch(
                                    h5_file, batch_inputs, batch_policies, batch_values
                                )
                                
                            pbar.update(stats["processed"])
                            pbar.set_postfix({
                                "processed": self.games_processed,
                                "skipped": skipped_games,
                                "positions": self.dataset_size
                            })
                            
                            if wandb.run is not None:
                                wandb.log({
                                    "games_processed": self.games_processed,
                                    "games_skipped": skipped_games,
                                    "total_positions": self.dataset_size,
                                    "progress": min(int((self.games_processed / self.max_games) * 100), 100)
                                })
                            
                            games_batch = []
                    
                    if games_batch:
                        (batch_inputs, batch_policies, batch_values), stats = self.process_batch(games_batch)
                        
                        self.games_processed += stats["processed"]
                        skipped_games += stats["skipped"]
                        self.elo_list.extend(stats["elo_list"])
                        
                        for tc, count in stats["time_controls"].items():
                            self.time_controls[tc] += count
                        
                        if batch_inputs:
                            self.write_batch(
                                h5_file, batch_inputs, batch_policies, batch_values
                            )
                        
                        pbar.update(stats["processed"])
                        pbar.set_postfix({
                            "processed": self.games_processed,
                            "skipped": skipped_games,
                            "positions": self.dataset_size
                        })
                
                print(f"Processed {self.games_processed} games, "
                      f"skipped {skipped_games} games, "
                      f"created {self.dataset_size} positions")
                
            except Exception as e:
                print(f"Error processing games: {e}")
                return False
            
            self.create_splits(h5_path)
        
        processing_time = time.time() - start_time
        print(f"Data preparation completed in {processing_time:.2f}s")
        
        if wandb.run is not None:
            if self.elo_list:
                try:
                    elo_table = wandb.Table(data=[[e] for e in self.elo_list], columns=["ELO"])
                    wandb.log({"ELO Distribution": wandb.plot.histogram(elo_table, "ELO")})
                except Exception as e:
                    print(f"Error logging ELO distribution: {e}")
            
            if self.time_controls:
                try:
                    tc_table = wandb.Table(columns=["TimeControl", "Count"])
                    for k, v in self.time_controls.items():
                        tc_table.add_data(k, v)
                    wandb.log({"Time Controls": wandb.plot.bar(tc_table, "TimeControl", "Count")})
                except Exception as e:
                    print(f"Error logging time control stats: {e}")
            
            try:
                artifact = wandb.Artifact("chess_dataset", type="dataset")
                artifact.add_file(os.path.join(self.output_dir, "dataset.h5"))
                wandb.log_artifact(artifact)
            except Exception as e:
                print(f"Error logging dataset artifact: {e}")
                
            wandb.finish()
        
        print(f"Dataset and indices saved to: {self.output_dir}")
        
        if self.generate_opening_book and self.opening_book:
            try:
                print(f"\nGenerating opening book from {len(self.opening_book)} positions...")
                
                filtered_book = {}
                total_moves = 0
                
                for fen, moves in self.opening_book.items():
                    filtered_moves = {}
                    
                    for move_uci, stats in moves.items():
                        total_games = stats["win"] + stats["draw"] + stats["loss"]
                        if total_games >= self.opening_min_games:
                            filtered_moves[move_uci] = stats
                            total_moves += 1
                    
                    if filtered_moves:
                        filtered_book[fen] = filtered_moves
                
                opening_book_path = os.path.join(self.output_dir, "opening_book.json")
                with open(opening_book_path, 'w') as f:
                    json.dump(filtered_book, f, indent=2)
                
                print(f"Opening book generated with {len(filtered_book)} positions and {total_moves} moves")
                print(f"Opening book saved to: {opening_book_path}")
                
                if wandb.run is not None:
                    try:
                        wandb.log({
                            "opening_book/positions": len(filtered_book),
                            "opening_book/total_moves": total_moves
                        })
                        
                        sample_size = min(20, len(filtered_book))
                        popular_positions = []
                        
                        for fen, moves in filtered_book.items():
                            total_games = 0
                            for move_uci, stats in moves.items():
                                if isinstance(stats, dict):
                                    total_games += stats.get("win", 0) + stats.get("draw", 0) + stats.get("loss", 0)
                            popular_positions.append((fen, total_games))
                        
                        popular_positions = sorted(popular_positions, key=lambda x: x[1], reverse=True)[:sample_size]
                        
                        popular_table = wandb.Table(columns=["Position", "FEN", "Total Games"])
                        for i, (fen, count) in enumerate(popular_positions):
                            popular_table.add_data(f"Position {i+1}", fen, count)
                        
                        wandb.log({"opening_book/popular_positions": popular_table})
                        
                        artifact = wandb.Artifact("opening_book", type="dataset")
                        artifact.add_file(opening_book_path)
                        wandb.log_artifact(artifact)
                        
                    except Exception as e:
                        print(f"Error logging opening book statistics: {e}")
                
                return True
            except Exception as e:
                print(f"Error generating opening book: {e}")
                return False
        
        return True
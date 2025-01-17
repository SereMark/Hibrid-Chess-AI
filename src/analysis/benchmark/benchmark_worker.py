from PyQt5.QtCore import pyqtSignal
import os
import time
import chess
import chess.pgn
import json
import torch
from typing import Dict, Optional, Tuple
from src.base.base_worker import BaseWorker
from src.training.reinforcement.mcts import MCTS
from src.utils.chess_utils import get_total_moves, convert_board_to_tensor, get_move_mapping
from src.utils.common_utils import wait_if_paused, update_progress_time_left, get_game_result
from src.models.model import ChessModel

class Bot:
    def __init__(self, path: str, use_mcts: bool, use_opening_book: bool, logger):
        self.path = path
        self.use_mcts = use_mcts
        self.use_opening_book = use_opening_book
        self.logger = logger
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self._load_model()
        self.mcts: Optional[MCTS] = None
        self.initialize_mcts()

    def _load_model(self) -> Optional[ChessModel]:
        if not os.path.exists(self.path):
            self.logger.error(f"Model path does not exist: {self.path}")
            return None

        try:
            model = ChessModel(get_total_moves()).to(self.device)
            checkpoint = torch.load(self.path, map_location=self.device)
            model.load_state_dict(checkpoint["model_state_dict"])
            model.eval()
            self.logger.info(f"Loaded model from {self.path}")
            return model
        except Exception as e:
            self.logger.error(f"Failed to load model from {self.path}: {e}")
            return None

    def initialize_mcts(self, simulations: int = 100, exploration: float = 1.4):
        if self.use_mcts and self.model:
            self.mcts = MCTS(model=self.model, device=torch.device(self.device), c_puct=exploration, n_simulations=simulations)
            self.logger.info("Initialized MCTS for bot.")

    def _get_board_action_probs(self, board: chess.Board) -> Dict[chess.Move, float]:
        if not self.model:
            return {}

        board_tensor = convert_board_to_tensor(board)
        board_tensor = torch.from_numpy(board_tensor).float().unsqueeze(0).to(self.device)

        with torch.no_grad():
            policy_logits, _ = self.model(board_tensor)
            policy_logits = policy_logits[0]  # Remove batch dimension

        # Convert logits to probabilities
        policy = torch.softmax(policy_logits, dim=0).cpu().numpy()

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return {}

        action_probs = {}
        total_prob = 0.0
        move_mapping = get_move_mapping()

        for move in legal_moves:
            idx = move_mapping.get_index_by_move(move)
            if idx is not None and idx < len(policy):
                prob = max(policy[idx], 1e-8)
                action_probs[move] = prob
                total_prob += prob
            else:
                action_probs[move] = 1e-8
                total_prob += 1e-8

        # Normalize probabilities
        if total_prob > 0:
            for mv in action_probs:
                action_probs[mv] /= total_prob
        else:
            # Fallback to uniform if total_prob is zero
            uniform_prob = 1.0 / len(legal_moves)
            for mv in action_probs:
                action_probs[mv] = uniform_prob

        return action_probs

    def get_move_pth(self, board: chess.Board) -> chess.Move:
        if not self.model:
            self.logger.warning("Model not loaded. Returning null move.")
            return chess.Move.null()

        try:
            legal_moves = list(board.legal_moves)
            if not legal_moves:
                self.logger.warning("No legal moves available.")
                return chess.Move.null()

            action_probs = self._get_board_action_probs(board)
            if not action_probs:
                self.logger.warning("No valid moves found from the NN inference.")
                return chess.Move.null()

            # Pick move with the highest probability
            best_move = max(action_probs, key=action_probs.get)
            return best_move

        except Exception as e:
            self.logger.error(f"Error determining move with .pth model: {e}")
            return chess.Move.null()

    def get_move_mcts(self, board: chess.Board, time_per_move: float) -> chess.Move:
        if not self.mcts:
            self.logger.warning("MCTS not initialized. Returning null move.")
            return chess.Move.null()

        self.mcts.set_root_node(board.copy())

        start_time = time.time()
        while (time.time() - start_time) < time_per_move:
            if board.is_game_over():
                break
            self.mcts.simulate()

        move_probs = self.mcts.get_move_probs(temperature=1e-3)
        if not move_probs:
            self.logger.warning("No move probabilities available from MCTS.")
            return chess.Move.null()

        # Select the move with the highest probability
        best_move = max(move_probs, key=move_probs.get)
        return best_move

    def get_move(self, board: chess.Board, time_per_move: float, opening_book: Dict[str, Dict[str, Dict[str, int]]]) -> chess.Move:
        # Use opening book and MCTS if both are enabled
        if self.use_mcts and self.use_opening_book:
            move = self.get_opening_book_move(board, opening_book)
            if move != chess.Move.null():
                return move
            return self.get_move_mcts(board, time_per_move)

        # Use only MCTS
        if self.use_mcts:
            return self.get_move_mcts(board, time_per_move)

        # Use only opening book
        if self.use_opening_book:
            return self.get_opening_book_move(board, opening_book)

        # Fallback to model-based move
        return self.get_move_pth(board)

    def get_opening_book_move(self, board: chess.Board, opening_book: Dict[str, Dict[str, Dict[str, int]]]) -> chess.Move:
        if not opening_book:
            return chess.Move.null()

        fen = board.fen()
        moves_data = opening_book.get(fen, {})
        best_move: Optional[chess.Move] = None
        best_score = -1.0

        # Iterate through possible moves from the opening book
        for uci_move, stats in moves_data.items():
            if not isinstance(stats, dict):
                self.logger.error(f"Invalid stats for move {uci_move}: {stats}")
                continue

            total = stats.get("win", 0) + stats.get("draw", 0) + stats.get("loss", 0)
            if total == 0:
                continue

            # Calculate score based on wins and draws
            score = (stats.get("win", 0) + 0.5 * stats.get("draw", 0)) / total
            try:
                move_candidate = chess.Move.from_uci(uci_move)
                if move_candidate in board.legal_moves and score > best_score:
                    best_score = score
                    best_move = move_candidate
            except ValueError as e:
                self.logger.error(f"Error parsing move {uci_move}: {e}")

        # Return the best move found or a null move if none
        return best_move if best_move else chess.Move.null()

class BenchmarkWorker(BaseWorker):
    benchmark_update = pyqtSignal(dict)

    def __init__(self, bot1_path: str, bot2_path: str, num_games: int, time_per_move: float, bot1_use_mcts: bool, bot1_use_opening_book: bool, bot2_use_mcts: bool, bot2_use_opening_book: bool):
        super().__init__()
        self.num_games = num_games
        self.time_per_move = time_per_move
        self.default_mcts_simulations = 100

        # Initialize bots with their respective configurations
        self.bot1 = Bot(path=bot1_path, use_mcts=bot1_use_mcts, use_opening_book=bot1_use_opening_book, logger=self.logger)
        self.bot2 = Bot(path=bot2_path, use_mcts=bot2_use_mcts, use_opening_book=bot2_use_opening_book, logger=self.logger)

        # Load the opening book
        self.opening_book = self._load_opening_book()

        # Ensure the benchmark games directory exists
        self.games_dir = os.path.join("data", "games", "benchmark")
        os.makedirs(self.games_dir, exist_ok=True)

    def _load_opening_book(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        path = os.path.join("data", "processed", "opening_book.json")
        if not os.path.exists(path):
            self.logger.warning(f"Opening book not found at {path}. Continuing without it.")
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                opening_book = json.load(f)
            self.logger.info("Loaded opening book.")
            return opening_book
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode opening book JSON: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"Unexpected error loading opening book: {e}")
            return {}

    def run_task(self):
        # Validate that both bots are properly initialized
        if not self._validate_bots():
            return

        start_time = time.time()
        results = []

        # Iterate through the number of games to be played
        for game_idx in range(1, self.num_games + 1):
            if self._is_stopped.is_set():
                self.logger.info("Benchmarking stopped by user.")
                return

            # Handle pause functionality
            wait_if_paused(self._is_paused)
            board = chess.Board()

            self.logger.info(f"Starting game {game_idx} of {self.num_games}...")
            
            # Play the game and get the result, move count, and PGN game
            game_result, moves_count, pgn_game = self._play_single_game(board)

            # Save the PGN game to a file
            pgn_filename = os.path.join(self.games_dir, f"game_{game_idx}.pgn")
            try:
                with open(pgn_filename, "w", encoding="utf-8") as pgn_file:
                    pgn_file.write(str(pgn_game))
                self.logger.info(f"Saved game {game_idx} to {pgn_filename}")
            except Exception as e:
                self.logger.error(f"Failed to save PGN for game {game_idx}: {e}")

            # Determine the winner based on the game result
            winner = self._determine_winner(game_result)
            results.append({"game_index": game_idx, "winner": winner, "moves": moves_count})

            self.logger.info(f"Game {game_idx} completed in {moves_count} moves. Winner: {winner}")
            # Update progress and estimated time left
            update_progress_time_left(self.progress_update, self.time_left_update, start_time, game_idx, self.num_games)

        # Aggregate and emit the final statistics
        final_stats = self._aggregate_results(results)
        self.benchmark_update.emit(final_stats)
        self.logger.info("Benchmarking completed.")

    def _validate_bots(self) -> bool:
        bot1_valid = self.bot1.model or self.bot1.use_opening_book or self.bot1.use_mcts
        bot2_valid = self.bot2.model or self.bot2.use_opening_book or self.bot2.use_mcts

        if not bot1_valid:
            self.logger.error("Bot1 is not properly configured.")
            return False
        if not bot2_valid:
            self.logger.error("Bot2 is not properly configured.")
            return False
        return True

    def _determine_winner(self, game_result: int) -> str:
        if game_result > 0:
            return "Bot1"
        elif game_result < 0:
            return "Bot2"
        return "Draw"

    def _aggregate_results(self, results: list) -> Dict[str, int]:
        return {
            "bot1_wins": sum(r["winner"] == "Bot1" for r in results),
            "bot2_wins": sum(r["winner"] == "Bot2" for r in results),
            "draws": sum(r["winner"] == "Draw" for r in results),
            "total_games": self.num_games,
        }

    def _play_single_game(self, board: chess.Board) -> Tuple[int, int, chess.pgn.Game]:
        moves_count = 0
        game = chess.pgn.Game()
        game.headers["Event"] = "Bot Benchmarking"
        game.headers["Site"] = "Local"
        game.headers["Date"] = time.strftime("%Y.%m.%d")
        game.headers["Round"] = "-"
        game.headers["White"] = "Bot1"
        game.headers["Black"] = "Bot2"
        game.headers["Result"] = "*"

        node = game

        while not board.is_game_over() and not self._is_stopped.is_set():
            # Handle pause functionality
            wait_if_paused(self._is_paused)
            # Determine which bot's turn it is
            current_bot = self.bot1 if board.turn == chess.WHITE else self.bot2
            # Get the move from the current bot
            move = current_bot.get_move(board, self.time_per_move, self.opening_book)
            if move == chess.Move.null():
                self.logger.warning(f"{'Bot1' if board.turn == chess.WHITE else 'Bot2'} returned a null move.")
                break
            board.push(move)
            node = node.add_variation(move)
            moves_count += 1

        # Get the game result
        result = get_game_result(board)
        if result > 0:
            game.headers["Result"] = "1-0"
        elif result < 0:
            game.headers["Result"] = "0-1"
        else:
            game.headers["Result"] = "1/2-1/2"

        return result, moves_count, game
# src\gui\tabs\game_tab.py

import os, chess
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QPushButton,
    QDialog, QGridLayout, QSizePolicy, QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QRectF
from PyQt5.QtGui import QPainter, QPixmap, QBrush, QColor, QFont
from PyQt5.QtSvg import QSvgRenderer
from src.gui.visualizations.game_visualization import GameVisualization
from src.scripts.chess_engine import ChessEngine


class ChessBoardView(QWidget):
    move_made_signal = pyqtSignal(str)
    status_message = pyqtSignal(str)
    promotion_requested = pyqtSignal(int, int)

    def __init__(self, engine: ChessEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.selected_square = None
        self.is_orientation_flipped = False
        self.highlighted_squares = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._setup_svg_renderer()
        self._connect_signals()
        self.policy_output = {}
        self.square_probs = {}

    def _setup_svg_renderer(self):
        svg_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'chess_pieces.svg')
        svg_path = os.path.abspath(svg_path)
        self.svg_renderer = QSvgRenderer(svg_path)
        if not self.svg_renderer.isValid():
            raise ValueError("Invalid SVG file for chess pieces.")

    def _connect_signals(self):
        self.engine.move_made_signal.connect(self.on_move_made)
        self.engine.game_over_signal.connect(self.on_game_over)
        self.engine.policy_output_signal.connect(self.update_policy_output)

    def update_policy_output(self, policy_output):
        self.policy_output = policy_output
        self.square_probs = {}
        for move_uci, prob in policy_output.items():
            move = chess.Move.from_uci(move_uci)
            to_sq = move.to_square
            self.square_probs[to_sq] = self.square_probs.get(to_sq, 0) + prob
        self.update()

    def on_move_made(self, msg):
        self.move_made_signal.emit(msg)
        self.selected_square = None
        self.highlighted_squares = []
        self.update()

    def on_game_over(self, msg):
        self.status_message.emit(msg)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        size = min(self.width(), self.height()) / 8
        painter.setRenderHint(QPainter.Antialiasing)
        if self.is_orientation_flipped:
            painter.translate(self.width(), self.height())
            painter.rotate(180)
        self._draw_board(painter, size)
        self._draw_pieces_and_highlights(painter, size)

    def _draw_board(self, painter, size):
        for r in range(8):
            for c in range(8):
                color = QColor('#F0D9B5') if (r + c) % 2 == 0 else QColor('#B58863')
                painter.fillRect(int(c * size), int(r * size), int(size), int(size), color)

    def _get_square_coordinates(self, square, size):
        col = chess.square_file(square)
        row = chess.square_rank(square)
        if self.is_orientation_flipped:
            col = 7 - col
        else:
            row = 7 - row
        x = col * size
        y = row * size
        return x, y

    def _draw_pieces_and_highlights(self, painter, size):
        if self.square_probs:
            max_prob = max(self.square_probs.values())
            for square, prob in self.square_probs.items():
                intensity = prob / max_prob if max_prob > 0 else 0
                color = QColor(255, 0, 0, int(255 * intensity * 0.3))
                x, y = self._get_square_coordinates(square, size)
                painter.fillRect(int(x), int(y), int(size), int(size), color)
        for square in chess.SQUARES:
            piece = self.engine.board.piece_at(square)
            if piece:
                x, y = self._get_square_coordinates(square, size)
                self._draw_piece(painter, piece, x, y, size)
        for square in self.highlighted_squares:
            x, y = self._get_square_coordinates(square, size)
            self._draw_highlight(painter, x, y, size)

    def _draw_piece(self, painter, piece, x, y, size):
        pad = size * 0.15
        ps = size * 0.7
        pixmap = QPixmap(int(ps), int(ps))
        pixmap.fill(Qt.transparent)
        svg_painter = QPainter(pixmap)
        piece_name = {
            chess.PAWN: 'pawn',
            chess.KNIGHT: 'knight',
            chess.BISHOP: 'bishop',
            chess.ROOK: 'rook',
            chess.QUEEN: 'queen',
            chess.KING: 'king'
        }
        piece_group = f"{piece_name[piece.piece_type]}_{'white' if piece.color == chess.WHITE else 'black'}"
        self.svg_renderer.render(svg_painter, piece_group, QRectF(0, 0, ps, ps))
        svg_painter.end()
        painter.drawPixmap(int(x + pad), int(y + pad), pixmap)

    def _draw_highlight(self, painter, x, y, size):
        r = size / 8
        painter.setBrush(QBrush(QColor(255, 255, 0, 180), Qt.SolidPattern))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            int(x + size / 2 - r),
            int(y + size / 2 - r),
            int(2 * r), int(2 * r)
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            size = min(self.width(), self.height()) / 8
            square = self._get_square(int(event.y() // size), int(event.x() // size))
            self.on_square_clicked(square)

    def on_square_clicked(self, sq):
        if self.engine.is_game_over:
            self.status_message.emit("The game is over.")
            return

        piece = self.engine.board.piece_at(sq)
        if self.selected_square is None:
            if piece and piece.color == self.engine.board.turn:
                self.selected_square = sq
                self.highlighted_squares = [
                    m.to_square for m in self.engine.board.legal_moves if m.from_square == sq
                ]
            else:
                self.status_message.emit("Select a piece to move.")
        else:
            if sq == self.selected_square:
                self.selected_square = None
                self.highlighted_squares = []
            elif piece and piece.color == self.engine.board.turn:
                self.selected_square = sq
                self.highlighted_squares = [
                    m.to_square for m in self.engine.board.legal_moves if m.from_square == sq
                ]
            else:
                moving_piece = self.engine.board.piece_at(self.selected_square)
                is_pawn_promotion_move = (
                    moving_piece.piece_type == chess.PAWN and
                    chess.square_rank(sq) in [0, 7]
                )
                if is_pawn_promotion_move:
                    self.promotion_requested.emit(self.selected_square, sq)
                    self.selected_square = None
                    self.highlighted_squares = []
                    self.update()
                    return
                else:
                    move = chess.Move(self.selected_square, sq)
                    if move in self.engine.board.legal_moves:
                        if not self.engine.make_move(self.selected_square, sq):
                            self.status_message.emit("Invalid Move.")
                    else:
                        self.status_message.emit("Invalid Move.")
                    self.selected_square = None
                    self.highlighted_squares = []
        self.update()

    def _get_square(self, row, col):
        return chess.square(
            col, 7 - row if not self.is_orientation_flipped else row
        )

    def reset_view(self):
        self.selected_square = None
        self.highlighted_squares = []
        self.update()


class ChessGameTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.time_limit = self.white_timer = self.black_timer = 600
        self.opponent_type = 'random'
        self.engine = ChessEngine(player_color=chess.WHITE, opponent_type=self.opponent_type)
        self.board_view = ChessBoardView(self.engine, self)
        self.visual = GameVisualization()
        self._setup_ui()
        self._connect_board_view_signals()
        self._connect_signals()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.decrement_timer)
        self.timer.start(1000)

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        timers_layout = self._create_timer_layout()
        self.status = QLabel("", alignment=Qt.AlignCenter)
        self.ai_selection = QComboBox()
        self.ai_selection.addItem("Random Move Opponent", userData='random')
        self.ai_selection.addItem("CNN AI Opponent", userData='cnn')
        self.ai_selection_label = QLabel("Select Opponent:")
        ai_selection_layout = QHBoxLayout()
        ai_selection_layout.addWidget(self.ai_selection_label)
        ai_selection_layout.addWidget(self.ai_selection)
        left_layout = QVBoxLayout()
        left_layout.addLayout(timers_layout)
        left_layout.addWidget(self.board_view)
        left_layout.addWidget(self.status)
        left_layout.addLayout(ai_selection_layout)
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.visual)
        main_layout.addWidget(splitter)

    def _create_timer_layout(self):
        font_bold = QFont('Arial', 14, QFont.Bold)
        self.white_label = QLabel(self._get_timer_text(chess.WHITE), alignment=Qt.AlignCenter)
        self.black_label = QLabel(self._get_timer_text(chess.BLACK), alignment=Qt.AlignCenter)
        self.white_label.setFont(font_bold)
        self.black_label.setFont(font_bold)
        timers_layout = QHBoxLayout()
        timers_layout.addWidget(self.white_label)
        timers_layout.addWidget(self.black_label)
        return timers_layout

    def _connect_board_view_signals(self):
        self.board_view.move_made_signal.connect(self.refresh_status)
        self.board_view.status_message.connect(self.status.setText)
        self.board_view.promotion_requested.connect(self.handle_promotion)

    def _connect_signals(self):
        self.engine.game_over_signal.connect(self.handle_game_over)
        self.engine.value_evaluation_signal.connect(self.visual.update_value_evaluation)
        self.engine.material_balance_signal.connect(self.visual.update_material_balance)
        self.engine.move_made_signal.connect(self.status.setText)
        self.engine.policy_output_signal.connect(self.board_view.update_policy_output)
        self.ai_selection.currentIndexChanged.connect(self.change_opponent_type)

    def refresh_status(self, msg):
        if "Move made:" in msg or "AI moved:" in msg:
            self.refresh_labels()
        elif "Game over" in msg:
            self.timer.stop()
        self.status.setText(msg)

    def decrement_timer(self):
        if self.engine.is_game_over:
            self.timer.stop()
            return
        current_timer = self.white_timer if self.engine.board.turn == chess.WHITE else self.black_timer
        current_timer = max(0, current_timer - 1)
        if current_timer == 0:
            self.engine.is_game_over = True
            winner = "Black" if self.engine.board.turn == chess.WHITE else "White"
            self.engine.move_made_signal.emit(f"Game over: {winner} wins on time!")
        if self.engine.board.turn == chess.WHITE:
            self.white_timer = current_timer
        else:
            self.black_timer = current_timer
        self.refresh_labels()

    def refresh_labels(self):
        self.white_label.setText(self._get_timer_text(chess.WHITE))
        self.black_label.setText(self._get_timer_text(chess.BLACK))

    def _get_timer_text(self, color):
        timer = self.white_timer if color == chess.WHITE else self.black_timer
        minutes, seconds = divmod(timer, 60)
        return f"{'White' if color == chess.WHITE else 'Black'}: {minutes:02d}:{seconds:02d}"

    def handle_game_over(self, msg):
        self.status.setText(msg)
        self.timer.stop()

    def handle_promotion(self, from_sq, to_sq):
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Promotion Piece")
        layout = QGridLayout(dialog)
        selected_piece = {'piece': None}
        for i, piece in enumerate(['Queen', 'Rook', 'Bishop', 'Knight']):
            btn = QPushButton(piece)
            btn.clicked.connect(
                lambda _, p=piece: self._handle_promotion_selection(p, dialog, selected_piece)
            )
            layout.addWidget(btn, 0, i)
        dialog.setLayout(layout)
        dialog.exec_()
        if selected_piece['piece'] and self.engine.make_move(from_sq, to_sq, promotion=selected_piece['piece']):
            self.board_view.highlighted_squares = []
            self.board_view.selected_square = None
            self.board_view.update()
        else:
            self.status.setText("Invalid Move.")

    def _handle_promotion_selection(self, piece, dialog, selected_piece):
        selected_piece['piece'] = piece
        dialog.accept()

    def change_opponent_type(self):
        new_opponent_type = self.ai_selection.currentData()
        if new_opponent_type != self.opponent_type:
            reply = QMessageBox.question(self, 'Restart Game',
                                         "Changing opponent will restart the game. Do you want to continue?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.opponent_type = new_opponent_type
                self.restart_game()
            else:
                index = self.ai_selection.findData(self.opponent_type)
                self.ai_selection.setCurrentIndex(index)

    def restart_game(self):
        self.engine.move_made_signal.disconnect()
        self.engine.game_over_signal.disconnect()
        self.engine.value_evaluation_signal.disconnect()
        self.engine.material_balance_signal.disconnect()
        self.engine.policy_output_signal.disconnect()
        self.ai_selection.currentIndexChanged.disconnect()
        self.engine = ChessEngine(player_color=chess.WHITE, opponent_type=self.opponent_type)
        self.board_view.engine = self.engine
        self.visual.reset_visualizations()
        self.white_timer = self.black_timer = self.time_limit
        self.engine.is_game_over = False
        self.timer.start(1000)
        self._connect_signals()
        self.refresh_labels()
        self.status.setText("Game restarted.")
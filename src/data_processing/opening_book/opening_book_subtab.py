from PyQt5.QtWidgets import QVBoxLayout, QLabel, QGridLayout, QLineEdit, QPushButton, QGroupBox, QMessageBox
from src.data_processing.opening_book.opening_book_worker import OpeningBookWorker
from src.data_processing.opening_book.opening_book_visualization import OpeningBookVisualization
from src.base.base_tab import BaseTab
import os

class OpeningBookSubTab(BaseTab):
    def __init__(self, parent=None, processed_data_dir_default="data/processed"):
        super().__init__(parent)
        self.visualization = OpeningBookVisualization()
        self.processed_data_dir_default = processed_data_dir_default
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        self.setup_subtab(
            main_layout,
            "Generate an Opening Book from processed PGN files.",
            "Opening Book Generation Progress",
            "Opening Book Generation Logs",
            "Opening Book Visualization",
            self.visualization,
            {
                "start_text": "Start Opening Book Generation",
                "stop_text": "Stop",
                "start_callback": self.start_process,
                "stop_callback": self.stop_process,
                "pause_text": "Pause",
                "resume_text": "Resume",
                "pause_callback": self.pause_worker,
                "resume_callback": self.resume_worker,
                "start_new_callback": self.reset_to_initial_state
            },
            "Start New"
        )
        self.opening_book_group = self.create_opening_book_group()
        self.layout().insertWidget(1, self.opening_book_group)
        self.stop_button.setEnabled(False)
        if self.pause_button:
            self.pause_button.setEnabled(False)
        if self.resume_button:
            self.resume_button.setEnabled(False)

    def create_opening_book_group(self):
        group = QGroupBox("Opening Book Configuration")
        layout = QGridLayout()
        label1 = QLabel("PGN File:")
        self.pgn_file_input = QLineEdit("data/raw/lichess_db_standard_rated_2024-12.pgn")
        pgn_browse_button = QPushButton("Browse")
        pgn_browse_button.clicked.connect(lambda: self.browse_file(self.pgn_file_input, "Select PGN File", "PGN Files (*.pgn)"))
        label2 = QLabel("Max Games:")
        self.ob_max_games_input = QLineEdit("100000")
        label3 = QLabel("Minimum ELO:")
        self.ob_min_elo_input = QLineEdit("1500")
        label4 = QLabel("Max Opening Moves:")
        self.ob_max_opening_moves_input = QLineEdit("20")
        label5 = QLabel("Processed Data Directory:")
        self.ob_processed_data_dir_input = QLineEdit(self.processed_data_dir_default)
        ob_processed_browse_button = QPushButton("Browse")
        ob_processed_browse_button.clicked.connect(lambda: self.browse_dir(self.ob_processed_data_dir_input, "Select Processed Data Directory"))
        layout.addWidget(label1, 0, 0)
        layout.addLayout(self.create_browse_layout(self.pgn_file_input, pgn_browse_button), 0, 1, 1, 3)
        layout.addWidget(label2, 1, 0)
        layout.addWidget(self.ob_max_games_input, 1, 1)
        layout.addWidget(label3, 1, 2)
        layout.addWidget(self.ob_min_elo_input, 1, 3)
        layout.addWidget(label4, 2, 0)
        layout.addWidget(self.ob_max_opening_moves_input, 2, 1)
        layout.addWidget(label5, 3, 0)
        layout.addLayout(self.create_browse_layout(self.ob_processed_data_dir_input, ob_processed_browse_button), 3, 1, 1, 3)
        group.setLayout(layout)
        return group

    def start_process(self):
        try:
            pgn_file_path = self.pgn_file_input.text()
            max_games = int(self.ob_max_games_input.text())
            min_elo = int(self.ob_min_elo_input.text())
            max_opening_moves = int(self.ob_max_opening_moves_input.text())
            if not os.path.exists(pgn_file_path):
                QMessageBox.warning(self, "Input Error", "The specified PGN file does not exist.")
                return
            if max_games <= 0 or min_elo <= 0 or max_opening_moves <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please ensure all inputs are valid positive integers.")
            return
        processed_data_dir = self.ob_processed_data_dir_input.text()
        if not os.path.exists(processed_data_dir):
            try:
                os.makedirs(processed_data_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Directory Error", str(e))
                return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        if self.pause_button:
            self.pause_button.setEnabled(True)
        if self.resume_button:
            self.resume_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")
        self.log_text_edit.clear()
        self.visualization.reset_visualization()
        self.opening_book_group.setVisible(False)
        self.progress_group.setVisible(True)
        self.control_group.setVisible(True)
        self.log_group.setVisible(True)
        if self.visualization_group:
            self.visualization_group.setVisible(False)
        if self.show_logs_button:
            self.show_logs_button.setVisible(True)
        if self.show_graphs_button:
            self.show_graphs_button.setVisible(True)
        if self.start_new_button:
            self.start_new_button.setVisible(False)
        self.init_ui_state = False
        started = self.start_worker(OpeningBookWorker, pgn_file_path, max_games, min_elo, max_opening_moves, processed_data_dir)
        if started:
            self.worker.positions_update.connect(self.visualization.update_opening_book)
        else:
            self.reset_to_initial_state()

    def stop_process(self):
        self.stop_worker()
        self.reset_to_initial_state()

    def reset_to_initial_state(self):
        self.opening_book_group.setVisible(True)
        self.progress_group.setVisible(False)
        self.log_group.setVisible(False)
        if self.visualization_group:
            self.visualization_group.setVisible(False)
        if self.start_new_button:
            self.start_new_button.setVisible(False)
        if self.show_logs_button:
            self.show_logs_button.setVisible(False)
        if self.show_graphs_button:
            self.show_graphs_button.setVisible(False)
        if self.show_logs_button:
            self.show_logs_button.setChecked(True)
        if self.show_graphs_button:
            self.show_graphs_button.setChecked(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.remaining_time_label.setText("Time Left: N/A")
        self.log_text_edit.clear()
        self.visualization.reset_visualization()
        if self.start_button:
            self.start_button.setEnabled(True)
        if self.stop_button:
            self.stop_button.setEnabled(False)
        if self.pause_button:
            self.pause_button.setEnabled(False)
        if self.resume_button:
            self.resume_button.setEnabled(False)
        self.init_ui_state = True
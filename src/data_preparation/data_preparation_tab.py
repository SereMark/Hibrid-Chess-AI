from PyQt5.QtWidgets import QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QPushButton, QMessageBox, QWidget, QTabWidget
from src.data_preparation.data_preparation_visualization import DataPreparationVisualization
from src.data_preparation.data_preparation_worker import DataPreparationWorker
from src.data_preparation.opening_book_worker import OpeningBookWorker
from src.data_preparation.opening_book_visualization import OpeningBookVisualization
from src.base.base_tab import BaseTab
import os

class DataProcessingTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.visualization = DataPreparationVisualization()

        self.parameters_group = self.create_parameters_group()
        self.directories_group = self.create_directories_group()
        control_buttons_layout = self.create_control_buttons(
            "Start Data Preparation",
            "Stop",
            self.start_data_preparation,
            self.stop_data_preparation,
            pause_text="Pause",
            resume_text="Resume",
            pause_callback=self.pause_worker,
            resume_callback=self.resume_worker
        )
        progress_layout = self.create_progress_layout()
        self.log_text_edit = self.create_log_text_edit()
        self.visualization_group = self.create_visualization_group(self.visualization, "Data Preparation Visualization")

        main_layout.addWidget(self.parameters_group)
        main_layout.addWidget(self.directories_group)
        main_layout.addLayout(control_buttons_layout)
        main_layout.addLayout(progress_layout)
        main_layout.addWidget(self.log_text_edit)
        main_layout.addWidget(self.visualization_group)

        self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=False, attribute="visible")

    def create_parameters_group(self):
        parameters_group = QGroupBox("Parameters")
        parameters_layout = QFormLayout()

        self.max_games_input = QLineEdit("100000")
        self.min_elo_input = QLineEdit("2000")
        self.batch_size_input = QLineEdit("10000")

        parameters_layout.addRow("Max Games:", self.max_games_input)
        parameters_layout.addRow("Minimum ELO:", self.min_elo_input)
        parameters_layout.addRow("Batch Size:", self.batch_size_input)

        parameters_group.setLayout(parameters_layout)
        return parameters_group

    def create_directories_group(self):
        directories_group = QGroupBox("Data Directories")
        directories_layout = QFormLayout()

        self.raw_data_dir_input = QLineEdit("data/raw")
        self.processed_data_dir_input = QLineEdit("data/processed")

        raw_browse_button = QPushButton("Browse")
        raw_browse_button.clicked.connect(lambda: self.browse_dir(self.raw_data_dir_input, "Select Raw Data Directory"))
        processed_browse_button = QPushButton("Browse")
        processed_browse_button.clicked.connect(lambda: self.browse_dir(self.processed_data_dir_input, "Select Processed Data Directory"))

        directories_layout.addRow("Raw Data Directory:", self.create_browse_layout(self.raw_data_dir_input, raw_browse_button))
        directories_layout.addRow("Processed Data Directory:", self.create_browse_layout(self.processed_data_dir_input, processed_browse_button))

        directories_group.setLayout(directories_layout)
        return directories_group

    def start_data_preparation(self):
        try:
            max_games = int(self.max_games_input.text())
            min_elo = int(self.min_elo_input.text())
            batch_size = int(self.batch_size_input.text())

            if max_games <= 0 or min_elo <= 0 or batch_size <= 0:
                raise ValueError("All numerical parameters must be positive integers.")
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Max Games, Minimum ELO, and Batch Size must be positive integers.")
            return

        raw_data_dir = self.raw_data_dir_input.text()
        processed_data_dir = self.processed_data_dir_input.text()
        if not os.path.exists(raw_data_dir):
            QMessageBox.warning(self, "Error", "Raw data directory does not exist.")
            return
        os.makedirs(processed_data_dir, exist_ok=True)

        self.toggle_widget_state([self.start_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.resume_button], state=False, attribute="enabled")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")
        self.log_text_edit.clear()

        self.visualization.reset_visualization()

        self.toggle_widget_state([self.parameters_group, self.directories_group], state=False, attribute="visible")
        self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=True, attribute="visible")

        started = self.start_worker(
            DataPreparationWorker,
            raw_data_dir,
            processed_data_dir,
            max_games,
            min_elo,
            batch_size
        )
        if started:
            self.worker.stats_update.connect(self.visualization.update_data_visualization)
            self.worker.task_finished.connect(self.on_data_preparation_finished)
        else:
            self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
            self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
            self.toggle_widget_state([self.parameters_group, self.directories_group], state=True, attribute="visible")
            self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=False, attribute="visible")

    def stop_data_preparation(self):
        self.stop_worker()
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.parameters_group, self.directories_group], state=True, attribute="visible")

    def on_data_preparation_finished(self):
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
        self.progress_bar.setFormat("Data Preparation Finished")
        self.remaining_time_label.setText("Time Left: N/A")
        self.toggle_widget_state([self.parameters_group, self.directories_group], state=True, attribute="visible")

class OpeningBookTab(BaseTab):
    def __init__(self, parent=None, processed_data_dir_default="data/processed"):
        super().__init__(parent)
        self.processed_data_dir_default = processed_data_dir_default
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.opening_visualization = OpeningBookVisualization()

        self.opening_book_group = self.create_opening_book_group()
        control_buttons_layout = self.create_control_buttons(
            "Start Opening Book Generation",
            "Stop",
            self.start_opening_book_generation,
            self.stop_opening_book_generation,
            pause_text="Pause",
            resume_text="Resume",
            pause_callback=self.pause_worker,
            resume_callback=self.resume_worker
        )
        progress_layout = self.create_progress_layout()
        self.opening_log_text_edit = self.create_log_text_edit()
        self.opening_visualization_group = self.create_visualization_group(self.opening_visualization, "Opening Book Visualization")

        main_layout.addWidget(self.opening_book_group)
        main_layout.addLayout(control_buttons_layout)
        main_layout.addLayout(progress_layout)
        main_layout.addWidget(self.opening_log_text_edit)
        main_layout.addWidget(self.opening_visualization_group)

        self.toggle_widget_state([self.opening_log_text_edit, self.opening_visualization_group], state=False, attribute="visible")

    def create_opening_book_group(self):
        opening_book_group = QGroupBox("Opening Book Generation")
        opening_book_layout = QFormLayout()

        self.pgn_file_input = QLineEdit("data/raw/lichess_db_standard_rated_2024-11.pgn")
        pgn_browse_button = QPushButton("Browse")
        pgn_browse_button.clicked.connect(lambda: self.browse_file(self.pgn_file_input, "Select PGN File", "PGN Files (*.pgn)"))

        self.ob_max_games_input = QLineEdit("100000")
        self.ob_min_elo_input = QLineEdit("1500")
        self.ob_max_opening_moves_input = QLineEdit("20")

        self.ob_processed_data_dir_input = QLineEdit(self.processed_data_dir_default)
        ob_processed_browse_button = QPushButton("Browse")
        ob_processed_browse_button.clicked.connect(lambda: self.browse_dir(self.ob_processed_data_dir_input, "Select Processed Data Directory"))

        opening_book_layout.addRow("PGN File:", self.create_browse_layout(self.pgn_file_input, pgn_browse_button))
        opening_book_layout.addRow("Max Games:", self.ob_max_games_input)
        opening_book_layout.addRow("Minimum ELO:", self.ob_min_elo_input)
        opening_book_layout.addRow("Max Opening Moves:", self.ob_max_opening_moves_input)
        opening_book_layout.addRow("Processed Data Directory:", self.create_browse_layout(self.ob_processed_data_dir_input, ob_processed_browse_button))

        opening_book_group.setLayout(opening_book_layout)
        return opening_book_group

    def start_opening_book_generation(self):
        try:
            pgn_file_path = self.pgn_file_input.text()
            max_games = int(self.ob_max_games_input.text())
            min_elo = int(self.ob_min_elo_input.text())
            max_opening_moves = int(self.ob_max_opening_moves_input.text())

            if not os.path.exists(pgn_file_path):
                QMessageBox.warning(self, "Input Error", "The specified PGN file does not exist.")
                return

            if max_games <= 0 or min_elo <= 0 or max_opening_moves <= 0:
                raise ValueError("All numerical parameters must be positive integers.")

        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please ensure all inputs are valid positive integers.")
            return

        processed_data_dir = self.ob_processed_data_dir_input.text()
        if not os.path.exists(processed_data_dir):
            try:
                os.makedirs(processed_data_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Directory Error", f"Failed to create processed data directory: {str(e)}")
                return

        self.toggle_widget_state([self.start_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.resume_button], state=False, attribute="enabled")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")
        self.opening_log_text_edit.clear()

        self.opening_visualization.reset_visualization()

        self.toggle_widget_state([self.opening_book_group], state=False, attribute="visible")
        self.toggle_widget_state([self.opening_log_text_edit, self.opening_visualization_group], state=True, attribute="visible")

        started = self.start_worker(
            OpeningBookWorker,
            pgn_file_path,
            max_games,
            min_elo,
            max_opening_moves,
            processed_data_dir
        )
        if started:
            self.worker.positions_update.connect(self.opening_visualization.update_opening_book)
            self.worker.task_finished.connect(self.on_opening_book_generation_finished)
        else:
            self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
            self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
            self.toggle_widget_state([self.opening_book_group], state=True, attribute="visible")
            self.toggle_widget_state([self.opening_log_text_edit, self.opening_visualization_group], state=False, attribute="visible")

    def stop_opening_book_generation(self):
        self.stop_worker()
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.opening_book_group], state=True, attribute="visible")
        self.toggle_widget_state([self.opening_visualization_group], state=False, attribute="visible")

    def on_opening_book_generation_finished(self):
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button, self.resume_button], state=False, attribute="enabled")
        self.progress_bar.setFormat("Opening Book Generation Finished")
        self.remaining_time_label.setText("Time Left: N/A")
        self.toggle_widget_state([self.opening_book_group, self.opening_visualization_group], state=True, attribute="visible")

class DataPreparationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget(self)

        self.data_processing_tab = DataProcessingTab()
        self.opening_book_tab = OpeningBookTab()

        self.tab_widget.addTab(self.data_processing_tab, "Data Processing")
        self.tab_widget.addTab(self.opening_book_tab, "Opening Book Generation")

        main_layout.addWidget(self.tab_widget)
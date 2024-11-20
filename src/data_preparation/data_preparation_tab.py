from PyQt5.QtWidgets import QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QPushButton, QMessageBox
from src.data_preparation.data_preparation_visualization import DataPreparationVisualization
from src.data_preparation.data_preparation_worker import DataPreparationWorker
from src.base.base_tab import BaseTab
import os


class DataPreparationTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.visualization = DataPreparationVisualization()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.parameters_group = self.create_parameters_group()
        self.directories_group = self.create_directories_group()
        control_buttons_layout = self.create_control_buttons(
            "Start Data Preparation",
            "Stop",
            self.start_data_preparation,
            self.stop_data_preparation
        )
        progress_layout = self.create_progress_layout()
        self.log_text_edit = self.create_log_text_edit()
        self.visualization_group = self.create_visualization_group("Data Preparation Visualization")

        main_layout.addWidget(self.parameters_group)
        main_layout.addWidget(self.directories_group)
        main_layout.addLayout(control_buttons_layout)
        main_layout.addLayout(progress_layout)
        main_layout.addWidget(self.log_text_edit)
        main_layout.addWidget(self.visualization_group)

        self.log_text_edit.setVisible(False)
        self.visualization_group.setVisible(False)

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
        except ValueError as e:
            QMessageBox.warning(self, "Input Error", "Max Games, Minimum ELO, and Batch Size must be positive integers.")
            return

        raw_data_dir = self.raw_data_dir_input.text()
        processed_data_dir = self.processed_data_dir_input.text()
        if not os.path.exists(raw_data_dir):
            QMessageBox.warning(self, "Error", "Raw data directory does not exist.")
            return
        os.makedirs(processed_data_dir, exist_ok=True)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.log_text_edit.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")

        self.visualization.reset_visualizations()

        self.parameters_group.setVisible(False)
        self.directories_group.setVisible(False)
        self.log_text_edit.setVisible(True)
        self.visualization_group.setVisible(True)

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
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.parameters_group.setVisible(True)
            self.directories_group.setVisible(True)
            self.log_text_edit.setVisible(False)
            self.visualization_group.setVisible(False)

    def stop_data_preparation(self):
        self.stop_worker()
        self.log_message("Stopping data preparation...")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.parameters_group.setVisible(True)
        self.directories_group.setVisible(True)

    def on_data_preparation_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress_bar.setFormat("Data Preparation Finished")
        self.remaining_time_label.setText("Time Left: N/A")
        self.log_message("Data preparation process finished.")
        self.parameters_group.setVisible(True)
        self.directories_group.setVisible(True)
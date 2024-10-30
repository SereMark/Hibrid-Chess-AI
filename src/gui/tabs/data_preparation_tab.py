from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QPushButton,
    QHBoxLayout, QProgressBar, QLabel, QTextEdit, QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt
import os
from src.gui.visualizations.data_preparation_visualization import DataPreparationVisualization
from src.gui.workers.data_preparation_worker import DataPreparationWorker


class DataPreparationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.visualization = DataPreparationVisualization()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        parameters_group = QGroupBox("Parameters")
        parameters_layout = QFormLayout()

        self.max_games_input = QLineEdit("100000")
        self.min_elo_input = QLineEdit("2000")

        parameters_layout.addRow("Max Games:", self.max_games_input)
        parameters_layout.addRow("Minimum ELO:", self.min_elo_input)

        parameters_group.setLayout(parameters_layout)

        directories_group = QGroupBox("Data Directories")
        directories_layout = QFormLayout()

        self.raw_data_dir_input = QLineEdit("data/raw")
        self.processed_data_dir_input = QLineEdit("data/processed")

        raw_browse_button = QPushButton("Browse")
        raw_browse_button.clicked.connect(self.browse_raw_dir)
        processed_browse_button = QPushButton("Browse")
        processed_browse_button.clicked.connect(self.browse_processed_dir)

        raw_dir_layout = QHBoxLayout()
        raw_dir_layout.addWidget(self.raw_data_dir_input)
        raw_dir_layout.addWidget(raw_browse_button)

        processed_dir_layout = QHBoxLayout()
        processed_dir_layout.addWidget(self.processed_data_dir_input)
        processed_dir_layout.addWidget(processed_browse_button)

        directories_layout.addRow("Raw Data Directory:", raw_dir_layout)
        directories_layout.addRow("Processed Data Directory:", processed_dir_layout)

        directories_group.setLayout(directories_layout)

        control_buttons_layout = self.create_control_buttons()

        progress_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.remaining_time_label = QLabel("Time Left: Calculating...")
        self.remaining_time_label.setAlignment(Qt.AlignCenter)

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.remaining_time_label)

        main_layout.addWidget(parameters_group)
        main_layout.addWidget(directories_group)
        main_layout.addLayout(control_buttons_layout)
        main_layout.addLayout(progress_layout)
        main_layout.addWidget(self.log_text_edit())
        main_layout.addWidget(self.create_visualization_group())

    def create_control_buttons(self):
        layout = QHBoxLayout()
        self.start_button = QPushButton("Start Data Preparation")
        self.stop_button = QPushButton("Stop")
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addStretch()

        self.stop_button.setEnabled(False)

        self.start_button.clicked.connect(self.start_data_preparation)
        self.stop_button.clicked.connect(self.stop_data_preparation)
        return layout

    def create_visualization_group(self):
        visualization_group = QGroupBox("Data Preparation Visualization")
        vis_layout = QVBoxLayout()
        vis_layout.addWidget(self.visualization)
        visualization_group.setLayout(vis_layout)
        return visualization_group

    def log_text_edit(self):
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        return self.log_text_edit

    def browse_raw_dir(self):
        self.browse_dir(self.raw_data_dir_input, "Raw Data")

    def browse_processed_dir(self):
        self.browse_dir(self.processed_data_dir_input, "Processed Data")

    def browse_dir(self, line_edit, title):
        dir_path = QFileDialog.getExistingDirectory(self, f"Select {title} Directory", line_edit.text())
        if dir_path:
            line_edit.setText(dir_path)

    def start_data_preparation(self):
        try:
            max_games = int(self.max_games_input.text())
            if max_games <= 0:
                raise ValueError
            min_elo = int(self.min_elo_input.text())
            if min_elo <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Max Games and Minimum ELO must be positive integers.")
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
        self.log_text_edit.append("Starting data preparation...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")

        self.visualization.reset_visualizations()

        self.worker = DataPreparationWorker(raw_data_dir, processed_data_dir, max_games, min_elo)
        self.worker.log_update.connect(self.log_text_edit.append)
        self.worker.progress_update.connect(self.update_progress)
        self.worker.time_left_update.connect(self.update_time_left)
        self.worker.finished.connect(self.on_data_preparation_finished)
        self.worker.stats_update.connect(self.visualization.update_data_visualization)
        self.worker.start()

    def stop_data_preparation(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log_text_edit.append("Stopping data preparation...")

    def on_data_preparation_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress_bar.setFormat("Data Preparation Finished")
        self.remaining_time_label.setText("Time Left: N/A")
        self.log_text_edit.append("Data preparation process finished.")

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(f"Progress: {value}%")

    def update_time_left(self, time_left_str):
        self.remaining_time_label.setText(f"Time Left: {time_left_str}")
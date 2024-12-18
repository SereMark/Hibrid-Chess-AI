from PyQt5.QtWidgets import QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QPushButton, QHBoxLayout, QMessageBox, QCheckBox, QLabel, QComboBox, QFrame
from PyQt5.QtCore import Qt
from src.reinforcement.reinforcement_training_visualization import ReinforcementVisualization
from src.reinforcement.reinforcement_training_worker import ReinforcementWorker
from src.base.base_tab import BaseTab
import os

class ReinforcementTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.visualization = ReinforcementVisualization()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        intro_label = QLabel("Train a chess model through self-play reinforcement learning.")
        intro_label.setWordWrap(True)
        intro_label.setAlignment(Qt.AlignLeft)
        intro_label.setToolTip("Use this tab to train a model using self-play games and reinforcement learning.")

        self.model_output_group = self.create_model_output_group()
        self.parameters_group = self.create_parameters_group()
        self.checkpoint_group = self.create_checkpoint_group()

        progress_group = QGroupBox("Self-Play Progress")
        pg_layout = QVBoxLayout(progress_group)
        pg_layout.addLayout(self.create_progress_layout())

        logs_group = QGroupBox("Self-Play Logs")
        lg_layout = QVBoxLayout(logs_group)
        self.log_text_edit = self.create_log_text_edit()
        lg_layout.addWidget(self.log_text_edit)

        self.visualization_group = self.create_visualization_group(self.visualization, "Self-Play Visualization")

        controls_group = QGroupBox("Actions")
        cg_layout = QVBoxLayout(controls_group)
        control_buttons_layout = self.create_control_buttons(
            "Start Self-Play",
            "Stop Self-Play",
            self.start_self_play,
            self.stop_self_play,
            pause_text="Pause Training",
            resume_text="Resume Training",
            pause_callback=self.pause_worker,
            resume_callback=self.resume_worker
        )
        cg_layout.addLayout(control_buttons_layout)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)

        main_layout.addWidget(intro_label)
        main_layout.addWidget(self.model_output_group)
        main_layout.addWidget(self.parameters_group)
        main_layout.addWidget(self.checkpoint_group)
        main_layout.addWidget(separator)
        main_layout.addWidget(progress_group)
        main_layout.addWidget(logs_group)
        main_layout.addWidget(self.visualization_group)
        main_layout.addWidget(controls_group)

        self.setup_batch_size_control(self.automatic_batch_size_checkbox, self.batch_size_input)
        interval_widgets = {
            'iteration': self.iteration_interval_widget,
            'epoch': self.epoch_interval_widget,
            'time': self.time_interval_widget,
            'batch': self.batch_interval_widget,
        }
        self.setup_checkpoint_controls(self.save_checkpoints_checkbox, self.checkpoint_type_combo, interval_widgets)

        self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=False, attribute="visible")

    def create_model_output_group(self):
        model_output_group = QGroupBox("Output Settings")
        model_output_layout = QFormLayout()

        self.model_path_input = QLineEdit("models/saved_models/pre_trained_model.pth")
        self.model_path_input.setPlaceholderText("Path to the base model file")
        self.model_path_input.setToolTip("The model to start from or continue training.")

        self.checkpoint_path_input = QLineEdit("")
        self.checkpoint_path_input.setPlaceholderText("Path to resume from checkpoint (optional)")
        self.checkpoint_path_input.setToolTip("If resuming training, specify the checkpoint file here.")

        model_browse_button = QPushButton("Browse")
        model_browse_button.setToolTip("Browse for the model file.")
        model_browse_button.clicked.connect(lambda: self.browse_file(self.model_path_input, "Select Model File", "PyTorch Files (*.pth *.pt)"))

        model_output_layout.addRow("Model Path:", self.create_browse_layout(self.model_path_input, model_browse_button))

        model_output_group.setLayout(model_output_layout)
        return model_output_group

    def create_parameters_group(self):
        parameters_group = QGroupBox("Self-Play Parameters")
        parameters_layout = QFormLayout()

        self.num_iterations_input = QLineEdit("10")
        self.num_iterations_input.setPlaceholderText("e.g. 10")
        self.num_iterations_input.setToolTip("Number of self-play iterations.")

        self.num_games_per_iteration_input = QLineEdit("100")
        self.num_games_per_iteration_input.setPlaceholderText("e.g. 100")
        self.num_games_per_iteration_input.setToolTip("Number of games per iteration.")

        self.simulations_input = QLineEdit("800")
        self.simulations_input.setPlaceholderText("e.g. 800")
        self.simulations_input.setToolTip("Number of MCTS simulations per move.")

        self.c_puct_input = QLineEdit("1.4")
        self.c_puct_input.setPlaceholderText("e.g. 1.4")
        self.c_puct_input.setToolTip("Exploration constant for MCTS.")

        self.temperature_input = QLineEdit("1.0")
        self.temperature_input.setPlaceholderText("e.g. 1.0")
        self.temperature_input.setToolTip("Temperature parameter for move selection.")

        self.num_epochs_input = QLineEdit("5")
        self.num_epochs_input.setPlaceholderText("e.g. 5")
        self.num_epochs_input.setToolTip("Training epochs per iteration.")

        self.batch_size_input = QLineEdit("128")
        self.batch_size_input.setPlaceholderText("e.g. 128")
        self.batch_size_input.setToolTip("Batch size if not automatic.")

        self.automatic_batch_size_checkbox = QCheckBox("Automatic Batch Size")
        self.automatic_batch_size_checkbox.setToolTip("Enable automatic determination of batch size.")
        self.automatic_batch_size_checkbox.setChecked(False)

        self.num_threads_input = QLineEdit("4")
        self.num_threads_input.setPlaceholderText("e.g. 4")
        self.num_threads_input.setToolTip("Number of CPU threads for self-play and training.")

        self.random_seed_input = QLineEdit("42")
        self.random_seed_input.setPlaceholderText("e.g. 42")
        self.random_seed_input.setToolTip("Random seed for reproducibility.")

        self.optimizer_type_combo = QComboBox()
        self.optimizer_type_combo.addItems(['Adam', 'AdamW', 'SGD'])
        self.optimizer_type_combo.setToolTip("Select the optimizer for training.")

        self.learning_rate_input = QLineEdit("0.0005")
        self.learning_rate_input.setPlaceholderText("e.g. 0.0005")
        self.learning_rate_input.setToolTip("Learning rate for the optimizer.")

        self.weight_decay_input = QLineEdit("0.0001")
        self.weight_decay_input.setPlaceholderText("e.g. 0.0001")
        self.weight_decay_input.setToolTip("Weight decay for regularization.")

        self.scheduler_type_combo = QComboBox()
        self.scheduler_type_combo.addItems(['None', 'StepLR', 'CosineAnnealing', 'CosineAnnealingWarmRestarts'])
        self.scheduler_type_combo.setCurrentText('CosineAnnealing')
        self.scheduler_type_combo.setToolTip("Select the scheduler type for learning rate.")

        parameters_layout.addRow("Number of Iterations:", self.num_iterations_input)
        parameters_layout.addRow("Games per Iteration:", self.num_games_per_iteration_input)
        parameters_layout.addRow("Simulations per Move:", self.simulations_input)
        parameters_layout.addRow("c_puct:", self.c_puct_input)
        parameters_layout.addRow("Temperature:", self.temperature_input)
        parameters_layout.addRow("Training Epochs per Iteration:", self.num_epochs_input)
        parameters_layout.addRow("Batch Size:", self.batch_size_input)
        parameters_layout.addRow(self.automatic_batch_size_checkbox)
        parameters_layout.addRow("Number of Threads:", self.num_threads_input)
        parameters_layout.addRow("Random Seed:", self.random_seed_input)
        parameters_layout.addRow("Optimizer Type:", self.optimizer_type_combo)
        parameters_layout.addRow("Learning Rate:", self.learning_rate_input)
        parameters_layout.addRow("Weight Decay:", self.weight_decay_input)
        parameters_layout.addRow("Scheduler Type:", self.scheduler_type_combo)

        parameters_group.setLayout(parameters_layout)
        return parameters_group

    def create_checkpoint_group(self):
        checkpoint_group = QGroupBox("Checkpoint Settings")
        checkpoint_layout = QFormLayout()

        self.save_checkpoints_checkbox = QCheckBox("Enable Checkpoints")
        self.save_checkpoints_checkbox.setChecked(True)
        self.save_checkpoints_checkbox.setToolTip("Enable periodic saving of training checkpoints.")

        self.checkpoint_type_combo = QComboBox()
        self.checkpoint_type_combo.addItems(['Iteration', 'Epoch', 'Time', 'Batch'])
        self.checkpoint_type_combo.setToolTip("Criteria to trigger checkpoint saving.")

        checkpoint_type_layout = QHBoxLayout()
        cpl = QLabel("Save checkpoint by:")
        cpl.setToolTip("Select when to save checkpoints.")
        checkpoint_type_layout.addWidget(cpl)
        checkpoint_type_layout.addWidget(self.checkpoint_type_combo)
        checkpoint_type_layout.addStretch()

        self.checkpoint_interval_input = QLineEdit("1")
        self.checkpoint_interval_input.setPlaceholderText("e.g. 1")
        self.checkpoint_interval_input.setToolTip("Interval for iteration/epoch-based checkpoints.")

        self.checkpoint_interval_minutes_input = QLineEdit("30")
        self.checkpoint_interval_minutes_input.setPlaceholderText("e.g. 30")
        self.checkpoint_interval_minutes_input.setToolTip("Interval in minutes for time-based checkpoints.")

        self.checkpoint_batch_interval_input = QLineEdit("2000")
        self.checkpoint_batch_interval_input.setPlaceholderText("e.g. 2000")
        self.checkpoint_batch_interval_input.setToolTip("Interval in batches for batch-based checkpoints.")

        self.iteration_interval_widget = self.create_interval_widget("Every", self.checkpoint_interval_input, "iterations")
        self.epoch_interval_widget = self.create_interval_widget("Every", self.checkpoint_interval_input, "epochs")
        self.time_interval_widget = self.create_interval_widget("Every", self.checkpoint_interval_minutes_input, "minutes")
        self.batch_interval_widget = self.create_interval_widget("Every", self.checkpoint_batch_interval_input, "batches")

        checkpoint_browse_button = QPushButton("Browse")
        checkpoint_browse_button.setToolTip("Browse for a checkpoint file to resume from.")
        checkpoint_browse_button.clicked.connect(lambda: self.browse_file(self.checkpoint_path_input, "Select Checkpoint File", "PyTorch Files (*.pth *.pt)"))

        checkpoint_layout.addRow(self.save_checkpoints_checkbox)
        checkpoint_layout.addRow(checkpoint_type_layout)
        checkpoint_layout.addRow(self.iteration_interval_widget)
        checkpoint_layout.addRow(self.epoch_interval_widget)
        checkpoint_layout.addRow(self.time_interval_widget)
        checkpoint_layout.addRow(self.batch_interval_widget)
        checkpoint_layout.addRow("Resume from Checkpoint:", self.create_browse_layout(self.checkpoint_path_input, checkpoint_browse_button))

        checkpoint_group.setLayout(checkpoint_layout)
        return checkpoint_group

    def start_self_play(self):
        try:
            num_iterations = int(self.num_iterations_input.text())
            num_games_per_iteration = int(self.num_games_per_iteration_input.text())
            simulations = int(self.simulations_input.text())
            c_puct = float(self.c_puct_input.text())
            temperature = float(self.temperature_input.text())
            num_epochs = int(self.num_epochs_input.text())
            num_threads = int(self.num_threads_input.text())
            random_seed = int(self.random_seed_input.text())

            if self.automatic_batch_size_checkbox.isChecked():
                batch_size = None
            else:
                batch_size = int(self.batch_size_input.text())
                if batch_size <= 0:
                    raise ValueError("Batch Size must be a positive integer.")

            if any(v <= 0 for v in [num_iterations, num_games_per_iteration, simulations, c_puct, temperature, num_epochs, num_threads]):
                raise ValueError("All numerical parameters must be positive.")

            optimizer_type = self.optimizer_type_combo.currentText().lower()
            learning_rate = float(self.learning_rate_input.text())
            weight_decay = float(self.weight_decay_input.text())
            scheduler_type = self.scheduler_type_combo.currentText().lower()

        except ValueError as e:
            QMessageBox.warning(self, "Input Error", f"Please enter valid and positive parameters.\n{str(e)}")
            return

        model_path = self.model_path_input.text()
        checkpoint_path = self.checkpoint_path_input.text() if self.checkpoint_path_input.text() else None
        if not os.path.exists(model_path):
            QMessageBox.warning(self, "Error", "Model file does not exist.")
            return

        save_checkpoints = self.save_checkpoints_checkbox.isChecked()
        checkpoint_type = self.checkpoint_type_combo.currentText().lower()
        checkpoint_interval = None
        checkpoint_interval_minutes = None
        checkpoint_batch_interval = None

        if save_checkpoints:
            if checkpoint_type in ['iteration', 'epoch']:
                try:
                    checkpoint_interval = int(self.checkpoint_interval_input.text())
                    if checkpoint_interval <= 0:
                        raise ValueError("Interval must be positive.")
                except ValueError as e:
                    QMessageBox.warning(self, "Input Error", str(e))
                    return
            elif checkpoint_type == 'time':
                try:
                    checkpoint_interval_minutes = int(self.checkpoint_interval_minutes_input.text())
                    if checkpoint_interval_minutes <= 0:
                        raise ValueError("Time interval must be positive.")
                except ValueError as e:
                    QMessageBox.warning(self, "Input Error", str(e))
                    return
            elif checkpoint_type == 'batch':
                try:
                    checkpoint_batch_interval = int(self.checkpoint_batch_interval_input.text())
                    if checkpoint_batch_interval <= 0:
                        raise ValueError("Batch interval must be positive.")
                except ValueError as e:
                    QMessageBox.warning(self, "Input Error", str(e))
                    return

        self.toggle_widget_state([self.start_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.stop_button, self.pause_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.resume_button], state=False, attribute="enabled")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.remaining_time_label.setText("Time Left: Calculating...")
        self.log_text_edit.clear()

        if not os.path.exists(os.path.dirname(model_path)):
            os.makedirs(os.path.dirname(model_path), exist_ok=True)

        self.toggle_widget_state([self.model_output_group, self.parameters_group, self.checkpoint_group], state=False, attribute="visible")
        self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=True, attribute="visible")

        started = self.start_worker(
            ReinforcementWorker,
            model_path=model_path,
            num_iterations=num_iterations,
            num_games_per_iteration=num_games_per_iteration,
            simulations=simulations,
            c_puct=c_puct,
            temperature=temperature,
            num_epochs=num_epochs,
            batch_size=batch_size,
            automatic_batch_size=self.automatic_batch_size_checkbox.isChecked(),
            num_threads=num_threads,
            checkpoint_path=checkpoint_path,
            random_seed=random_seed,
            save_checkpoints=save_checkpoints,
            checkpoint_interval=checkpoint_interval,
            checkpoint_type=checkpoint_type,
            checkpoint_interval_minutes=checkpoint_interval_minutes,
            checkpoint_batch_interval=checkpoint_batch_interval,
            optimizer_type=optimizer_type,
            learning_rate=learning_rate,
            weight_decay=weight_decay,
            scheduler_type=scheduler_type,
        )
        if started:
            self.worker.stats_update.connect(self.visualization.update_stats)
            self.worker.task_finished.connect(self.on_self_play_finished)
            self.worker.paused.connect(self.on_worker_paused)
        else:
            self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
            self.toggle_widget_state([self.pause_button, self.stop_button, self.resume_button], state=False, attribute="enabled")
            self.toggle_widget_state([self.model_output_group, self.parameters_group, self.checkpoint_group], state=True, attribute="visible")
            self.toggle_widget_state([self.log_text_edit, self.visualization_group], state=False, attribute="visible")

    def stop_self_play(self):
        self.stop_worker()
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.pause_button, self.resume_button, self.stop_button], state=False, attribute="enabled")
        self.toggle_widget_state([self.model_output_group, self.parameters_group, self.checkpoint_group], state=True, attribute="visible")

    def on_self_play_finished(self):
        self.toggle_widget_state([self.start_button], state=True, attribute="enabled")
        self.toggle_widget_state([self.pause_button, self.resume_button, self.stop_button], state=False, attribute="enabled")
        self.progress_bar.setFormat("Self-Play Finished")
        self.remaining_time_label.setText("Time Left: N/A")
        self.toggle_widget_state([self.model_output_group, self.parameters_group, self.checkpoint_group], state=True, attribute="visible")
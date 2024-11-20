from PyQt5.QtWidgets import (
    QWidget, QTextEdit, QProgressBar, QLabel, QVBoxLayout,
    QHBoxLayout, QGroupBox, QLineEdit, QPushButton, QFileDialog, QHBoxLayout, QVBoxLayout, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread
import traceback


class BaseTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self.worker = None
        self.log_text_edit = None
        self.progress_bar = None
        self.remaining_time_label = None

    def create_log_text_edit(self):
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        return self.log_text_edit

    def create_progress_layout(self):
        layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.remaining_time_label = QLabel("Time Left: N/A")
        self.remaining_time_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.remaining_time_label)
        return layout

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(f"Progress: {value}%")

    def update_time_left(self, time_left_str):
        self.remaining_time_label.setText(f"Time Left: {time_left_str}")

    def log_message(self, message):
        if self.log_text_edit:
            self.log_text_edit.append(message)
        else:
            print(message)

    def start_worker(self, worker_class, *args, **kwargs):
        if self.thread is not None and self.thread.isRunning():
            self.log_message("A worker is already running.")
            return False

        try:
            self.thread = QThread()
            self.worker = worker_class(*args, **kwargs)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.finished.connect(self.thread.quit)
            self.worker.finished.connect(self.worker.deleteLater)
            self.thread.finished.connect(self.thread.deleteLater)
            self.thread.finished.connect(self.on_worker_finished)
            self.worker.log_update.connect(self.log_message)
            self.worker.progress_update.connect(self.update_progress)
            self.worker.time_left_update.connect(self.update_time_left)
            self.thread.start()
            return True
        except Exception as e:
            self.log_message(f"Error starting worker: {str(e)}\n{traceback.format_exc()}")
            return False

    def stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.log_message("Worker stop requested.")
        else:
            self.log_message("No worker to stop.")

    def on_worker_finished(self):
        self.worker = None
        self.thread = None

    def create_browse_layout(self, line_edit, browse_button):
        layout = QHBoxLayout()
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return layout

    def create_interval_widget(self, prefix, input_field, suffix):
        layout = QHBoxLayout()
        layout.addWidget(QLabel(prefix))
        layout.addWidget(input_field)
        layout.addWidget(QLabel(suffix))
        layout.addStretch()
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def create_visualization_group(self, title: str):
        visualization_group = QGroupBox(title)
        vis_layout = QVBoxLayout()
        self.visualization.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vis_layout.addWidget(self.visualization)
        visualization_group.setLayout(vis_layout)
        return visualization_group

    def create_control_buttons(self, start_text, stop_text, start_callback, stop_callback, pause_text=None, resume_text=None, pause_callback=None, resume_callback=None):
        layout = QHBoxLayout()
        self.start_button = QPushButton(start_text)
        layout.addWidget(self.start_button)
        self.stop_button = QPushButton(stop_text)
        layout.addWidget(self.stop_button)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(start_callback)
        self.stop_button.clicked.connect(stop_callback)

        if pause_text and resume_text and pause_callback and resume_callback:
            self.pause_button = QPushButton(pause_text)
            self.resume_button = QPushButton(resume_text)
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(False)
            layout.addWidget(self.pause_button)
            layout.addWidget(self.resume_button)
            self.pause_button.clicked.connect(pause_callback)
            self.resume_button.clicked.connect(resume_callback)
        layout.addStretch()
        return layout

    def browse_file(self, input_field: QLineEdit, title: str, file_filter: str):
        file_path, _ = QFileDialog.getOpenFileName(self, title, input_field.text(), file_filter)
        if file_path:
            input_field.setText(file_path)

    def browse_dir(self, input_field: QLineEdit, title: str):
        dir_path = QFileDialog.getExistingDirectory(self, title, input_field.text())
        if dir_path:
            input_field.setText(dir_path)

    def toggle_batch_size_input(self, checked):
        if hasattr(self, 'batch_size_input'):
            self.batch_size_input.setEnabled(not checked)

    def pause_worker(self):
        if self.worker:
            self.worker.pause()

    def resume_worker(self):
        if self.worker:
            self.worker.resume()

    def on_worker_paused(self, is_paused):
        if hasattr(self, 'pause_button') and hasattr(self, 'resume_button'):
            self.pause_button.setEnabled(not is_paused)
            self.resume_button.setEnabled(is_paused)
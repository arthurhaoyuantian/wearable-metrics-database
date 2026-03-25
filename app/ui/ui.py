from datetime import date, timedelta, datetime

from PyQt5.QtWidgets import (
    QWidget,
    QPushButton,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QDateEdit,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QDate
from PyQt5.QtGui import QFont
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from app.integrations import fitbit_auth
from app.data.database import EHRDatabase


class MetricSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select metrics")
        self.resize(520, 230)

        self.heart_checkbox = QCheckBox("Heart Rate")
        self.heart_checkbox.setChecked(True)
        self.heart_mode = QComboBox()
        self.heart_mode.addItem("daily only", "daily")
        self.heart_mode.addItem("intraday only", "intraday")
        self.heart_mode.addItem("intraday + daily", "both")

        self.steps_checkbox = QCheckBox("Steps")
        self.steps_checkbox.setChecked(False)
        self.steps_mode = QComboBox()
        self.steps_mode.addItem("daily only", "daily")
        self.steps_mode.addItem("intraday only", "intraday")
        self.steps_mode.addItem("intraday + daily", "both")

        heart_row = QHBoxLayout()
        heart_row.addWidget(self.heart_checkbox)
        heart_row.addWidget(QLabel("View:"))
        heart_row.addWidget(self.heart_mode)

        steps_row = QHBoxLayout()
        steps_row.addWidget(self.steps_checkbox)
        steps_row.addWidget(QLabel("View:"))
        steps_row.addWidget(self.steps_mode)

        info = QLabel("Choose metrics to include before opening graph window.")
        info.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(info)
        layout.addLayout(heart_row)
        layout.addLayout(steps_row)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_selection(self):
        selection = {}
        if self.heart_checkbox.isChecked():
            selection["heart"] = self.heart_mode.currentData()
        if self.steps_checkbox.isChecked():
            selection["steps"] = self.steps_mode.currentData()
        return selection


class GraphWindow(QWidget):
    def __init__(self, metric_selection):
        super().__init__()
        self.db = EHRDatabase("test.db")
        self.metric_selection = metric_selection
        self.dragging = False
        self.drag_start_x = None
        self.drag_start_xlim = None
        self.hover_annotation = None
        self.point_lookup = []

        self.setWindowTitle("Health Metrics Graph")
        self.resize(1366, 768)

        self.status_label = QLabel("Select patient/date range and generate chart.")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.chart_button = QPushButton("show graph")
        self.zoom_in_button = QPushButton("+")
        self.zoom_out_button = QPushButton("-")
        self.patient_dropdown = QComboBox()
        self.patient_dropdown.setMinimumWidth(220)
        self.view_config_label = QLabel(self._format_metric_selection())

        self.from_date_edit = QDateEdit()
        self.to_date_edit = QDateEdit()
        self.from_date_edit.setCalendarPopup(True)
        self.to_date_edit.setCalendarPopup(True)
        self.from_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.to_date_edit.setDisplayFormat("yyyy-MM-dd")

        today = date.today()
        one_week_ago = today - timedelta(days=7)
        self.from_date_edit.setDate(QDate(one_week_ago.year, one_week_ago.month, one_week_ago.day))
        self.to_date_edit.setDate(QDate(today.year, today.month, today.day))

        self.figure = Figure(figsize=(8, 4))
        self.canvas = FigureCanvas(self.figure)

        small_font = QFont("Arial", 10)
        self.chart_button.setFont(small_font)
        self.zoom_in_button.setFont(small_font)
        self.zoom_out_button.setFont(small_font)
        self.patient_dropdown.setFont(small_font)
        self.view_config_label.setFont(small_font)
        self.from_date_edit.setFont(small_font)
        self.to_date_edit.setFont(small_font)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Patient:"))
        top_row.addWidget(self.patient_dropdown, stretch=1)
        top_row.addWidget(QLabel("Metrics:"))
        top_row.addWidget(self.view_config_label)
        top_row.addWidget(QLabel("From:"))
        top_row.addWidget(self.from_date_edit)
        top_row.addWidget(QLabel("To:"))
        top_row.addWidget(self.to_date_edit)
        top_row.addWidget(self.zoom_out_button)
        top_row.addWidget(self.zoom_in_button)
        top_row.addWidget(self.chart_button)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(top_row)
        layout.addWidget(self.canvas, stretch=1)
        self.setLayout(layout)

        self.ax = None
        self.full_xlim = None
        self.current_xlim = None
        self.min_window_days = 1 / 24  # 1 hour

        self.chart_button.clicked.connect(self.show_graph)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.refresh_patients()
        self._draw_empty_chart("No graph loaded yet.")

        self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)

    def _format_metric_selection(self):
        label_map = {
            "daily": "daily",
            "intraday": "intraday",
            "both": "daily+intraday",
        }
        parts = []
        for metric in ("heart", "steps"):
            if metric in self.metric_selection:
                parts.append(f"{metric}:{label_map[self.metric_selection[metric]]}")
        return " | ".join(parts) if parts else "none"

    def refresh_patients(self):
        self.patient_dropdown.clear()
        patients = self.db.get_all_patients()
        for patient_id, name in patients:
            self.patient_dropdown.addItem(f"{name} (ID: {patient_id})", patient_id)
        if not patients:
            self.patient_dropdown.addItem("No patients available", None)

    def _draw_empty_chart(self, message):
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.ax.text(0.5, 0.5, message, ha="center", va="center", transform=self.ax.transAxes)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw()
        self.full_xlim = None
        self.current_xlim = None
        self.point_lookup = []
        self.hover_annotation = None

    def _style_axes(self):
        for spine in self.ax.spines.values():
            spine.set_linewidth(2.2)
            spine.set_color("black")
        self.ax.tick_params(width=1.5, colors="black")

    def _update_axis_ticks(self):
        if not self.ax:
            return
        xmin, xmax = self.current_xlim if self.current_xlim else self.ax.get_xlim()
        span_days = max(xmax - xmin, 1e-9)

        if span_days > 60:
            locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        elif span_days > 7:
            locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
        elif span_days > 1:
            locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
        else:
            locator = mdates.AutoDateLocator(minticks=6, maxticks=12)

        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        for label in self.ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")

    def _apply_xlim(self, new_start, new_end):
        if not self.ax or not self.full_xlim:
            return
        full_start, full_end = self.full_xlim
        full_span = full_end - full_start
        window = max(new_end - new_start, self.min_window_days)
        window = min(window, full_span)
        clamped_start = max(full_start, min(new_start, full_end - window))
        clamped_end = clamped_start + window
        self.current_xlim = (clamped_start, clamped_end)
        self.ax.set_xlim(*self.current_xlim)
        self._update_axis_ticks()
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def zoom_in(self):
        if not self.current_xlim:
            return
        start, end = self.current_xlim
        center = (start + end) / 2
        new_half = (end - start) * 0.35
        self._apply_xlim(center - new_half, center + new_half)

    def zoom_out(self):
        if not self.current_xlim:
            return
        start, end = self.current_xlim
        center = (start + end) / 2
        new_half = (end - start) * 0.7
        self._apply_xlim(center - new_half, center + new_half)

    def on_mouse_press(self, event):
        if event.inaxes != self.ax or event.button != 1 or not self.current_xlim:
            return
        self.dragging = True
        self.drag_start_x = event.xdata
        self.drag_start_xlim = self.current_xlim

    def on_mouse_release(self, _event):
        self.dragging = False
        self.drag_start_x = None
        self.drag_start_xlim = None

    def _update_hover(self, event):
        if event.inaxes != self.ax or not self.point_lookup:
            if self.hover_annotation and self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return

        hover_x = event.xdata
        hover_y = event.ydata
        if hover_x is None or hover_y is None:
            return

        threshold_x = max((self.current_xlim[1] - self.current_xlim[0]) * 0.02, 1e-6)
        y_min, y_max = self.ax.get_ylim()
        threshold_y = max((y_max - y_min) * 0.06, 1e-6)

        best = None
        best_score = float("inf")
        for item in self.point_lookup:
            dx = abs(item["x"] - hover_x)
            dy = abs(item["y"] - hover_y)
            if dx > threshold_x or dy > threshold_y:
                continue
            score = dx + dy
            if score < best_score:
                best_score = score
                best = item

        if not best:
            if self.hover_annotation and self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return

        if self.hover_annotation is None:
            self.hover_annotation = self.ax.annotate(
                "",
                xy=(best["x"], best["y"]),
                xytext=(10, 15),
                textcoords="offset points",
                bbox={"boxstyle": "round", "fc": "w", "alpha": 0.9},
                arrowprops={"arrowstyle": "->", "color": "black"},
            )

        timestamp_text = best["dt"].strftime("%Y-%m-%d %H:%M")
        self.hover_annotation.set_text(
            f"{best['label']}\n{timestamp_text}\n{best['y']} {best['unit']}"
        )
        self.hover_annotation.xy = (best["x"], best["y"])
        self.hover_annotation.set_visible(True)
        self.canvas.draw_idle()

    def on_mouse_move(self, event):
        if self.dragging and event.inaxes == self.ax and self.drag_start_x is not None:
            if event.xdata is None:
                return
            delta = event.xdata - self.drag_start_x
            start, end = self.drag_start_xlim
            self._apply_xlim(start - delta, end - delta)
        self._update_hover(event)

    def show_graph(self):
        if not self.metric_selection:
            self.status_label.setText("No metric selected. Reopen and select at least one metric.")
            self._draw_empty_chart("No metric selected.")
            return

        selected_id = self.patient_dropdown.currentData()
        if selected_id is None:
            self.status_label.setText("No patients found in DB. Add/import data first.")
            self._draw_empty_chart("No patients found.")
            return

        patient_id = selected_id
        start_date = self.from_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.to_date_edit.date().toString("yyyy-MM-dd")
        if start_date > end_date:
            self.status_label.setText("Invalid date range: start date must be before end date.")
            self._draw_empty_chart("Invalid date range.")
            return

        daily_data = self.db.get_patient_daily_health_data(patient_id, start_date, end_date)
        start_datetime = f"{start_date} 00:00:00"
        end_datetime = f"{end_date} 23:59:59"
        intraday_data = self.db.get_patient_intraday_health_data(patient_id, start_datetime, end_datetime)

        metric_specs = {
            "heart": {
                "daily_idx": 2,
                "intraday_idx": 2,
                "daily_label": "heart daily",
                "intraday_label": "heart intraday",
                "unit": "bpm",
            },
            "steps": {
                "daily_idx": 1,
                "intraday_idx": 1,
                "daily_label": "steps daily",
                "intraday_label": "steps intraday",
                "unit": "steps",
            },
        }

        plotted_any = False
        plotted_labels = []

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.point_lookup = []

        for metric_name, mode in self.metric_selection.items():
            if metric_name not in metric_specs:
                continue
            spec = metric_specs[metric_name]

            if mode in ("daily", "both"):
                metric_daily = [(row[0], row[spec["daily_idx"]]) for row in daily_data if row[spec["daily_idx"]] is not None]
                if metric_daily:
                    daily_dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in metric_daily]
                    daily_vals = [v for _, v in metric_daily]
                    self.ax.plot(daily_dates, daily_vals, marker="o", linestyle="-", label=spec["daily_label"])
                    self.point_lookup.extend(
                        {
                            "x": mdates.date2num(dt_obj),
                            "y": val,
                            "dt": dt_obj,
                            "label": spec["daily_label"],
                            "unit": spec["unit"],
                        }
                        for dt_obj, val in zip(daily_dates, daily_vals)
                    )
                    plotted_any = True
                    plotted_labels.append(spec["daily_label"])

            if mode in ("intraday", "both"):
                metric_intraday = [
                    (row[0], row[spec["intraday_idx"]])
                    for row in intraday_data
                    if row[spec["intraday_idx"]] is not None
                ]
                if metric_intraday:
                    intraday_dates = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t, _ in metric_intraday]
                    intraday_vals = [v for _, v in metric_intraday]
                    self.ax.plot(
                        intraday_dates,
                        intraday_vals,
                        marker=".",
                        linestyle="-",
                        linewidth=1,
                        label=spec["intraday_label"],
                    )
                    self.point_lookup.extend(
                        {
                            "x": mdates.date2num(dt_obj),
                            "y": val,
                            "dt": dt_obj,
                            "label": spec["intraday_label"],
                            "unit": spec["unit"],
                        }
                        for dt_obj, val in zip(intraday_dates, intraday_vals)
                    )
                    plotted_any = True
                    plotted_labels.append(spec["intraday_label"])

        if not plotted_any:
            self.status_label.setText(f"No selected metric data from {start_date} to {end_date}.")
            self._draw_empty_chart("No selected metric data in date range.")
            return

        self.ax.set_title("Selected Metrics Over Time")
        self.ax.set_xlabel("Date / Timestamp")
        self.ax.set_ylabel("Value")
        self.ax.grid(True, alpha=0.3)
        self.ax.legend()
        self._style_axes()

        self.full_xlim = self.ax.get_xlim()
        self.current_xlim = self.full_xlim
        self._update_axis_ticks()
        self.figure.tight_layout()
        self.canvas.draw()

        self.status_label.setText(
            f"Showing {', '.join(plotted_labels)} for patient {patient_id} "
            f"from {start_date} to {end_date}. Drag chart to pan."
        )

    def closeEvent(self, event):
        self.db.close()
        super().closeEvent(event)


class MainWindow(QWidget):
    token_recieved = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.graph_windows = []
        self.setWindowTitle("Smartwatch Data App")
        self.resize(900, 520)

        title = QLabel("Smartwatch EHR")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Arial", 24))

        subtitle = QLabel("Choose an action:")
        subtitle.setAlignment(Qt.AlignCenter)

        self.connect_button = QPushButton("Connect to Fitbit")
        self.open_graph_button = QPushButton("Open Graph Window")
        self.status_label = QLabel("Ready.")
        self.status_label.setAlignment(Qt.AlignCenter)

        row = QHBoxLayout()
        row.addWidget(self.connect_button)
        row.addWidget(self.open_graph_button)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.setLayout(layout)

        self.connect_button.clicked.connect(self.on_login_click)
        self.open_graph_button.clicked.connect(self.open_graph_window)

    def on_login_click(self):
        self.status_label.setText("Attempting to connect...")
        fitbit_auth.start_server()
        fitbit_auth.start_auth_flow()
        self.status_label.setText("Fitbit auth launched in browser.")

    def open_graph_window(self):
        dialog = MetricSelectionDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            self.status_label.setText("Graph window launch cancelled.")
            return

        metric_selection = dialog.get_selection()
        if not metric_selection:
            self.status_label.setText("Select at least one metric to open a graph window.")
            return

        graph_window = GraphWindow(metric_selection)
        graph_window.show()
        self.graph_windows.append(graph_window)
        self.status_label.setText("Graph window opened.")
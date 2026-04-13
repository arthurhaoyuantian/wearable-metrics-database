import logging
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
    QLineEdit,
    QMessageBox,
    QFileDialog,
    QInputDialog,
)
from PyQt5.QtCore import Qt, pyqtSignal, QDate
from PyQt5.QtGui import QFont
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from src.integrations import fitbit_auth
from src.data.database import EHRDatabase
from src.services.metrics_service import METRIC_SPECS, METRIC_DIALOG_ROWS
from src.integrations.csv_import import import_daily_csv, list_dates_in_daily_csv

log = logging.getLogger(__name__)


def _make_view_combo(daily_only=False):
    combo = QComboBox()
    if daily_only:
        combo.addItem("daily only", "daily")
    else:
        combo.addItem("daily only", "daily")
        combo.addItem("intraday only", "intraday")
        combo.addItem("intraday + daily", "both")
    return combo


class MetricSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select metrics")
        self.resize(560, 480)
        self._rows = {}

        info = QLabel(
            "Choose metrics for this graph window."
        )
        info.setWordWrap(True)

        form = QVBoxLayout()
        for key, label, default_on, daily_only in METRIC_DIALOG_ROWS:
            cb = QCheckBox(label)
            cb.setChecked(default_on)
            combo = _make_view_combo(daily_only=daily_only)
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(QLabel("View:"))
            row.addWidget(combo)
            form.addLayout(row)
            self._rows[key] = (cb, combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_selection(self):
        selection = {}
        for key, (cb, combo) in self._rows.items():
            if cb.isChecked():
                selection[key] = combo.currentData()
        return selection


class GraphWindow(QWidget):
    def __init__(self, metric_selection, patient_id, patient_name=None):
        super().__init__()
        self.db = EHRDatabase()
        self.metric_selection = metric_selection
        self._patient_id = patient_id
        if patient_name is None:
            row = self.db.get_patient_info(patient_id)
            patient_name = row[1] if row else "Unknown"
        self._patient_name = patient_name
        self.dragging = False
        self.drag_start_x = None
        self.drag_start_xlim = None
        self.hover_annotation = None
        self.point_lookup = []

        self.setWindowTitle(f"Health Metrics — {self._patient_name} (ID {self._patient_id})")
        self.resize(1366, 768)

        self.status_label = QLabel("Select date range and click show graph.")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.chart_button = QPushButton("show graph")
        self.zoom_in_button = QPushButton("+")
        self.zoom_out_button = QPushButton("-")
        self.patient_label = QLabel(f"{self._patient_name} (ID: {self._patient_id})")
        self.patient_label.setMinimumWidth(220)
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(160)
        self.source_combo.setToolTip(
            "Show rows for one source tag only (e.g. fitbit), or all sources mixed."
        )
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
        self.patient_label.setFont(small_font)
        self.view_config_label.setFont(small_font)
        self.from_date_edit.setFont(small_font)
        self.to_date_edit.setFont(small_font)
        self.source_combo.setFont(small_font)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Patient:"))
        top_row.addWidget(self.patient_label)
        top_row.addWidget(QLabel("Source:"))
        top_row.addWidget(self.source_combo)
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
        self.refresh_source_combo()
        self._draw_empty_chart("No graph loaded yet.")

        self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.canvas.mpl_connect("scroll_event", self.on_scroll)

    def _format_metric_selection(self):
        label_map = {
            "daily": "daily",
            "intraday": "intraday",
            "both": "daily+intraday",
        }
        parts = []
        for metric, mode in sorted(self.metric_selection.items()):
            parts.append(f"{metric}:{label_map.get(mode, mode)}")
        return " | ".join(parts) if parts else "none"

    def refresh_source_combo(self):
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("All sources", None)
        for src in self.db.get_sources_for_patient(self._patient_id):
            self.source_combo.addItem(src, src)
        self.source_combo.blockSignals(False)

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
        
        # Check if the span is extremely small (less than 1 hour)
        if span_days < 1/24:  # Less than 1 hour
            from matplotlib.dates import MinuteLocator
            # For very small ranges, use minute locator
            interval = max(1, int(span_days * 24 * 60 / 6))  # Show about 6 ticks
            locator = MinuteLocator(interval=interval)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        elif span_days < 1:  # Less than 1 day but more than 1 hour
            from matplotlib.dates import HourLocator
            interval = max(1, int(span_days * 24 / 6))  # Show about 6 ticks
            locator = HourLocator(interval=interval)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        else:  # More than 1 day
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

    def _scroll_ctrl_held(self, event):
        key = getattr(event, "key", None) or ""
        kl = str(key).lower()
        if "ctrl" in kl or kl == "control":
            return True
        ge = getattr(event, "guiEvent", None)
        if ge is not None and hasattr(ge, "modifiers"):
            try:
                if ge.modifiers() & Qt.ControlModifier:
                    return True
            except (TypeError, AttributeError):
                pass
        return False

    def on_scroll(self, event):
        if event.inaxes != self.ax or not self.current_xlim or not self.full_xlim:
            return
        if not event.step:
            return

        start, end = self.current_xlim
        span = end - start
        if span <= 0:
            return

        if self._scroll_ctrl_held(event):
            zoom_step = 1.15
            if event.step > 0:
                new_span = span / zoom_step
            else:
                new_span = span * zoom_step
            full_start, full_end = self.full_xlim
            max_span = full_end - full_start
            new_span = max(new_span, self.min_window_days)
            new_span = min(new_span, max_span)

            cx = event.xdata if event.xdata is not None else (start + end) / 2
            ratio = (cx - start) / span if span else 0.5
            new_start = cx - ratio * new_span
            new_end = new_start + new_span
            self._apply_xlim(new_start, new_end)
            return

        pan_frac = 0.12
        delta = span * pan_frac
        if event.step > 0:
            self._apply_xlim(start - delta, end - delta)
        else:
            self._apply_xlim(start + delta, end + delta)

    def show_graph(self):
        if not self.metric_selection:
            self.status_label.setText("No metric selected. Reopen and select at least one metric.")
            self._draw_empty_chart("No metric selected.")
            return

        patient_id = self._patient_id
        source_filter = self.source_combo.currentData()
        allow_fitbit_import = source_filter is None or source_filter == "fitbit"
        start_date = self.from_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.to_date_edit.date().toString("yyyy-MM-dd")
        if start_date > end_date:
            self.status_label.setText("Invalid date range: start date must be before end date.")
            self._draw_empty_chart("Invalid date range.")
            log.warning("show_graph: invalid date range %s > %s", start_date, end_date)
            return

        log.info(
            "show_graph: patient_id=%s source_filter=%s range=%s..%s metrics=%s",
            patient_id,
            source_filter,
            start_date,
            end_date,
            list(self.metric_selection.keys()),
        )

        # FIRST: Check what data already exists in the database for this range
        self.status_label.setText(f"Checking existing data from {start_date} to {end_date}...")
        self.canvas.draw()
        
        # Get ALL dates in the range
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        all_dates_in_range = []
        current = start_dt
        while current <= end_dt:
            all_dates_in_range.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        
        # Get existing daily data dates
        existing_daily_data = self.db.get_patient_daily_health_data(
            patient_id, start_date, end_date, source=source_filter
        )
        existing_daily_dates = {row[0] for row in existing_daily_data}
        
        # Get existing intraday data dates
        start_datetime = f"{start_date} 00:00:00"
        end_datetime = f"{end_date} 23:59:59"
        existing_intraday_data = self.db.get_patient_intraday_health_data(
            patient_id, start_datetime, end_datetime, source=source_filter
        )
        existing_intraday_dates = {row[0].split()[0] for row in existing_intraday_data}  # Extract just the date part
        
        # Determine what data we need based on selected metrics
        needs_daily = False
        needs_intraday = False
        for metric_name, mode in self.metric_selection.items():
            if mode in ("daily", "both"):
                needs_daily = True
            if mode in ("intraday", "both"):
                needs_intraday = True
        
        # Check which dates are missing
        missing_daily_dates = []
        missing_intraday_dates = []
        
        if needs_daily:
            missing_daily_dates = [d for d in all_dates_in_range if d not in existing_daily_dates]
        
        if needs_intraday:
            missing_intraday_dates = [d for d in all_dates_in_range if d not in existing_intraday_dates]
        
        if missing_daily_dates or missing_intraday_dates:
            if not allow_fitbit_import:
                daily_data = existing_daily_data
                intraday_data = existing_intraday_data
                self.status_label.setText(
                    f"Showing stored data for source {source_filter!r} only (no Fitbit auto-import)."
                )
            else:
                patient_info = self.db.get_patient_info(patient_id)
                has_tokens = patient_info and patient_info[2] and patient_info[3]

                if not has_tokens:
                    self.status_label.setText(
                        "No Fitbit tokens found. Connect to Fitbit first to import data."
                    )
                    self._draw_empty_chart("Connect to Fitbit to import data.")
                    log.warning(
                        "show_graph: missing data but patient_id=%s has no Fitbit tokens",
                        patient_id,
                    )
                    return

                missing_desc = []
                if missing_daily_dates:
                    missing_desc.append(f"{len(missing_daily_dates)} daily data points")
                if missing_intraday_dates:
                    missing_desc.append(f"{len(missing_intraday_dates)} days of intraday data")
                missing_text = " and ".join(missing_desc)

                reply = QMessageBox.question(
                    self,
                    "Import Fitbit Data?",
                    f"Missing {missing_text} for {start_date} to {end_date}.\n\n"
                    f"Would you like to import this data from Fitbit?\n"
                    f"(This may take a moment for large date ranges)",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )

                if reply == QMessageBox.Yes:
                    self.status_label.setText(
                        f"Importing missing Fitbit data from {start_date} to {end_date}..."
                    )
                    self.canvas.draw()
                    log.info(
                        "show_graph: user accepted Fitbit import patient_id=%s missing_daily=%s missing_intraday_days=%s",
                        patient_id,
                        len(missing_daily_dates),
                        len(missing_intraday_dates),
                    )

                    try:
                        imported_count = self.db.import_fitbit_data(
                            patient_id=patient_id,
                            start=start_date,
                            end=end_date,
                            include_intraday=needs_intraday,
                        )
                        log.info(
                            "show_graph: Fitbit import returned total_row_counter=%s",
                            imported_count,
                        )
                        self.status_label.setText(
                            f"Imported {imported_count} data records. Refreshing data..."
                        )
                        self.canvas.draw()
                        daily_data = self.db.get_patient_daily_health_data(
                            patient_id, start_date, end_date, source=source_filter
                        )
                        intraday_data = self.db.get_patient_intraday_health_data(
                            patient_id, start_datetime, end_datetime, source=source_filter
                        )
                        prev_source = source_filter
                        self.refresh_source_combo()
                        if prev_source is not None:
                            idx = self.source_combo.findData(prev_source)
                            if idx >= 0:
                                self.source_combo.setCurrentIndex(idx)
                    except Exception as e:
                        log.exception("show_graph: Fitbit import failed")
                        self.status_label.setText(f"Error importing Fitbit data: {str(e)}")
                        daily_data = existing_daily_data
                        intraday_data = existing_intraday_data
                else:
                    self.status_label.setText("Using existing data only.")
                    daily_data = existing_daily_data
                    intraday_data = existing_intraday_data
        else:
            daily_data = existing_daily_data
            intraday_data = existing_intraday_data
            self.status_label.setText("Using existing data from database.")
        
        # Now check if we have any data to display
        has_daily_data = len(daily_data) > 0
        has_intraday_data = len(intraday_data) > 0
        
        # Final check: do we have any data to display?
        data_available = False
        for metric_name, mode in self.metric_selection.items():
            if mode in ("daily", "both") and has_daily_data:
                data_available = True
            if mode in ("intraday", "both") and has_intraday_data:
                data_available = True
        
        if not data_available:
            self.status_label.setText(f"No data available for {start_date} to {end_date}.")
            self._draw_empty_chart("No data available in selected date range.")
            log.warning(
                "show_graph: no rows to plot patient_id=%s range=%s..%s",
                patient_id,
                start_date,
                end_date,
            )
            return

        plotted_any = False
        plotted_labels = []

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.point_lookup = []

        for metric_name, mode in self.metric_selection.items():
            if metric_name not in METRIC_SPECS:
                continue
            spec = METRIC_SPECS[metric_name]

            if mode in ("daily", "both") and has_daily_data:
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

            if (
                mode in ("intraday", "both")
                and has_intraday_data
                and spec.get("intraday_idx") is not None
            ):
                idx = spec["intraday_idx"]
                metric_intraday = [
                    (row[0], row[idx]) for row in intraday_data if row[idx] is not None
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
            log.warning(
                "show_graph: data exists but nothing plotted (nulls?) patient_id=%s",
                patient_id,
            )
            return

        title_suffix = "" if source_filter is None else f" ({source_filter})"
        self.ax.set_title(f"Selected Metrics Over Time{title_suffix}")
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

        src_note = "all sources" if source_filter is None else repr(source_filter)
        self.status_label.setText(
            f"Showing {', '.join(plotted_labels)} for patient {patient_id} ({src_note}) "
            f"from {start_date} to {end_date}. Drag to pan; wheel = time scroll; "
            f"Ctrl+wheel = zoom."
        )
        log.info(
            "show_graph: plotted patient_id=%s series=%s daily_rows=%s intraday_rows=%s",
            patient_id,
            plotted_labels,
            len(daily_data),
            len(intraday_data),
        )
    
    def closeEvent(self, event):
        log.info("GraphWindow closing (DB connection close)")
        self.db.close()
        super().closeEvent(event)

class AddPatientDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Patient")
        self.resize(300, 120)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter patient name")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Patient Name:"))
        layout.addWidget(self.name_input)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def get_name(self):
        return self.name_input.text().strip()
class MainWindow(QWidget):
    token_recieved = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.graph_windows = []
        self.setWindowTitle("Wearable Health Data Aggregation App")
        self.resize(900, 520)

        title = QLabel("Wearable Health Database")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Arial", 24))

        subtitle = QLabel("Choose an action:")
        subtitle.setAlignment(Qt.AlignCenter)

        self.connect_button = QPushButton("Connect to Fitbit")
        self.open_graph_button = QPushButton("Open Graph Window")
        self.status_label = QLabel("Ready.")
        self.status_label.setAlignment(Qt.AlignCenter)
        
        self.db = EHRDatabase()
        self.patient_dropdown = QComboBox()
        self.patient_dropdown.setMinimumWidth(220)
        self.refresh_patients()

        self.add_patient_button = QPushButton("Add Patient")
        self.add_patient_button.clicked.connect(self.on_add_patient)
        self.import_csv_button = QPushButton("Import daily CSV…")
        self.import_csv_button.clicked.connect(self.on_import_daily_csv)
        
        row = QHBoxLayout()
        row.addWidget(QLabel("Patient:"))
        row.addWidget(self.patient_dropdown)
        row.addWidget(self.connect_button)
        row.addWidget(self.open_graph_button)
        row.addWidget(self.import_csv_button)
        row.addWidget(self.add_patient_button)

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

    def on_import_daily_csv(self):
        patient_id = self.patient_dropdown.currentData()
        if patient_id is None:
            self.status_label.setText("Select a patient first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import daily health CSV",
            "",
            "CSV (*.csv);;All files (*)",
        )
        if not path:
            return
        log.info("CSV UI: picked file=%s patient_id=%s", path, patient_id)

        dates, errs = list_dates_in_daily_csv(path)
        if errs:
            QMessageBox.warning(self, "CSV import", "\n".join(errs))
            return
        if not dates:
            QMessageBox.information(
                self, "CSV import", "No data rows with a date were found."
            )
            return

        src_text, ok = QInputDialog.getText(
            self,
            "Source tag",
            "Source for imported rows (e.g. manual, csv). Shown in graph Source filter:",
            text="csv",
        )
        if not ok:
            self.status_label.setText("CSV import cancelled.")
            log.info("CSV UI: user cancelled source dialog")
            return
        source = src_text.strip() or "csv"

        overlapping = self.db.daily_dates_existing_for_source(patient_id, source, dates)
        overwrite = False
        if overlapping:
            log.warning(
                "CSV UI: overlap patient_id=%s source=%s overlapping_dates=%s",
                patient_id,
                source,
                len(overlapping),
            )
            preview = ", ".join(overlapping[:8])
            if len(overlapping) > 8:
                preview += f", … (+{len(overlapping) - 8} more)"
            reply = QMessageBox.question(
                self,
                "Date overlap",
                f"{len(overlapping)} day(s) already have daily data for this patient "
                f"with source {source!r}:\n{preview}\n\n"
                "Replace those rows with the CSV data?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.status_label.setText("CSV import cancelled (overlap not confirmed).")
                log.info("CSV UI: user declined overwrite for %s overlapping dates", len(overlapping))
                return
            overwrite = True
            log.info("CSV UI: user confirmed overwrite for %s dates", len(overlapping))

        result = import_daily_csv(
            self.db, path, patient_id=patient_id, source=source, overwrite=overwrite
        )
        err_tail = ""
        if result["errors"]:
            err_tail = " Errors: " + "; ".join(result["errors"][:5])
            if len(result["errors"]) > 5:
                err_tail += f" … (+{len(result['errors']) - 5} more)"
        self.status_label.setText(
            f"CSV import: inserted {result['inserted']}, skipped {result['skipped']}.{err_tail}"
        )
        log.info(
            "CSV UI: done inserted=%s skipped=%s error_count=%s overwrite=%s",
            result["inserted"],
            result["skipped"],
            len(result["errors"]),
            overwrite,
        )
    
    def on_add_patient(self):
        dialog = AddPatientDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        name = dialog.get_name()
        if not name:
            self.status_label.setText("Patient name cannot be empty.")
            return
        patient_id = self.db.add_patient(name)
        if patient_id:
            self.status_label.setText(f"Added patient: {name}")
            self.refresh_patients()
            log.info("Added patient name=%r patient_id=%s", name, patient_id)
        else:
            self.status_label.setText("Failed to add patient (may already exist).")
            log.warning("add_patient failed for name=%r (integrity?)", name)
    
    def refresh_patients(self):
        self.patient_dropdown.clear()
        patients = self.db.get_all_patients()
        for patient_id, name in patients:
            self.patient_dropdown.addItem(f"{name} (ID: {patient_id})", patient_id)
        if not patients:
            self.patient_dropdown.addItem("No patients available", None)

    def on_login_click(self):
        fitbit_auth.set_db(self.db)
        selected_id = self.patient_dropdown.currentData()  
        if selected_id is None:
            self.status_label.setText("Select a patient first.")
            return
        self.status_label.setText("Attempting to connect...")
        fitbit_auth.start_server()
        fitbit_auth.start_auth_flow(selected_id)
        self.status_label.setText("Fitbit auth launched in browser.")
        log.info("Fitbit OAuth flow started for patient_id=%s", selected_id)

    def open_graph_window(self):
        dialog = MetricSelectionDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            self.status_label.setText("Graph window launch cancelled.")
            return

        metric_selection = dialog.get_selection()
        if not metric_selection:
            self.status_label.setText("Select at least one metric to open a graph window.")
            return

        selected_id = self.patient_dropdown.currentData()
        if selected_id is None:
            self.status_label.setText("Select a patient on the main window before opening the graph.")
            return
        row = self.db.get_patient_info(selected_id)
        patient_name = row[1] if row else "Unknown"

        graph_window = GraphWindow(
            metric_selection, patient_id=selected_id, patient_name=patient_name
        )
        graph_window.show()
        self.graph_windows.append(graph_window)
        self.status_label.setText("Graph window opened.")
        log.info(
            "Opened GraphWindow patient_id=%s metrics=%s",
            selected_id,
            list(metric_selection.keys()),
        )

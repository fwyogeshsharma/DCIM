"""Tick Settings Dialog — control the DeviceStateStore background ticker."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QPushButton, QLabel, QScrollArea, QWidget,
    QTabWidget, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.device_state_store import DeviceStateStore


# ── group names for color-coded titles ──────────────────────────────────────
_GRP_NAMES: dict[str, str] = {
    "All Devices":                   "grp_all",
    "Sensor Devices Only":           "grp_sensor",
    "Sensor Devices":                "grp_sensor",
    "UPS Devices Only":              "grp_ups",
    "UPS Devices":                   "grp_ups",
    "PDU / Floor PDU Devices Only":  "grp_pdu",
    "PDU Devices":                   "grp_pdu",
    "Router / Firewall Devices Only":"grp_net",
    "Router / Firewall":             "grp_net",
}

# (group_title, [(flag_key, display_label, tooltip), ...])
_METRIC_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("All Devices", [
        ("cpu_usage",      "CPU Usage",
         "Random walk ±4 pp; 1% spike chance to >90% with brief or sustained recovery"),
        ("memory_used",    "Memory Used",
         "Random walk; 0.5% spike chance to >85% with brief or sustained recovery"),
        ("disk_used",      "Disk Used",
         "Slow growth-biased walk (log/tmp accumulation); capped 5–90% of disk total"),
        ("sys_uptime",     "System Uptime",
         "Advances by tick_interval each tick (stored as centiseconds)"),
        ("cpu_temp",       "CPU / ASIC Temperature",
         "Derived: 20 + 0.42 × cpu_usage ± 1 °C, clamped 20–95 °C"),
        ("inlet_temp",     "Chassis Inlet Temperature",
         "Derived: 18 + 0.12 × cpu_usage ± 0.5 °C, clamped 15–55 °C"),
        ("iface_octets",   "Interface Byte Counters",
         "in_octets / out_octets +5K–150K per tick on every UP interface"),
        ("iface_errors",   "Interface Error Counters",
         "in_errors +1 (10% chance) / out_errors +1 (5% chance) per UP interface"),
        ("iface_discards", "Interface Discard Counters",
         "in/out_discards — scale with congestion (cpu>70: +0–10; cpu>50: 30% chance +0–3)"),
        ("interface_flap", "Interface Flapping",
         "0.2% chance per connected interface goes DOWN; auto-recovers after 5 s"),
    ]),
    ("Sensor Devices Only", [
        ("humidity",  "Humidity",
         "±1.5 %RH random walk, clamped 10–90 %"),
        ("dewpoint",  "Dew Point",
         "Recalculated from inlet_temp + humidity each tick"),
        ("airflow",   "Airflow  (NetBotz models only)",
         "±0.15 m/s random walk, clamped 0.2–4.0 m/s"),
    ]),
    ("UPS Devices Only", [
        ("ups_status",           "UPS Status",
         "State machine: normal → on_battery (0.1%) → low_battery (8%) → normal (10%)"),
        ("ups_output_load",      "Output Load",
         "±3% walk; 0.5% spike chance to >90%"),
        ("ups_battery_status",   "Battery Hardware Status",
         "normal / failure (0.05%) / disconnected — recovers 15% per tick"),
        ("ups_input_voltage",    "Input Voltage",
         "±2 V walk; 0.3% spike outside 200–240 V range"),
        ("ups_input_frequency",  "Input Frequency",
         "±0.05 Hz walk; 0.2% spike outside 49.5–50.5 Hz"),
        ("ups_fan_status",       "Fan Status",
         "ok / failure — 0.1% failure chance per tick; recovers 15% per tick"),
        ("ups_charger_status",   "Charger Status",
         "ok / failure — 0.1% failure chance per tick; recovers 15% per tick"),
        ("ups_rectifier_status", "Rectifier Status",
         "ok / failure — 0.1% failure chance per tick; recovers 15% per tick"),
        ("ups_phase_status",     "Phase Status",
         "ok / failure — 0.1% failure chance per tick; recovers 15% per tick"),
    ]),
    ("PDU / Floor PDU Devices Only", [
        ("pdu_load",            "Load",
         "±3% walk; 0.4% spike chance to >80%"),
        ("pdu_voltage",         "Voltage",
         "±2 V walk; 0.3% spike outside 205–235 V"),
        ("pdu_power_factor",    "Power Factor",
         "±0.02 walk; 0.3% dip to <0.70"),
        ("pdu_phase_imbalance", "Phase Imbalance",
         "±1% walk; 0.3% spike to >20%"),
        ("pdu_outlet_status",   "Outlet Status",
         "on / off — 0.1% flip chance; recovers 30% per tick"),
        ("pdu_breaker_status",  "Breaker Status",
         "ok / tripped — 0.1% trip chance; recovers 25% per tick"),
        ("pdu_outlet_failure",  "Outlet Failure",
         "ok / failed — 0.1% failure chance; recovers 25% per tick"),
        ("pdu_smoke",           "Smoke Detection",
         "no / yes — 0.01% chance; clears 5% per tick"),
        ("pdu_outlet_current",  "Outlet Current",
         "±1 A walk; 0.3% spike to >20 A"),
        ("pdu_ground_fault",    "Ground Fault",
         "no / yes — 0.05% chance; clears 20% per tick"),
    ]),
    ("Router / Firewall Devices Only", [
        ("bgp_sessions", "BGP Sessions",
         "Peer state machine: established → idle (0.5%) → established (15%)"),
    ]),
]

# Limit rows — (key, label, kind, *kind_args)
# kind="num":   (abs_min, abs_max, step, decimals, suffix, default_min, default_max)
# kind="state": (options: list[str],)
_LIMIT_GROUPS: list[tuple[str, list]] = [
    ("All Devices", [
        ("cpu_usage",  "CPU Usage",   "num",  0,   100,  1,  0, " %",   0,   100),
        ("memory_pct", "Memory Used", "num",  0,   100,  1,  0, " %",   0,   100),
        ("disk_pct",   "Disk Used",   "num",  0,   100,  1,  0, " %",   0,   100),
        ("cpu_temp",   "CPU Temp",    "num", 20,    95, 0.5, 1, " °C", 20,    95),
        ("inlet_temp", "Inlet Temp",  "num", 15,    55, 0.5, 1, " °C", 15,    55),
    ]),
    ("Sensor Devices", [
        ("humidity", "Humidity", "num", 10,  90, 1.0, 1, " %",    10,   90),
        ("airflow",  "Airflow",  "num",  0,   5, 0.1, 2, " m/s", 0.2,  4.0),
    ]),
    ("UPS Devices", [
        ("ups_output_load",     "Output Load",    "num", 0,   100, 1.0, 1, " %",   0,  100),
        ("ups_input_voltage",   "Input Voltage",  "num", 200, 260, 1.0, 1, " V", 200,  240),
        ("ups_input_frequency", "Input Freq",     "num",  47,  53, 0.1, 2, " Hz", 49.5, 50.5),
        ("ups_status",          "UPS Status",     "state", ["normal", "on_battery", "low_battery"]),
        ("ups_battery_status",  "Battery Status", "state", ["normal", "failure", "disconnected"]),
        ("ups_fan_status",      "Fan Status",     "state", ["ok", "failure"]),
        ("ups_charger_status",  "Charger Status", "state", ["ok", "failure"]),
        ("ups_rectifier_status","Rectifier",      "state", ["ok", "failure"]),
        ("ups_phase_status",    "Phase Status",   "state", ["ok", "failure"]),
    ]),
    ("PDU Devices", [
        ("pdu_load",           "Load",           "num", 0,   100, 1.0, 1, " %",   0,  100),
        ("pdu_voltage",        "Voltage",        "num", 190, 250, 1.0, 1, " V", 205,  235),
        ("pdu_outlet_current", "Outlet Current", "num", 0,    30, 1.0, 1, " A",   0,   20),
        ("pdu_outlet_status",  "Outlet Status",  "state", ["on", "off"]),
        ("pdu_breaker_status", "Breaker Status", "state", ["ok", "tripped"]),
        ("pdu_outlet_failure", "Outlet Failure", "state", ["ok", "failed"]),
        ("pdu_smoke",          "Smoke Detect",   "state", ["no", "yes"]),
        ("pdu_ground_fault",   "Ground Fault",   "state", ["no", "yes"]),
    ]),
    ("Router / Firewall", [
        ("bgp_sessions", "BGP Sessions", "state", ["established", "idle"]),
    ]),
]

# ── palette constants ────────────────────────────────────────────────────────
_BG       = "#070c12"
_SURFACE  = "#0d1117"
_CARD     = "#0e1520"
_ELEVATED = "#141d27"
_BORDER   = "#1a2332"
_BORDER2  = "#243040"
_TEXT     = "#dde4ed"
_MUTED    = "#6b7a8d"
_FAINT    = "#2e3d4f"
_ACCENT   = "#06b6d4"   # electric cyan
_ACCENT_H = "#22d3ee"   # hover
_ACCENT_D = "#0891b2"   # pressed
_GREEN    = "#34d399"
_AMBER    = "#fbbf24"
_BLUE     = "#60a5fa"
_VIOLET   = "#a78bfa"
_RED_ERR  = "#f87171"

_STYLE = f"""
QDialog {{
    background: {_BG};
    color: {_TEXT};
}}

/* ── Header ── */
QWidget#dlg_header {{
    background: {_SURFACE};
    border-bottom: 1px solid {_BORDER};
}}

/* ── Tabs ── */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {_BORDER};
    background: transparent;
    margin-top: -1px;
}}
QTabWidget {{ background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {_MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 7px 20px 6px 20px;
    font-size: 8.5pt;
    letter-spacing: 0.4px;
}}
QTabBar::tab:selected {{
    color: {_ACCENT};
    border-bottom: 2px solid {_ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {_TEXT};
    border-bottom: 2px solid {_BORDER2};
}}

/* ── Scroll ── */
QScrollArea {{ background: transparent; border: none; }}
QWidget#scroll_content {{ background: transparent; }}
QScrollBar:vertical {{
    background: {_BG};
    width: 5px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_BORDER2};
    border-radius: 2px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {_MUTED};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ background: none; }}

/* ── Group boxes ── */
QGroupBox {{
    font-size: 7.5pt;
    font-weight: bold;
    letter-spacing: 0.6px;
    border: 1px solid {_BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 8px;
    background: {_CARD};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {_MUTED};
}}
QGroupBox#ticker_control {{
    border-top:    1px solid {_BORDER};
    border-right:  1px solid {_BORDER};
    border-bottom: 1px solid {_BORDER};
    border-left:   3px solid {_ACCENT};
}}
QGroupBox#ticker_control::title {{ color: {_ACCENT}; }}
QGroupBox#grp_all::title    {{ color: {_ACCENT};  }}
QGroupBox#grp_sensor::title {{ color: {_GREEN};   }}
QGroupBox#grp_ups::title    {{ color: {_AMBER};   }}
QGroupBox#grp_pdu::title    {{ color: {_BLUE};    }}
QGroupBox#grp_net::title    {{ color: {_VIOLET};  }}

/* ── Labels ── */
QLabel {{
    color: {_MUTED};
    background: transparent;
    font-size: 9pt;
}}

/* ── Checkboxes ── */
QCheckBox {{
    color: {_TEXT};
    spacing: 8px;
    font-size: 9pt;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 4px;
    border: 1.5px solid {_BORDER2};
    background: {_SURFACE};
}}
QCheckBox::indicator:hover {{
    border-color: {_MUTED};
    background: {_ELEVATED};
}}
QCheckBox::indicator:checked {{
    background: {_ACCENT};
    border-color: {_ACCENT};
    image: none;
}}
QCheckBox::indicator:checked:hover {{
    background: {_ACCENT_H};
    border-color: {_ACCENT_H};
}}
QCheckBox::indicator:disabled {{
    background: {_CARD};
    border-color: {_BORDER};
}}

/* ── Spin boxes ── */
QSpinBox, QDoubleSpinBox {{
    background: {_SURFACE};
    color: {_TEXT};
    border: 1px solid {_BORDER2};
    border-radius: 4px;
    padding: 3px 7px;
    font-size: 9pt;
    font-family: Consolas, "Courier New", monospace;
    selection-background-color: {_ACCENT_D};
    selection-color: #ffffff;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {_ACCENT};
    background: {_CARD};
}}
QSpinBox:hover:!focus, QDoubleSpinBox:hover:!focus {{
    border-color: {_MUTED};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {_FAINT};
    background: {_CARD};
    border-color: {_BORDER};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; height: 0; border: none;
}}

/* ── Combo boxes ── */
QComboBox {{
    background: {_SURFACE};
    color: {_TEXT};
    border: 1px solid {_BORDER2};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 9pt;
    font-family: Consolas, "Courier New", monospace;
}}
QComboBox:focus {{ border-color: {_ACCENT}; background: {_CARD}; }}
QComboBox:hover:!focus {{ border-color: {_MUTED}; }}
QComboBox:disabled {{ color: {_FAINT}; background: {_CARD}; border-color: {_BORDER}; }}
QComboBox::drop-down {{
    border: none;
    width: 20px;
    subcontrol-origin: padding;
    subcontrol-position: right center;
}}
QComboBox QAbstractItemView {{
    background: {_ELEVATED};
    color: {_TEXT};
    border: 1px solid {_BORDER2};
    border-radius: 4px;
    selection-background-color: {_BORDER2};
    selection-color: {_ACCENT};
    outline: none;
    padding: 2px;
}}

/* ── Buttons ── */
QPushButton {{
    border-radius: 5px;
    font-size: 9pt;
    padding: 5px 18px;
    color: {_MUTED};
    border: 1px solid {_BORDER2};
    background: transparent;
    letter-spacing: 0.3px;
}}
QPushButton:hover:enabled {{
    color: {_TEXT};
    background: {_ELEVATED};
    border-color: {_MUTED};
}}
QPushButton:pressed {{ background: {_CARD}; }}
QPushButton:disabled {{ color: {_FAINT}; border-color: {_BORDER}; }}
QPushButton#btn_apply {{
    background: {_ACCENT};
    border-color: {_ACCENT};
    color: #ffffff;
    font-weight: bold;
    letter-spacing: 0.4px;
    padding: 5px 22px;
}}
QPushButton#btn_apply:hover:enabled {{
    background: {_ACCENT_H};
    border-color: {_ACCENT_H};
}}
QPushButton#btn_apply:pressed {{ background: {_ACCENT_D}; }}
QPushButton#btn_small {{
    padding: 3px 12px;
    font-size: 8pt;
    letter-spacing: 0.2px;
}}
"""


class TickSettingsDialog(QDialog):
    def __init__(self, state_store: "DeviceStateStore", parent=None):
        super().__init__(parent)
        self._store = state_store
        self.setWindowTitle("Metrics Tick Settings")
        self.setMinimumWidth(540)
        self.setMinimumHeight(600)
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Header bar ────────────────────────────────────────────────────
        header = QWidget()
        header.setObjectName("dlg_header")
        header.setFixedHeight(42)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(8)

        title_lbl = QLabel("METRICS TICK SETTINGS")
        title_lbl.setFont(QFont("Consolas", 8))
        title_lbl.setStyleSheet(f"color: {_MUTED}; letter-spacing: 2px; font-weight: bold; font-size: 8pt;")
        hl.addWidget(title_lbl)
        hl.addStretch()

        # Status dot + label
        self._status_dot = QFrame()
        self._status_dot.setFixedSize(7, 7)
        self._status_dot.setStyleSheet(f"border-radius: 3px; background: {_MUTED};")

        self._status_lbl = QLabel("not started")
        self._status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 8pt; font-family: Consolas, monospace;")

        hl.addWidget(self._status_dot)
        hl.addWidget(self._status_lbl)

        root.addWidget(header)

        # ── Tab widget ─────────────────────────────────────────────────────
        tab_wrapper = QWidget()
        tab_layout = QVBoxLayout(tab_wrapper)
        tab_layout.setContentsMargins(14, 10, 14, 0)
        tab_layout.setSpacing(0)

        tabs = QTabWidget()
        metrics_w = QWidget()
        self._build_metrics_tab(metrics_w)
        tabs.addTab(metrics_w, "Metrics")

        limits_w = QWidget()
        self._build_limits_tab(limits_w)
        tabs.addTab(limits_w, "Limits")

        tab_layout.addWidget(tabs)
        root.addWidget(tab_wrapper, stretch=1)

        # ── Button row ───────────────────────────────────────────────────
        btn_bar = QWidget()
        btn_bar.setStyleSheet(f"background: {_SURFACE}; border-top: 1px solid {_BORDER};")
        bl = QHBoxLayout(btn_bar)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(8)

        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setObjectName("btn_apply")
        self._btn_apply.clicked.connect(self._apply)
        self._btn_apply.setFixedHeight(30)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setFixedHeight(30)

        bl.addStretch()
        bl.addWidget(self._btn_apply)
        bl.addWidget(btn_close)
        root.addWidget(btn_bar)

    # ------------------------------------------------------------------ #
    #  Tab 1: Metrics                                                      #
    # ------------------------------------------------------------------ #

    def _build_metrics_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 10, 0, 0)

        # ── Ticker control ────────────────────────────────────────────────
        ticker_box = QGroupBox("Ticker Control")
        ticker_box.setObjectName("ticker_control")
        tl = QVBoxLayout(ticker_box)
        tl.setSpacing(10)
        tl.setContentsMargins(14, 10, 14, 12)

        self._chk_enabled = QCheckBox("Ticker enabled")
        self._chk_enabled.setFont(QFont("Segoe UI", 9))
        self._chk_enabled.setStyleSheet(f"color: {_TEXT}; font-size: 9pt;")
        tl.addWidget(self._chk_enabled)

        # separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_BORDER}; background: {_BORDER}; max-height: 1px;")
        tl.addWidget(sep)

        interval_row = QHBoxLayout()
        interval_row.setSpacing(8)
        lbl_iv = QLabel("Interval")
        lbl_iv.setStyleSheet(f"color: {_MUTED}; font-size: 9pt;")
        lbl_iv.setFixedWidth(56)
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 3600)
        self._spin_interval.setSuffix(" s")
        self._spin_interval.setFixedWidth(84)
        hint = QLabel("1 – 3600 s")
        hint.setStyleSheet(f"color: {_FAINT}; font-size: 8pt; font-family: Consolas, monospace;")
        interval_row.addWidget(lbl_iv)
        interval_row.addWidget(self._spin_interval)
        interval_row.addWidget(hint)
        interval_row.addStretch()
        tl.addLayout(interval_row)
        layout.addWidget(ticker_box)

        # ── Scrollable metrics list ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(6)
        scroll_layout.setContentsMargins(0, 2, 4, 2)

        self._metric_checks: dict[str, QCheckBox] = {}

        for group_title, rows in _METRIC_GROUPS:
            grp = QGroupBox(group_title)
            grp.setObjectName(_GRP_NAMES.get(group_title, ""))
            gl = QVBoxLayout(grp)
            gl.setSpacing(2)
            gl.setContentsMargins(12, 8, 12, 10)
            for key, label, tooltip in rows:
                chk = QCheckBox(label)
                chk.setToolTip(tooltip)
                chk.setStyleSheet(f"color: {_TEXT}; font-size: 9pt; spacing: 8px;")
                gl.addWidget(chk)
                self._metric_checks[key] = chk
            scroll_layout.addWidget(grp)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        # ── Select all / none ─────────────────────────────────────────────
        sel_row = QHBoxLayout()
        sel_row.setContentsMargins(0, 4, 0, 4)
        lbl_sel = QLabel("Toggle all:")
        lbl_sel.setStyleSheet(f"color: {_FAINT}; font-size: 8pt;")
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        for b in (btn_all, btn_none):
            b.setObjectName("btn_small")
            b.setFixedWidth(60)
            b.setFixedHeight(24)
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(lbl_sel)
        sel_row.addStretch()
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        layout.addLayout(sel_row)

    # ------------------------------------------------------------------ #
    #  Tab 2: Limits                                                       #
    # ------------------------------------------------------------------ #

    def _build_limits_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 8, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(6)
        scroll_layout.setContentsMargins(0, 0, 4, 2)

        # hint bar
        hint_bar = QWidget()
        hint_bar.setStyleSheet(
            f"background: {_ELEVATED}; border: 1px solid {_BORDER}; border-radius: 5px;"
        )
        hbl = QHBoxLayout(hint_bar)
        hbl.setContentsMargins(10, 7, 10, 7)
        hint_icon = QLabel("ⓘ")
        hint_icon.setStyleSheet(f"color: {_ACCENT}; font-size: 10pt; background: transparent; border: none;")
        hint_text = QLabel(
            "Check a row to constrain that metric. Numeric: clamps the walk output. State: forces a fixed value."
        )
        hint_text.setStyleSheet(f"color: {_MUTED}; font-size: 8pt; background: transparent; border: none;")
        hint_text.setWordWrap(True)
        hbl.addWidget(hint_icon)
        hbl.addWidget(hint_text, stretch=1)
        scroll_layout.addWidget(hint_bar)

        self._limit_checks: dict[str, QCheckBox] = {}
        self._limit_min:    dict[str, QDoubleSpinBox] = {}
        self._limit_max:    dict[str, QDoubleSpinBox] = {}
        self._limit_combo:  dict[str, QComboBox] = {}

        for group_title, rows in _LIMIT_GROUPS:
            grp = QGroupBox(group_title)
            grp.setObjectName(_GRP_NAMES.get(group_title, ""))
            gl = QVBoxLayout(grp)
            gl.setSpacing(3)
            gl.setContentsMargins(12, 8, 12, 10)

            for row in rows:
                key, label, kind = row[0], row[1], row[2]

                chk = QCheckBox()
                chk.setFixedSize(18, 18)
                self._limit_checks[key] = chk

                if kind == "num":
                    _, _, _, abs_min, abs_max, step, decimals, suffix, def_min, def_max = row
                    row_w = self._make_num_row(
                        chk, label, abs_min, abs_max, step, decimals, suffix, def_min, def_max
                    )
                    spin_min = row_w.property("spin_min")
                    spin_max = row_w.property("spin_max")
                    self._limit_min[key] = spin_min
                    self._limit_max[key] = spin_max
                    chk.toggled.connect(lambda v, sm=spin_min, sx=spin_max: (
                        sm.setEnabled(v), sx.setEnabled(v)
                    ))
                    spin_min.setEnabled(False)
                    spin_max.setEnabled(False)
                    gl.addWidget(row_w)

                else:
                    options = row[3]
                    row_w = self._make_state_row(chk, label, options)
                    combo = row_w.property("combo")
                    self._limit_combo[key] = combo
                    chk.toggled.connect(lambda v, c=combo: c.setEnabled(v))
                    combo.setEnabled(False)
                    gl.addWidget(row_w)

            scroll_layout.addWidget(grp)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

    def _make_num_row(
        self, chk: QCheckBox, label: str,
        abs_min: float, abs_max: float, step: float, decimals: int, suffix: str,
        def_min: float, def_max: float,
    ) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(8)

        hl.addWidget(chk)

        lbl = QLabel(label)
        lbl.setFixedWidth(114)
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 9pt; background: transparent;")
        hl.addWidget(lbl)

        # Min side
        lbl_min = QLabel("min")
        lbl_min.setStyleSheet(f"color: {_FAINT}; font-size: 7.5pt; font-family: Consolas, monospace; background: transparent;")
        lbl_min.setFixedWidth(20)
        spin_min = QDoubleSpinBox()
        spin_min.setRange(abs_min, abs_max)
        spin_min.setSingleStep(step)
        spin_min.setDecimals(decimals)
        spin_min.setValue(def_min)
        spin_min.setSuffix(suffix)
        spin_min.setFixedWidth(88)

        lbl_sep = QLabel("→")
        lbl_sep.setStyleSheet(f"color: {_FAINT}; font-size: 9pt; background: transparent;")
        lbl_sep.setAlignment(Qt.AlignCenter)
        lbl_sep.setFixedWidth(16)

        # Max side
        lbl_max = QLabel("max")
        lbl_max.setStyleSheet(f"color: {_FAINT}; font-size: 7.5pt; font-family: Consolas, monospace; background: transparent;")
        lbl_max.setFixedWidth(22)
        spin_max = QDoubleSpinBox()
        spin_max.setRange(abs_min, abs_max)
        spin_max.setSingleStep(step)
        spin_max.setDecimals(decimals)
        spin_max.setValue(def_max)
        spin_max.setSuffix(suffix)
        spin_max.setFixedWidth(88)

        hl.addWidget(lbl_min)
        hl.addWidget(spin_min)
        hl.addWidget(lbl_sep)
        hl.addWidget(lbl_max)
        hl.addWidget(spin_max)
        hl.addStretch()

        w.setProperty("spin_min", spin_min)
        w.setProperty("spin_max", spin_max)
        return w

    def _make_state_row(
        self, chk: QCheckBox, label: str, options: list[str]
    ) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(8)

        hl.addWidget(chk)

        lbl = QLabel(label)
        lbl.setFixedWidth(114)
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 9pt; background: transparent;")
        hl.addWidget(lbl)

        lbl_force = QLabel("force →")
        lbl_force.setStyleSheet(
            f"color: {_FAINT}; font-size: 8pt; font-family: Consolas, monospace; background: transparent;"
        )
        combo = QComboBox()
        combo.addItems(options)
        combo.setFixedWidth(144)
        combo.setFixedHeight(26)

        hl.addWidget(lbl_force)
        hl.addWidget(combo)
        hl.addStretch()

        w.setProperty("combo", combo)
        return w

    # ------------------------------------------------------------------ #
    #  Load / Apply                                                        #
    # ------------------------------------------------------------------ #

    def _load(self):
        running = self._store.is_running()
        paused  = self._store.is_paused()
        self._chk_enabled.setChecked(running and not paused)
        self._spin_interval.setValue(int(self._store._tick_interval))

        for key, chk in self._metric_checks.items():
            chk.setChecked(self._store.metric_flags.get(key, True))

        for key, lim in self._store.metric_limits.items():
            if key in self._limit_checks:
                self._limit_checks[key].setChecked(lim.get("enabled", False))
            if key in self._limit_min:
                self._limit_min[key].setValue(lim.get("min", 0))
                self._limit_max[key].setValue(lim.get("max", 100))
                enabled = lim.get("enabled", False)
                self._limit_min[key].setEnabled(enabled)
                self._limit_max[key].setEnabled(enabled)
            if key in self._limit_combo:
                lock = lim.get("lock", "")
                idx = self._limit_combo[key].findText(lock)
                if idx >= 0:
                    self._limit_combo[key].setCurrentIndex(idx)
                self._limit_combo[key].setEnabled(lim.get("enabled", False))

        self._update_status()

    def _apply(self):
        store = self._store
        store.set_tick_interval(self._spin_interval.value())
        if store.is_running():
            store.set_paused(not self._chk_enabled.isChecked())
        for key, chk in self._metric_checks.items():
            store.metric_flags[key] = chk.isChecked()

        for key, chk in self._limit_checks.items():
            lim = store.metric_limits.get(key)
            if lim is None:
                continue
            lim["enabled"] = chk.isChecked()
            if key in self._limit_min:
                lim["min"] = self._limit_min[key].value()
                lim["max"] = self._limit_max[key].value()
            if key in self._limit_combo:
                lim["lock"] = self._limit_combo[key].currentText()

        self._update_status()
        txt = self._status_lbl.text()
        if "applied" not in txt:
            self._status_lbl.setText(txt + "  · applied")

    def _update_status(self):
        store = self._store
        if not store.is_running():
            dot, txt, color = _FAINT, "not started", _MUTED
        elif store.is_paused():
            dot, txt, color = _AMBER, "paused", _AMBER
        else:
            ivl = int(store._tick_interval)
            dot, txt, color = _GREEN, f"running  ·  {ivl} s", _GREEN
        self._status_dot.setStyleSheet(f"border-radius: 3px; background: {dot};")
        self._status_lbl.setText(txt)
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 8pt; font-family: Consolas, monospace;"
        )

    def _set_all(self, state: bool):
        for chk in self._metric_checks.values():
            chk.setChecked(state)

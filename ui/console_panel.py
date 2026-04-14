"""
Console Panel — separate log tabs for SNMP and gNMI activity.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTextEdit, QTabWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor

_COLORS = {
    "info":    "#58a6ff",
    "success": "#3fb950",
    "warning": "#d29922",
    "warn":    "#d29922",
    "error":   "#f85149",
}

_CONSOLE_STYLE = """
    QTextEdit {
        background-color: #0d1117;
        color: #58a6ff;
        border: none;
    }
    QScrollBar:vertical {
        background: #0d1117; width: 10px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #30363d; border-radius: 5px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #58a6ff; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_TAB_STYLE = """
    QTabWidget::pane {
        border: none;
        background: #0d1117;
    }
    QTabBar::tab {
        background: #161b22;
        color: #8b949e;
        padding: 4px 12px;
        border: none;
        border-right: 1px solid #30363d;
        font-size: 8pt;
    }
    QTabBar::tab:selected {
        background: #21262d;
        color: #e6edf3;
        border-bottom: 2px solid #58a6ff;
    }
    QTabBar::tab:hover:!selected {
        background: #1c2128;
        color: #cdd9e5;
    }
"""

_CLEAR_BTN_STYLE = (
    "QPushButton { background: #30363d; color: #8b949e; border: none; "
    "border-radius: 3px; font-size: 8pt; padding: 0 4px; } "
    "QPushButton:hover { background: #3d444d; color: #e6edf3; }"
)


def _make_tab(label: str) -> tuple[QWidget, QTextEdit]:
    """Build one tab's content: a thin clear-bar + log text area."""
    widget = QWidget()
    vlay = QVBoxLayout(widget)
    vlay.setContentsMargins(0, 0, 0, 0)
    vlay.setSpacing(0)

    # Clear bar
    bar = QWidget()
    bar.setFixedHeight(24)
    bar.setStyleSheet("background: #161b22; border-bottom: 1px solid #30363d;")
    row = QHBoxLayout(bar)
    row.setContentsMargins(8, 0, 8, 0)
    lbl = QLabel(label)
    lbl.setFont(QFont("Arial", 8))
    lbl.setStyleSheet("color: #8b949e; background: transparent; border: none;")
    row.addWidget(lbl)
    row.addStretch()
    clr = QPushButton("Clear")
    clr.setFixedHeight(18)
    clr.setFixedWidth(44)
    clr.setStyleSheet(_CLEAR_BTN_STYLE)
    row.addWidget(clr)
    vlay.addWidget(bar)

    # Text area
    te = QTextEdit()
    te.setReadOnly(True)
    te.setFont(QFont("Consolas", 9))
    te.setStyleSheet(_CONSOLE_STYLE)
    vlay.addWidget(te)

    clr.clicked.connect(te.clear)
    return widget, te


class ConsolePanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet(
            "background: #21262d; border-bottom: 1px solid #30363d;"
        )
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("Console")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        layout.addWidget(title_bar)

        # ── Tab widget ────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_STYLE)
        self._tabs.setDocumentMode(True)

        snmp_widget, self._snmp_te = _make_tab("SNMP Simulator log")
        gnmi_widget, self._gnmi_te = _make_tab("gNMI Simulator log")

        self._tabs.addTab(snmp_widget, "SNMP Simulator")
        self._tabs.addTab(gnmi_widget, "gNMI Simulator")

        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def log(self, message: str, level: str = "info"):
        """Append a message to the SNMP Simulator tab."""
        color = _COLORS.get(level, "#58a6ff")
        self._snmp_te.append(f'<span style="color:{color};">{message}</span>')
        self._snmp_te.moveCursor(QTextCursor.End)

    def log_gnmi(self, message: str, level: str = "info"):
        """Append a message to the gNMI Simulator tab."""
        color = _COLORS.get(level, "#58a6ff")
        self._gnmi_te.append(f'<span style="color:{color};">{message}</span>')
        self._gnmi_te.moveCursor(QTextCursor.End)

    def clear_log(self):
        """Clear the currently visible tab."""
        if self._tabs.currentIndex() == 0:
            self._snmp_te.clear()
        else:
            self._gnmi_te.clear()
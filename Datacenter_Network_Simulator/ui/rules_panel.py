"""
Rules Panel — view and manage the SNMP trap rule engine.

Displays the active rule list with per-rule statistics and provides
controls to enable/disable individual rules, import/export JSON, and
toggle the rule engine globally.
"""
from __future__ import annotations

import json
from typing import List, Optional, TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSizePolicy, QFileDialog, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from core.trap_definitions import SEVERITY_COLOR

if TYPE_CHECKING:
    from core.rule_engine import Rule, RuleEngine

# ── Rule categories (ordered; rules not listed fall into "Other") ──────────────

_CATEGORIES: List[tuple] = [
    ("Interface / Link", [
        "LinkDown", "LinkUp", "LinkFlap",
        "OOBSwitchLinkDown", "OOBSwitchLinkUp",
    ]),
    ("CPU", [
        "HighCPU", "HighCPUSustained", "CPUNormal",
    ]),
    ("Memory", [
        "HighMemory", "MemoryNormal",
    ]),
    ("Temperature", [
        "HighTemperature", "TemperatureNormal", "CriticalCPUAndTemp",
    ]),
    ("Power / UPS", [
        "UPSOnBattery", "UPSLowBattery",
    ]),
    ("Routing", [
        "BGPSessionDown",
    ]),
    ("Infrastructure", [
        "RackFailure",
    ]),
    ("Env. Sensors — Temperature", [
        "SensorAmbientTempHigh", "SensorAmbientTempCritical", "SensorAmbientTempNormal",
    ]),
    ("Env. Sensors — Humidity", [
        "SensorHighHumidity", "SensorCriticalHumidity",
        "SensorLowHumidity", "SensorHumidityNormal",
    ]),
    ("Env. Sensors — Dew Point", [
        "SensorHighDewPoint", "SensorDewPointNormal",
    ]),
    ("Env. Sensors — Airflow", [
        "SensorHighAirflow", "SensorLowAirflow", "SensorAirflowNormal",
    ]),
]


# ── Severity badge ─────────────────────────────────────────────────────────────

def _sev_style(severity: str) -> str:
    color = SEVERITY_COLOR.get(severity, "#888")
    return (
        f"background:{color}; color:white; border-radius:3px;"
        f" padding:1px 5px; font-size:9px; font-weight:bold;"
    )


# ── Rules Panel ────────────────────────────────────────────────────────────────

class RulesPanel(QWidget):
    sig_rule_engine_toggled = Signal(bool)   # enable/disable rule engine
    sig_rule_toggled        = Signal(str, bool)  # (rule_name, enabled)
    sig_rules_imported      = Signal(list)   # list[Rule]

    _COL_ENABLED  = 0
    _COL_NAME     = 1
    _COL_OID      = 2
    _COL_SEV      = 3
    _COL_FIRED    = 4
    _COL_LAST     = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rule_engine: Optional["RuleEngine"] = None
        self._fired_cache: dict = {}          # rule_name → (fired_count, last_ts_str)
        self._cat_rows: set = set()           # row indices that are category headers
        self._cat_expanded: dict = {}         # label → bool  (persists across refreshes)
        self._cat_row_to_label: dict = {}     # row_index → label
        self._cat_label_to_children: dict = {}# label → [child_row, ...]
        self._cat_label_to_detail: dict = {}  # label → detail string
        self._build_ui()

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet("background:#21262d; border-bottom:1px solid #30363d;")
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(8, 0, 8, 0)
        lbl = QLabel("Rule Engine")
        lbl.setFont(QFont("Arial", 9, QFont.Bold))
        lbl.setStyleSheet("color:#e6edf3; background:transparent; border:none;")
        tb.addWidget(lbl)
        tb.addStretch()
        self._engine_toggle = QPushButton("● Disabled")
        self._engine_toggle.setCheckable(True)
        self._engine_toggle.setEnabled(False)
        self._engine_toggle.setFixedWidth(90)
        self._engine_toggle.setStyleSheet(self._toggle_style(False))
        self._engine_toggle.toggled.connect(self._on_engine_toggled)
        tb.addWidget(self._engine_toggle)
        root.addWidget(title_bar)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(6)
        root.addWidget(content, stretch=1)

        # Stats row
        stats = QHBoxLayout()
        self._lbl_rules  = QLabel("Rules: 0")
        self._lbl_rules.setFont(QFont("Consolas", 8))
        self._lbl_rules.setStyleSheet("color:#8b949e;")
        self._lbl_fired  = QLabel("Fired: 0")
        self._lbl_fired.setFont(QFont("Consolas", 8))
        self._lbl_fired.setStyleSheet("color:#3fb950;")
        stats.addWidget(self._lbl_rules)
        stats.addStretch()
        stats.addWidget(self._lbl_fired)
        cl.addLayout(stats)

        # Rule table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "On", "Rule Name", "Trap OID", "Sev", "Fired", "Last Fired",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setDefaultSectionSize(80)
        self._table.setColumnWidth(self._COL_ENABLED, 30)
        self._table.setColumnWidth(self._COL_NAME,   150)
        self._table.setColumnWidth(self._COL_OID,    175)
        self._table.setColumnWidth(self._COL_SEV,     46)
        self._table.setColumnWidth(self._COL_FIRED,   46)
        self._table.setColumnWidth(self._COL_LAST,    90)
        hdr.setStretchLastSection(True)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setFont(QFont("Consolas", 8))
        self._table.setMinimumHeight(120)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._table.setStyleSheet("""
            QTableWidget {
                background:#161b22; color:#e6edf3;
                border:1px solid #30363d;
                alternate-background-color:#0d1117;
                gridline-color:#30363d;
            }
            QHeaderView::section {
                background:#21262d; color:#8b949e;
                padding:3px; border:none;
                border-bottom:1px solid #30363d;
            }
            QTableWidget::item:selected { background:#1f6feb; }
        """)
        self._table.setMouseTracking(True)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellEntered.connect(self._on_cell_entered)
        cl.addWidget(self._table, stretch=1)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._btn_enable_all  = QPushButton("Enable All")
        self._btn_disable_all = QPushButton("Disable All")
        self._btn_export      = QPushButton("Export JSON")
        self._btn_import      = QPushButton("Import JSON")
        self._btn_refresh     = QPushButton("Refresh")
        for btn in (self._btn_enable_all, self._btn_disable_all,
                    self._btn_export, self._btn_import, self._btn_refresh):
            btn.setStyleSheet(self._btn_style())
            btn_row.addWidget(btn)
        cl.addLayout(btn_row)

        self._btn_enable_all.clicked.connect(self._on_enable_all)
        self._btn_disable_all.clicked.connect(self._on_disable_all)
        self._btn_export.clicked.connect(self._on_export)
        self._btn_import.clicked.connect(self._on_import)
        self._btn_refresh.clicked.connect(self.refresh)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_rule_engine(self, engine: "RuleEngine"):
        self._rule_engine = engine
        self.refresh()

    def refresh(self):
        if self._rule_engine is None:
            return
        rules = self._rule_engine.get_rules()
        self._populate_table(rules)
        self._lbl_rules.setText(f"Rules: {len(rules)}")

    def reset_stats(self):
        """Clear all fired counts and last-fired timestamps from the table and cache."""
        self._fired_cache.clear()
        self._lbl_fired.setText("Fired: 0")
        for row in range(self._table.rowCount()):
            if row in self._cat_rows:
                continue
            fired_item = self._table.item(row, self._COL_FIRED)
            last_item  = self._table.item(row, self._COL_LAST)
            if fired_item:
                fired_item.setText("0")
            if last_item:
                last_item.setText("—")

    def update_stats(self, fired_total: int):
        self._lbl_fired.setText(f"Fired: {fired_total}")

    def update_rule_stats(self, rule_name: str, fired: int, last_ts: str):
        self._fired_cache[rule_name] = (fired, last_ts)
        for row in range(self._table.rowCount()):
            if row in self._cat_rows:
                continue
            name_item = self._table.item(row, self._COL_NAME)
            if name_item and name_item.text() == rule_name:
                self._table.item(row, self._COL_FIRED).setText(str(fired))
                self._table.item(row, self._COL_LAST).setText(last_ts)
                break

    # ── Internal ───────────────────────────────────────────────────────────────

    def _populate_table(self, rules: list):
        self._table.setRowCount(0)
        self._cat_rows.clear()
        self._cat_row_to_label.clear()
        self._cat_label_to_children.clear()
        self._cat_label_to_detail.clear()

        rule_map = {r.rule_name: r for r in rules}
        placed: set = set()

        ordered: List[tuple] = []
        for cat_label, names in _CATEGORIES:
            cat_rules = [rule_map[n] for n in names if n in rule_map]
            if cat_rules:
                ordered.append((cat_label, cat_rules))
                placed.update(n for n in names if n in rule_map)

        leftover = [r for r in rules if r.rule_name not in placed]
        if leftover:
            ordered.append(("Other", leftover))

        for cat_label, cat_rules in ordered:
            # Preserve expansion state across refreshes; default = expanded
            if cat_label not in self._cat_expanded:
                self._cat_expanded[cat_label] = True
            expanded = self._cat_expanded[cat_label]

            # Build detail string stored for later arrow updates
            n_rules = len(cat_rules)
            n_recovery = sum(1 for r in cat_rules if r.is_recovery)
            detail = f"  {n_rules} rule{'s' if n_rules != 1 else ''}"
            if n_recovery:
                detail += f"  ·  {n_recovery} recovery"
            self._cat_label_to_detail[cat_label] = detail

            # ── Category header row ───────────────────────────────────────────
            hdr_row = self._table.rowCount()
            self._table.insertRow(hdr_row)
            self._cat_rows.add(hdr_row)
            self._cat_row_to_label[hdr_row] = cat_label
            self._table.setSpan(hdr_row, 0, 1, 6)
            arrow = "▾" if expanded else "▸"
            hdr_item = QTableWidgetItem(f"  {arrow}  {cat_label}{detail}")
            hdr_item.setFont(QFont("Arial", 8, QFont.Bold))
            hdr_item.setForeground(QColor("#58a6ff"))
            hdr_item.setBackground(QColor("#1c2128"))
            hdr_item.setFlags(Qt.ItemIsEnabled)
            hdr_item.setToolTip("Click to collapse / expand")
            self._table.setItem(hdr_row, 0, hdr_item)
            self._table.setRowHeight(hdr_row, 20)

            # ── Rule rows ─────────────────────────────────────────────────────
            children: List[int] = []
            for rule in cat_rules:
                row = self._table.rowCount()
                self._table.insertRow(row)
                children.append(row)
                self._table.setRowHidden(row, not expanded)

                cb = QCheckBox()
                cb.setChecked(rule.enabled)
                cb.setStyleSheet("QCheckBox { margin-left:6px; }")
                rname = rule.rule_name
                cb.toggled.connect(lambda checked, n=rname: self.sig_rule_toggled.emit(n, checked))
                self._table.setCellWidget(row, self._COL_ENABLED, cb)

                name_item = QTableWidgetItem(rule.rule_name)
                if rule.is_recovery:
                    name_item.setForeground(QColor("#8b949e"))
                self._table.setItem(row, self._COL_NAME, name_item)

                oid_item = QTableWidgetItem(rule.trap_oid)
                oid_item.setToolTip(rule.trap_oid)
                self._table.setItem(row, self._COL_OID, oid_item)

                sev_item = QTableWidgetItem(rule.severity[:4])
                color = SEVERITY_COLOR.get(rule.severity, "#888")
                sev_item.setForeground(QColor(color))
                sev_item.setFont(QFont("Consolas", 8, QFont.Bold))
                self._table.setItem(row, self._COL_SEV, sev_item)

                cached_fired, cached_ts = self._fired_cache.get(rule.rule_name, (0, "—"))
                self._table.setItem(row, self._COL_FIRED, QTableWidgetItem(str(cached_fired)))
                self._table.setItem(row, self._COL_LAST,  QTableWidgetItem(cached_ts))

            self._cat_label_to_children[cat_label] = children

    def _on_cell_clicked(self, row: int, col: int):
        """Toggle collapse/expand when a category header row is clicked."""
        if row not in self._cat_rows:
            return
        label = self._cat_row_to_label.get(row)
        if label is None:
            return
        expanded = not self._cat_expanded.get(label, True)
        self._cat_expanded[label] = expanded
        for child_row in self._cat_label_to_children.get(label, []):
            self._table.setRowHidden(child_row, not expanded)
        item = self._table.item(row, 0)
        if item:
            arrow = "▾" if expanded else "▸"
            detail = self._cat_label_to_detail.get(label, "")
            item.setText(f"  {arrow}  {label}{detail}")

    def _on_cell_entered(self, row: int, col: int):
        """Show pointer cursor over category headers to hint they are clickable."""
        if row in self._cat_rows:
            self._table.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self._table.viewport().setCursor(Qt.ArrowCursor)

    def set_engine_active(self, active: bool):
        """Update visual state only — does NOT emit any signal."""
        self._engine_toggle.blockSignals(True)
        self._engine_toggle.setChecked(active)
        self._engine_toggle.setText("● Active" if active else "● Disabled")
        self._engine_toggle.setStyleSheet(self._toggle_style(active))
        self._engine_toggle.blockSignals(False)

    def set_rule_engine_available(self, available: bool):
        """Enable or disable the toggle button (grayed out when SNMP sim is not running)."""
        self._engine_toggle.setEnabled(available)
        if not available:
            self.set_engine_active(False)

    def _on_engine_toggled(self, checked: bool):
        self._engine_toggle.setText("● Active" if checked else "● Disabled")
        self._engine_toggle.setStyleSheet(self._toggle_style(checked))
        self.sig_rule_engine_toggled.emit(checked)

    def _on_enable_all(self):
        if self._rule_engine:
            for rule in self._rule_engine.get_rules():
                self._rule_engine.enable_rule(rule.rule_name, True)
            self.refresh()

    def _on_disable_all(self):
        if self._rule_engine:
            for rule in self._rule_engine.get_rules():
                self._rule_engine.enable_rule(rule.rule_name, False)
            self.refresh()

    def _on_export(self):
        if self._rule_engine is None:
            return
        from core.trap_rules import rules_to_json
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Rules", "trap_rules.json", "JSON Files (*.json)"
        )
        if path:
            try:
                rules = self._rule_engine.get_rules()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(rules_to_json(rules))
                QMessageBox.information(self, "Export", f"Saved {len(rules)} rules to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Rules", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            from core.trap_rules import load_rules
            rules = load_rules(path)
            self.sig_rules_imported.emit(rules)
            QMessageBox.information(self, "Import", f"Imported {len(rules)} rules")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    # ── Styles ──────────────────────────────────────────────────────────────────

    def _toggle_style(self, active: bool) -> str:
        if active:
            return ("QPushButton { background:#238636; color:white; border:none; "
                    "border-radius:4px; padding:3px 8px; font-size:9px; font-weight:bold; } "
                    "QPushButton:hover { background:#2ea043; }")
        return ("QPushButton { background:#30363d; color:#8b949e; border:none; "
                "border-radius:4px; padding:3px 8px; font-size:9px; } "
                "QPushButton:hover { background:#3d444d; }")

    def _btn_style(self) -> str:
        return ("QPushButton { background:#21262d; color:#e6edf3; "
                "border:1px solid #30363d; border-radius:4px; padding:4px 6px; "
                "font-size:8pt; } "
                "QPushButton:hover { background:#30363d; } "
                "QPushButton:pressed { background:#0d1117; }")
"""
Topology Canvas - QGraphicsView/Scene based drag-drop topology editor.
"""
from __future__ import annotations
import math
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsTextItem,
    QGraphicsRectItem, QMenu, QInputDialog,
    QWidget, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QFrame,
)
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QObject, QLineF, QTimer, QPoint
from PySide6.QtGui import (
    QPen, QBrush, QColor, QPainter, QFont, QLinearGradient,
    QPolygonF, QPainterPath, QPainterPathStroker,
)

from core.device_manager import Device, DeviceType
from core.device_models import DEVICE_MODELS


# ------------------------------------------------------------------ #
#  Color scheme                                                        #
# ------------------------------------------------------------------ #
DEVICE_COLORS = {
    DeviceType.ROUTER: {
        "fill": QColor("#2980b9"),
        "border": QColor("#1a5276"),
        "text": QColor("white"),
        "icon": "R",
    },
    DeviceType.SWITCH: {
        "fill": QColor("#27ae60"),
        "border": QColor("#145a32"),
        "text": QColor("white"),
        "icon": "S",
    },
    DeviceType.SERVER: {
        "fill": QColor("#8e44ad"),
        "border": QColor("#4a235a"),
        "text": QColor("white"),
        "icon": "SRV",
    },
    DeviceType.FIREWALL: {
        "fill": QColor("#e67e22"),
        "border": QColor("#784212"),
        "text": QColor("white"),
        "icon": "FW",
    },
    DeviceType.LOAD_BALANCER: {
        "fill": QColor("#16a085"),
        "border": QColor("#0e6655"),
        "text": QColor("white"),
        "icon": "LB",
    },
    DeviceType.UPS: {
        "fill": QColor("#c9a227"),
        "border": QColor("#7d6417"),
        "text": QColor("white"),
        "icon": "UPS",
    },
    DeviceType.PDU: {
        "fill": QColor("#d44f00"),
        "border": QColor("#7a2d00"),
        "text": QColor("white"),
        "icon": "PDU",
    },
    DeviceType.FLOOR_PDU: {
        "fill": QColor("#b03060"),
        "border": QColor("#6b1c3a"),
        "text": QColor("white"),
        "icon": "FPDU",
    },
    DeviceType.OOB_SWITCH: {
        "fill": QColor("#7c3aed"),
        "border": QColor("#4c1d95"),
        "text": QColor("white"),
        "icon": "OOB",
    },
    DeviceType.SENSOR: {
        "fill": QColor("#0891b2"),
        "border": QColor("#164e63"),
        "text": QColor("white"),
        "icon": "SNS",
    },
    DeviceType.ENERGY_MONITOR: {
        "fill": QColor("#16a34a"),
        "border": QColor("#14532d"),
        "text": QColor("white"),
        "icon": "EV2",
    },
}

# Edge color/style by network layer
LAYER_EDGE_STYLE = {
    "production": {"color": QColor("#7f8c8d"), "width": 2,   "style": Qt.SolidLine},
    "management": {"color": QColor("#00b4d8"), "width": 1.5, "style": Qt.DashLine},
    "power":      {"color": QColor("#f59e0b"), "width": 1.5, "style": Qt.DotLine},
    "cooling":    {"color": QColor("#22d3ee"), "width": 2,   "style": Qt.DashDotLine},
}

NODE_W = 90
NODE_H = 70
ICON_SIZE = 30


class DeviceNode(QGraphicsItem):
    """Visual representation of a network device on the canvas."""

    Type = QGraphicsItem.UserType + 1

    # Fonts created once at class load, shared across all instances.
    # Avoids 4 × QFont allocations per node per repaint (5 000+ allocs/frame).
    _FONT_ICON = QFont("Arial", 14, QFont.Bold)
    _FONT_TYPE = QFont("Arial", 7)
    _FONT_NAME = QFont("Arial", 8, QFont.Bold)
    _FONT_IP   = QFont("Courier", 7)

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.device = device
        self._color_scheme = DEVICE_COLORS.get(device.device_type, DEVICE_COLORS[DeviceType.SERVER])
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self._edges: list = []
        self._faded: bool = False
        # Cache each node's rendered output as a device-pixel pixmap.
        # On a 1 300-node canvas, Qt normally calls paint() on every visible
        # node for each scene repaint (drag, panel resize, timer-induced redraws).
        # With DeviceCoordinateCache only the moved/changed node is re-painted;
        # all others are composited from cache — O(1) per static node per frame.
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)

        # Pre-compute per-node paint resources so paint() allocates nothing.
        # With 1 300+ nodes a single scene.update() would otherwise create
        # thousands of QLinearGradient / QColor objects on every frame.
        _fill = self._color_scheme["fill"]
        _text = self._color_scheme["text"]
        self._gradient = QLinearGradient(0, -NODE_H / 2, 0, NODE_H / 2)
        self._gradient.setColorAt(0, _fill.lighter(120))
        self._gradient.setColorAt(1, _fill)
        self._brush          = QBrush(self._gradient)
        self._color_type_lbl = _text.lighter(150)
        self._color_ip_lbl   = _text.lighter(130)
        self._type_str       = device.device_type.value.capitalize()

    def set_faded(self, faded: bool, repaint: bool = True):
        self._faded = faded
        # Always call update() to invalidate the DeviceCoordinateCache pixmap.
        # With caching, skipping update() (repaint=False) leaves the old faded
        # pixmap in the cache — nodes never visually unfade during discovery.
        self.update()

    def type(self):
        return DeviceNode.Type

    def add_edge(self, edge):
        self._edges.append(edge)

    def remove_edge(self, edge):
        if edge in self._edges:
            self._edges.remove(edge)

    def boundingRect(self) -> QRectF:
        return QRectF(-NODE_W / 2 - 4, -NODE_H / 2 - 4, NODE_W + 8, NODE_H + 24)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H, 8, 8)
        return path

    def paint(self, painter: QPainter, option, widget=None):
        if self._faded:
            painter.setOpacity(0.20)
        colors = self._color_scheme
        selected = self.isSelected()

        # Shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(-NODE_W / 2 + 3, -NODE_H / 2 + 3, NODE_W, NODE_H, 8, 8)

        # Body gradient (brush pre-computed in __init__)
        painter.setBrush(self._brush)

        border_color = QColor("#f39c12") if selected else colors["border"]
        border_width = 3 if selected else 1.5
        painter.setPen(QPen(border_color, border_width))
        painter.drawRoundedRect(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H, 8, 8)

        # Icon letter
        painter.setPen(QPen(colors["text"]))
        painter.setFont(self._FONT_ICON)
        icon_rect = QRectF(-NODE_W / 2 + 5, -NODE_H / 2 + 4, NODE_W - 10, ICON_SIZE)
        painter.drawText(icon_rect, Qt.AlignCenter, colors["icon"])

        # Device type label
        painter.setFont(self._FONT_TYPE)
        painter.setPen(QPen(self._color_type_lbl))
        type_rect = QRectF(-NODE_W / 2 + 2, -NODE_H / 2 + ICON_SIZE - 2, NODE_W - 4, 14)
        painter.drawText(type_rect, Qt.AlignCenter, self._type_str)

        # Determine IP display mode before drawing name (affects vertical layout)
        scene   = self.scene()
        layer   = getattr(scene, '_layer_filter', 'all')
        prod_ip = self.device.ip_address
        mgmt_ip = self.device.mgmt_ip

        if layer == 'production':
            display_ip = prod_ip
            show_two   = False
        elif layer in ('management', 'power'):
            display_ip = mgmt_ip or prod_ip
            show_two   = False
        else:  # 'all'
            show_two   = bool(prod_ip and mgmt_ip)
            display_ip = '' if show_two else (prod_ip or mgmt_ip)

        # Device name — shift up when two IP lines needed so both fit inside the box
        painter.setFont(self._FONT_NAME)
        painter.setPen(QPen(colors["text"]))
        name_y    = NODE_H / 2 - 30 if show_two else NODE_H / 2 - 26
        name_rect = QRectF(-NODE_W / 2, name_y, NODE_W, 14)
        painter.drawText(name_rect, Qt.AlignCenter, self.device.name)

        # IP address lines
        painter.setFont(self._FONT_IP)
        painter.setPen(QPen(self._color_ip_lbl))
        if show_two:
            # Both lines stay inside the box: IP1 y=15→25, IP2 y=25→35
            painter.drawText(QRectF(-NODE_W / 2, NODE_H / 2 - 20, NODE_W, 10),
                             Qt.AlignCenter, prod_ip)
            painter.drawText(QRectF(-NODE_W / 2, NODE_H / 2 - 10, NODE_W, 10),
                             Qt.AlignCenter, mgmt_ip)
        else:
            painter.drawText(QRectF(-NODE_W / 2, NODE_H / 2 - 14, NODE_W, 12),
                             Qt.AlignCenter, display_ip or '')

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for edge in self._edges:
                edge.adjust()
        return super().itemChange(change, value)

    def center_point(self) -> QPointF:
        return self.scenePos()

    def hoverEnterEvent(self, event):
        _link_tooltip().show_for_node(self.device, event.screenPos())
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        _link_tooltip().move(event.screenPos() + QPoint(16, 16))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        _link_tooltip().hide()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        _link_tooltip().hide()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.isUnderMouse():
            _link_tooltip().show_for_node(self.device, event.screenPos())


class LinkEdge(QGraphicsLineItem):
    """Visual link between two device nodes."""

    # Shared stroker — created once instead of per hit-test call.
    _STROKER = QPainterPathStroker()
    _STROKER.setWidth(16)

    def __init__(self, src_node: DeviceNode, dst_node: DeviceNode,
                 layer: str = "production", parent=None):
        super().__init__(parent)
        self.src_node = src_node
        self.dst_node = dst_node
        self._broken = False
        self._faded = False
        self._layer = layer
        src_node.add_edge(self)
        dst_node.add_edge(self)
        self.setZValue(0)
        style = LAYER_EDGE_STYLE.get(layer, LAYER_EDGE_STYLE["production"])
        self.setPen(QPen(style["color"], style["width"], style["style"], Qt.RoundCap))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.adjust()

    def set_faded(self, faded: bool, repaint: bool = True):
        self._faded = faded
        if repaint:
            self.update()

    def hoverEnterEvent(self, event):
        scene = self.scene()
        discovery_running = getattr(scene, "_discovery_running", False)
        _link_tooltip().show_for_link(
            self.src_node.device.name,
            self.dst_node.device.name,
            self._broken,
            self._faded,
            discovery_running,
            event.screenPos(),
        )
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        _link_tooltip().move(event.screenPos() + QPoint(16, 16))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        _link_tooltip().hide()
        super().hoverLeaveEvent(event)

    def set_broken(self, broken: bool):
        self._broken = broken
        self.update()

    def boundingRect(self) -> QRectF:
        line = self.line()
        extra = 10.0  # half of the 16px hit area + margin
        x1, y1 = line.x1(), line.y1()
        x2, y2 = line.x2(), line.y2()
        return QRectF(
            min(x1, x2) - extra,
            min(y1, y2) - extra,
            abs(x2 - x1) + extra * 2,
            abs(y2 - y1) + extra * 2,
        )

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.moveTo(self.line().p1())
        p.lineTo(self.line().p2())
        return self._STROKER.createStroke(p)

    def adjust(self):
        src = self.src_node.scenePos()
        dst = self.dst_node.scenePos()
        self.setLine(QLineF(src, dst))

    def paint(self, painter, option, widget=None):
        if self._faded:
            painter.setOpacity(0.10)
        selected = self.isSelected()
        if self._broken:
            color = QColor("#e74c3c")      # red for broken links
            width = 2.5
            style = Qt.DashLine
        elif selected:
            color = QColor("#f39c12")      # orange when selected
            width = 3
            style = Qt.SolidLine
        else:
            layer_style = LAYER_EDGE_STYLE.get(self._layer, LAYER_EDGE_STYLE["production"])
            color = layer_style["color"]
            width = layer_style["width"]
            style = layer_style["style"]

        painter.setPen(QPen(color, width, style, Qt.RoundCap))
        painter.drawLine(self.line())

        # Midpoint indicator: X for broken, dot for normal (production only)
        line = self.line()
        mid = QPointF((line.x1() + line.x2()) / 2, (line.y1() + line.y2()) / 2)
        painter.setBrush(QBrush(color))
        if self._broken:
            painter.setPen(QPen(color, 2))
            painter.drawLine(QPointF(mid.x() - 5, mid.y() - 5), QPointF(mid.x() + 5, mid.y() + 5))
            painter.drawLine(QPointF(mid.x() + 5, mid.y() - 5), QPointF(mid.x() - 5, mid.y() + 5))
        elif self._layer == "production":
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(mid, 4, 4)


def _resolve_model_name(device) -> str:
    if device.model_name:
        return device.model_name
    models = DEVICE_MODELS.get((device.device_type, device.vendor), [])
    return models[0].name if models else "—"


# ------------------------------------------------------------------ #
#  Modern link tooltip                                                 #
# ------------------------------------------------------------------ #

class _LinkTooltip(QWidget):
    """Frameless, dark-themed tooltip shown when hovering a link edge."""

    def __init__(self):
        super().__init__(None,
                         Qt.ToolTip | Qt.FramelessWindowHint |
                         Qt.NoDropShadowWindowHint)
        # WA_TranslucentBackground is intentionally NOT set.
        # On Windows, layered (WS_EX_LAYERED) windows created by
        # WA_TranslucentBackground ask the DWM to composite their transparent
        # pixels against the content of the window underneath.  While the main
        # window's QGraphicsView scene is rendering (beginPaint active), the DWM
        # compositing pass can call endPaint() on the same QBackingStore, leaving
        # the scene QPainter dangling and crashing on the very next painter call.
        # Using a solid background removes the layered-window requirement entirely.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            QWidget {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
            }
            QLabel { background: transparent; border: none; }
        """)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)

        # Device names row
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            "color:#e6edf3; font-size:13px; font-weight:600;"
        )

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#30363d; border:none;")

        # Status row
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("font-size:12px;")
        self._status_lbl.setTextFormat(Qt.RichText)

        layout.addWidget(self._name_lbl)
        layout.addWidget(sep)
        layout.addWidget(self._status_lbl)

    def _show(self, global_pos: QPoint):
        self.adjustSize()
        self.move(global_pos + QPoint(16, 16))
        self.show()
        self.raise_()

    def show_for_link(self, src: str, dst: str, broken: bool, faded: bool,
                      discovery_running: bool, global_pos: QPoint):
        self._name_lbl.setText(f"{src}  ↔  {dst}")
        if faded and discovery_running:
            dot = '<span style="color:#8b949e;font-size:15px;">●</span>'
            txt = '<span style="color:#8b949e;">Discovering…</span>'
        elif faded:
            dot = '<span style="color:#8b949e;font-size:15px;">●</span>'
            txt = '<span style="color:#8b949e;">Not simulated</span>'
        elif broken:
            dot = '<span style="color:#f85149;font-size:15px;">●</span>'
            txt = '<span style="color:#f85149;">Broken</span>'
        else:
            dot = '<span style="color:#3fb950;font-size:15px;">●</span>'
            txt = '<span style="color:#3fb950;">Active</span>'
        self._status_lbl.setText(
            f'{dot}&nbsp;&nbsp;<span style="color:#8b949e;">Status:</span>&nbsp;{txt}'
        )
        self._show(global_pos)

    def show_for_node(self, device, global_pos: QPoint):
        self._name_lbl.setText(device.name)
        mono = 'font-family:Consolas,monospace;'
        rows = [
            ("Type",      device.device_type.value.capitalize()),
            ("Vendor",    device.vendor.value),
            ("Model",     _resolve_model_name(device)),
            ("OS",        device.os_name),
            ("Version",   device.os_version),
        ]
        if device.ip_address:
            rows.append(("Prod IP",  f'<span style="{mono}">{device.ip_address}</span>'))
        if device.mgmt_ip:
            rows.append(("Mgmt IP", f'<span style="{mono}">{device.mgmt_ip}</span>'))
        rows.append(("SNMP Port", f'<span style="{mono}">{device.snmp_port}</span>'))
        if device.device_type in (DeviceType.ROUTER, DeviceType.SWITCH):
            rows.append(("gNMI Port", f'<span style="{mono}">{device.gnmi_port}</span>'))
        lines = "".join(
            f'<span style="color:#8b949e;">{k}:</span>'
            f'&nbsp;&nbsp;<span style="color:#e6edf3;">{v}</span><br>'
            for k, v in rows
        ).rstrip("<br>")
        self._status_lbl.setText(lines)
        self._show(global_pos)


_tooltip_instance: Optional[_LinkTooltip] = None

def _link_tooltip() -> _LinkTooltip:
    global _tooltip_instance
    if _tooltip_instance is None:
        _tooltip_instance = _LinkTooltip()
    return _tooltip_instance


class TopologyScene(QGraphicsScene):
    """Custom scene handling link creation via drag."""

    link_created = Signal(str, str)      # (src_device_id, dst_device_id)
    device_moved = Signal(str, float, float)
    node_right_clicked = Signal(str, object)  # (device_id, QPoint)
    edge_right_clicked = Signal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._link_mode = False
        self._link_start_node: Optional[DeviceNode] = None
        self._drag_line: Optional[QGraphicsLineItem] = None
        self._nodes: Dict[str, DeviceNode] = {}
        self._edges: Dict[tuple, LinkEdge] = {}
        self._discovery_running: bool = False
        self._layer_filter: str = "all"

    def set_discovery_running(self, running: bool):
        self._discovery_running = running

    def set_link_mode(self, enabled: bool):
        self._link_mode = enabled
        if not enabled and self._drag_line:
            self.removeItem(self._drag_line)
            self._drag_line = None
        self._link_start_node = None

    def add_device_node(self, device: Device, x: float, y: float) -> DeviceNode:
        node = DeviceNode(device)
        node.setPos(x, y)
        self.addItem(node)
        self._nodes[device.id] = node
        return node

    def remove_device_node(self, device_id: str):
        node = self._nodes.pop(device_id, None)
        if node:
            # Remove associated edges (key format is ((src, dst), layer))
            to_remove = [k for k in self._edges if device_id in k[0]]
            for k in to_remove:
                edge = self._edges.pop(k)
                edge.src_node.remove_edge(edge)
                edge.dst_node.remove_edge(edge)
                self.removeItem(edge)
            self.removeItem(node)

    def add_link_edge(self, src_id: str, dst_id: str,
                      layer: str = "production") -> Optional[LinkEdge]:
        # Key includes layer so power + management edges between the same pair
        # can both be stored (e.g. PDU↔OOB switch).
        pair = tuple(sorted([src_id, dst_id]))
        key = (pair, layer)
        if key in self._edges:
            return None
        src_node = self._nodes.get(src_id)
        dst_node = self._nodes.get(dst_id)
        if not src_node or not dst_node:
            return None
        edge = LinkEdge(src_node, dst_node, layer=layer)
        self.addItem(edge)
        self._edges[key] = edge
        # Apply current layer filter immediately
        if self._layer_filter != "all":
            edge.setVisible(edge._layer == self._layer_filter)
        return edge

    def set_layer_filter(self, layer: str):
        """Show only devices and edges that belong to the selected layer.
        Use 'all' to show every layer.
        """
        self._layer_filter = layer
        from core.device_manager import DeviceType as _DT
        _MGMT_TYPES  = frozenset({_DT.OOB_SWITCH, _DT.SENSOR})
        _POWER_TYPES = frozenset({_DT.UPS, _DT.PDU, _DT.FLOOR_PDU, _DT.RPP, _DT.GENERATOR, _DT.ENERGY_MONITOR})

        # Devices that participate in the selected layer's edges — used to hide
        # everything unrelated when viewing a single infrastructure layer.
        layer_node_ids = set()
        if layer not in ("all", "production"):
            for edge in self._edges.values():
                if edge._layer == layer:
                    layer_node_ids.add(edge.src_node.device.id)
                    layer_node_ids.add(edge.dst_node.device.id)

        for node in self._nodes.values():
            dtype = node.device.device_type
            if layer == "production":
                # Only production-class devices; OOB switches, sensors, UPS, PDU hidden
                visible = dtype not in _MGMT_TYPES and dtype not in _POWER_TYPES
            elif layer == "cooling":
                # Only devices on a cooling (water-loop) edge
                visible = node.device.id in layer_node_ids
            else:
                # "all", "management", "power" — every device is reachable via mgmt/power
                visible = True
            node.setVisible(visible)

        for edge in self._edges.values():
            if layer == "all":
                edge.setVisible(True)
            else:
                visible = (
                    edge._layer == layer
                    and edge.src_node.isVisible()
                    and edge.dst_node.isVisible()
                )
                edge.setVisible(visible)

        # Invalidate DeviceCoordinateCache so IP text re-renders for new layer
        for node in self._nodes.values():
            node.update()
        self.update()

    def remove_link_edge(self, src_id: str, dst_id: str):
        pair = tuple(sorted([src_id, dst_id]))
        # Remove all layer edges between this pair
        for key in [k for k in self._edges if k[0] == pair]:
            edge = self._edges.pop(key)
            edge.src_node.remove_edge(edge)
            edge.dst_node.remove_edge(edge)
            self.removeItem(edge)

    def get_node(self, device_id: str) -> Optional[DeviceNode]:
        return self._nodes.get(device_id)

    def set_edge_broken(self, src_id: str, dst_id: str, broken: bool):
        # Break/restore the production edge between this pair
        pair = tuple(sorted([src_id, dst_id]))
        edge = self._edges.get((pair, "production"))
        if edge:
            edge.set_broken(broken)

    def set_node_faded(self, device_id: str, faded: bool, repaint: bool = True):
        node = self._nodes.get(device_id)
        if node:
            node.set_faded(faded, repaint=repaint)

    def set_edge_faded(self, src_id: str, dst_id: str, faded: bool, repaint: bool = True):
        pair = tuple(sorted([src_id, dst_id]))
        # Fade/unfade all layer edges between this pair
        for key, edge in self._edges.items():
            if key[0] == pair:
                edge.set_faded(faded, repaint=repaint)

    def set_all_faded(self, faded: bool):
        # Set flags on every item without triggering individual repaints,
        # then issue a single scene-wide update — avoids O(N) dirty-region calls.
        for node in self._nodes.values():
            node.set_faded(faded, repaint=False)
        for edge in self._edges.values():
            edge.set_faded(faded, repaint=False)
        self.update()

    def set_layer_faded(self, layer: str, faded: bool):
        """Fade or unfade only devices and edges that belong to *layer*."""
        from core.device_manager import DeviceType as _DT
        _MGMT_TYPES  = frozenset({_DT.OOB_SWITCH, _DT.SENSOR})
        _POWER_TYPES = frozenset({_DT.UPS, _DT.PDU, _DT.FLOOR_PDU, _DT.RPP, _DT.GENERATOR, _DT.ENERGY_MONITOR})
        _PROD_TYPES  = frozenset({
            _DT.ROUTER, _DT.SWITCH, _DT.FIREWALL,
            _DT.SERVER, _DT.LOAD_BALANCER,
        })
        if layer == "management":
            node_types = _MGMT_TYPES
        elif layer == "power":
            node_types = _POWER_TYPES
        else:  # production
            node_types = _PROD_TYPES

        for node in self._nodes.values():
            if node.device.device_type in node_types:
                node.set_faded(faded, repaint=False)
        for edge in self._edges.values():
            if edge._layer == layer:
                edge.set_faded(faded, repaint=False)
        self.update()

    def mousePressEvent(self, event):
        if self._link_mode and event.button() == Qt.LeftButton:
            node = self._find_node_at(event.scenePos())
            if node:
                self._link_start_node = node
                self._drag_line = self.addLine(
                    QLineF(node.scenePos(), event.scenePos()),
                    QPen(QColor("#e74c3c"), 2, Qt.DashLine)
                )
                self._drag_line.setZValue(10)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._link_mode and self._drag_line and self._link_start_node:
            line = self._drag_line.line()
            self._drag_line.setLine(QLineF(line.p1(), event.scenePos()))
        else:
            super().mouseMoveEvent(event)
            # selectedItems() returns only the (usually tiny) selection set —
            # avoids scanning all 1000+ nodes on every mouse-move event.
            for item in self.selectedItems():
                if isinstance(item, DeviceNode):
                    p = item.scenePos()
                    self.device_moved.emit(item.device.id, p.x(), p.y())

    def mouseReleaseEvent(self, event):
        if self._link_mode and event.button() == Qt.LeftButton:
            if self._drag_line:
                self.removeItem(self._drag_line)
                self._drag_line = None
            if self._link_start_node:
                end_node = self._find_node_at(event.scenePos())
                if end_node and end_node is not self._link_start_node:
                    self.link_created.emit(
                        self._link_start_node.device.id,
                        end_node.device.id
                    )
                self._link_start_node = None
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        node = self._find_node_at(event.scenePos())
        if node:
            self.node_right_clicked.emit(node.device.id, event.screenPos())
            return

        # Check edge — key format is ((src_id, dst_id), layer)
        # Prefer visible edges (current layer) over hidden ones when multiple
        # layer edges share the same screen position.
        hit_visible = None
        hit_hidden  = None
        for (pair, _layer), edge in self._edges.items():
            if not edge.contains(event.scenePos() - edge.pos()):
                continue
            src_id, dst_id = pair
            if edge.isVisible():
                hit_visible = (src_id, dst_id)
                break
            elif hit_hidden is None:
                hit_hidden = (src_id, dst_id)
        hit = hit_visible or hit_hidden
        if hit:
            self.edge_right_clicked.emit(hit[0], hit[1], event.screenPos())
            return

        super().contextMenuEvent(event)

    def _find_node_at(self, scene_pos: QPointF) -> Optional[DeviceNode]:
        for item in self.items(scene_pos):
            if isinstance(item, DeviceNode):
                return item
        return None

    def clear_all(self):
        self._nodes.clear()
        self._edges.clear()
        self.clear()

    def highlight_device(self, device_id: str, highlight: bool):
        node = self._nodes.get(device_id)
        if node:
            node.setSelected(highlight)

    def apply_positions(self, positions: dict):
        """Move nodes to computed positions and refresh all edges.

        positions: {device_id: (x, y)}
        """
        for dev_id, (x, y) in positions.items():
            node = self._nodes.get(dev_id)
            if node:
                node.setPos(x, y)
        for edge in self._edges.values():
            edge.adjust()


class TopologyView(QGraphicsView):
    """The main topology canvas widget."""

    reset_current_layout_requested = Signal()   # undo drags, restore last applied layout
    sig_live_discovery = Signal()               # user wants to start live discovery
    sig_stop_discovery = Signal()               # user wants to stop live discovery

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = TopologyScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor("#1a1a2e")))
        self._zoom_level = 1.0
        self._min_zoom   = 0.05   # updated by fitInView so zoom-out can reach fit-all level

        # Spinner state — drawn via drawForeground so it always renders on top
        self._spinner_visible = False
        self._spinner_angle   = 0
        self._spinner_label   = ""
        self._spinner_timer   = QTimer(self)
        self._spinner_timer.timeout.connect(self._spinner_tick)

        self._discovery_busy = False
        self._snmp_running = False

        # Overlay controls (children of viewport for correct hit-testing)
        self._build_overlay_controls()

    # ------------------------------------------------------------------ #
    #  Overlay controls                                                    #
    # ------------------------------------------------------------------ #

    _BTN_GROUP_STYLE = """
        QWidget#btnGroup {
            background: rgba(22,27,34,220);
            border: 1px solid #30363d;
            border-radius: 6px;
        }
    """
    _BTN_STYLE = """
        QPushButton {
            background: transparent;
            border: none;
            color: #c9d1d9;
            font-size: 16px;
            border-radius: 4px;
        }
        QPushButton:hover   { background: rgba(31,111,235,160); color: #ffffff; }
        QPushButton:checked { background: rgba(31,111,235,220); color: #ffffff; }
    """
    _SEARCH_BAR_STYLE = """
        QWidget#searchBarPanel {
            background: rgba(22,27,34,220);
            border: 1px solid #30363d;
            border-radius: 6px;
        }
    """
    _EDIT_STYLE = """
        QLineEdit {
            background: transparent;
            border: none;
            color: #e6edf3;
            font-size: 12px;
        }
        QLineEdit::placeholder { color: #8b949e; }
    """
    _COUNT_STYLE = "color:#8b949e; font-size:11px; background:transparent; border:none;"
    _CLEAR_BTN_STYLE = """
        QPushButton { background:transparent; border:none; color:#8b949e; font-size:15px; }
        QPushButton:hover { color:#e6edf3; }
    """

    # Pixel constants for overlay layout
    _BTN_SIZE   = 34
    _BTN_MARGIN = 12
    _BTN_GAP    = 1     # gap between buttons inside the group panel

    def _build_overlay_controls(self):
        """Create the top-left button group and the collapsible search bar."""
        vp = self.viewport()

        # ── Button group panel ──────────────────────────────────────────
        panel = QWidget(vp)
        panel.setObjectName("btnGroup")
        panel.setStyleSheet(self._BTN_GROUP_STYLE)
        panel.setAttribute(Qt.WA_StyledBackground, True)

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(3, 3, 3, 3)
        vbox.setSpacing(self._BTN_GAP)

        def _btn(text, tip):
            b = QPushButton(text, panel)
            b.setFixedSize(self._BTN_SIZE - 6, self._BTN_SIZE - 6)
            b.setToolTip(tip)
            b.setStyleSheet(self._BTN_STYLE)
            vbox.addWidget(b)
            return b

        self._btn_search      = _btn("⌕", "Search devices")
        self._btn_zoom_in     = _btn("+", "Zoom In")
        self._btn_zoom_out    = _btn("−", "Zoom Out")
        self._btn_fit         = _btn("⊡", "Fit to View")
        self._btn_reset_current_layout = _btn("↺", "Reset Current Layout\n(undo node drags)")
        self._btn_live_discovery = _btn("●", "Live Discovery\n(poll all devices via SNMP)")
        self._btn_live_discovery.setEnabled(False)
        self._btn_live_discovery.setStyleSheet(self._BTN_STYLE + """
            QPushButton { color: #e74c3c; }
            QPushButton:disabled { color: #5a2020; }
            QPushButton:hover { color: #ff6b6b; }
        """)

        self._btn_search.setCheckable(True)
        self._btn_search.clicked.connect(self._toggle_search_bar)
        self._btn_zoom_in.clicked.connect(self.zoom_in)
        self._btn_zoom_out.clicked.connect(self.zoom_out)
        self._btn_fit.clicked.connect(self.fit_view)
        self._btn_reset_current_layout.clicked.connect(self.reset_current_layout_requested)
        self._btn_live_discovery.clicked.connect(self._on_discovery_btn_clicked)

        panel.adjustSize()
        panel.raise_()
        self._btn_group = panel

        # ── Search bar panel ────────────────────────────────────────────
        bar = QWidget(vp)
        bar.setObjectName("searchBarPanel")
        bar.setStyleSheet(self._SEARCH_BAR_STYLE)
        bar.setAttribute(Qt.WA_StyledBackground, True)

        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(8, 0, 4, 0)
        hbox.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search by name or IP…")
        self._search_edit.setStyleSheet(self._EDIT_STYLE)
        self._search_edit.textChanged.connect(self._on_search)

        self._search_count = QLabel()
        self._search_count.setStyleSheet(self._COUNT_STYLE)
        self._search_count.hide()

        clear_btn = QPushButton("×")
        clear_btn.setFixedSize(22, 22)
        clear_btn.setStyleSheet(self._CLEAR_BTN_STYLE)
        clear_btn.clicked.connect(self._clear_search)

        hbox.addWidget(self._search_edit)
        hbox.addWidget(self._search_count)
        hbox.addWidget(clear_btn)

        bar.hide()
        bar.raise_()
        self._search_bar = bar

        # ── Layer toggle panel ──────────────────────────────────────────
        layer_panel = QWidget(vp)
        layer_panel.setObjectName("layerPanel")
        layer_panel.setStyleSheet("""
            QWidget#layerPanel {
                background: rgba(22,27,34,220);
                border: 1px solid #30363d;
                border-radius: 6px;
            }
        """)
        layer_panel.setAttribute(Qt.WA_StyledBackground, True)

        lhbox = QHBoxLayout(layer_panel)
        lhbox.setContentsMargins(6, 2, 6, 2)
        lhbox.setSpacing(2)

        _lbl_layer = QLabel("Layer:")
        _lbl_layer.setStyleSheet("color:#8b949e; font-size:10px; background:transparent; border:none;")
        lhbox.addWidget(_lbl_layer)

        _layer_btn_style = """
            QPushButton {
                background: transparent;
                border: 1px solid #30363d;
                color: #8b949e;
                font-size: 9px;
                border-radius: 3px;
                padding: 2px 6px;
            }
            QPushButton:checked {
                background: rgba(31,111,235,200);
                color: white;
                border-color: #1f6feb;
            }
            QPushButton:hover { background: rgba(31,111,235,100); color: white; }
        """

        def _layer_btn(text, tip):
            b = QPushButton(text, layer_panel)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setStyleSheet(_layer_btn_style)
            b.setFixedHeight(20)
            lhbox.addWidget(b)
            return b

        self._btn_layer_all  = _layer_btn("All",   "Show all network layers")
        self._btn_layer_prod = _layer_btn("Prod",  "Production network only")
        self._btn_layer_mgmt = _layer_btn("Mgmt",  "Management (OOB) network only")
        self._btn_layer_pwr  = _layer_btn("Power", "Power chain only")
        self._btn_layer_cool = _layer_btn("Cool",  "Cooling water loops only")
        self._btn_layer_all.setChecked(True)

        # Mutual exclusion
        self._layer_btns = [self._btn_layer_all, self._btn_layer_prod,
                            self._btn_layer_mgmt, self._btn_layer_pwr,
                            self._btn_layer_cool]
        _layer_map = {"All": "all", "Prod": "production",
                      "Mgmt": "management", "Power": "power", "Cool": "cooling"}
        for btn in self._layer_btns:
            label = btn.text()
            btn.clicked.connect(lambda checked, b=btn, lbl=label: self._on_layer_btn(b, lbl))

        layer_panel.adjustSize()
        layer_panel.raise_()
        self._layer_panel = layer_panel
        self._layer_map = _layer_map

    def get_active_layer(self) -> str:
        """Return the currently selected layer filter ('all', 'production', 'management', 'power')."""
        return self._scene._layer_filter

    def set_snmp_running(self, running: bool):
        """Enable or disable the Live Discovery button based on SNMP sim state."""
        self._snmp_running = running
        if not running:
            self._apply_discovery_idle_style()
        self._update_discovery_btn_state()

    def set_discovery_busy(self, busy: bool):
        """Switch the button between start (●) and stop (■) appearance."""
        self._discovery_busy = busy
        if busy:
            self._apply_discovery_stop_style()
        else:
            self._apply_discovery_idle_style()
        self._update_discovery_btn_state()

    def _update_discovery_btn_state(self):
        """Enable discovery button whenever SNMP simulator is running."""
        self._btn_live_discovery.setEnabled(self._snmp_running)

    def _apply_discovery_idle_style(self):
        self._btn_live_discovery.setText("●")
        self._btn_live_discovery.setToolTip("Live Discovery\n(poll all devices via SNMP)")
        self._btn_live_discovery.setStyleSheet(self._BTN_STYLE + """
            QPushButton { color: #e74c3c; }
            QPushButton:disabled { color: #5a2020; }
            QPushButton:hover { color: #ff6b6b; }
        """)

    def _apply_discovery_stop_style(self):
        self._btn_live_discovery.setText("■")
        self._btn_live_discovery.setToolTip("Stop Live Discovery")
        self._btn_live_discovery.setStyleSheet(self._BTN_STYLE + """
            QPushButton { color: #f39c12; }
            QPushButton:hover { color: #f5c543; }
        """)

    def _on_discovery_btn_clicked(self):
        if self._discovery_busy:
            self.sig_stop_discovery.emit()
        else:
            self.sig_live_discovery.emit()

    def _on_layer_btn(self, clicked_btn: QPushButton, label: str):
        for btn in self._layer_btns:
            btn.setChecked(btn is clicked_btn)
        self._scene.set_layer_filter(self._layer_map.get(label, "all"))
        self._update_discovery_btn_state()

    def _toggle_search_bar(self, checked: bool):
        if checked:
            self._search_bar.show()
            self._search_bar.raise_()
            self._search_edit.setFocus()
        else:
            self._clear_search()
            self._search_bar.hide()

    def _clear_search(self):
        self._search_edit.clear()
        self._scene.clearSelection()
        self._search_count.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_overlays()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._reposition_overlays()

    def _reposition_overlays(self):
        vp  = self.viewport()
        vw  = vp.width()
        m   = self._BTN_MARGIN
        bsz = self._BTN_SIZE

        # Button group: top-left
        self._btn_group.move(m, m)

        # Search bar: same top edge, immediately right of the button group
        bar_x = m + self._btn_group.width() + 6
        bar_w = min(280, max(160, vw - bar_x - m))
        self._search_bar.setGeometry(bar_x, m, bar_w, bsz)

        # Layer toggle panel: below search bar (right-aligned to search bar)
        self._layer_panel.adjustSize()
        lp_w = self._layer_panel.width()
        lp_x = bar_x
        lp_y = m + bsz + 4
        self._layer_panel.setGeometry(lp_x, lp_y, lp_w, 26)

        self._btn_group.raise_()
        self._search_bar.raise_()
        self._layer_panel.raise_()

    def _on_search(self, query: str):
        self._scene.clearSelection()
        query = query.strip().lower()
        if not query:
            self._search_count.hide()
            return

        matches = [
            node for node in self._scene._nodes.values()
            if query in node.device.name.lower()
            or query in node.device.ip_address.lower()
            or query in (node.device.mgmt_ip or "").lower()
        ]

        self._search_count.setText(f"{len(matches)}/{len(self._scene._nodes)}")
        self._search_count.show()

        if not matches:
            return

        for node in matches:
            node.setSelected(True)

        bounds = matches[0].sceneBoundingRect()
        for node in matches[1:]:
            bounds = bounds.united(node.sceneBoundingRect())
        self.fitInView(bounds.adjusted(-120, -120, 120, 120), Qt.KeepAspectRatio)
        self._sync_zoom_after_fit()

    def _sync_zoom_after_fit(self):
        """Called after every fitInView to keep _zoom_level and _min_zoom in sync."""
        self._zoom_level = self.transform().m11()
        # Allow zooming out 20 % beyond the fitted level so the user can always
        # reach a fully-visible view regardless of topology size.
        self._min_zoom = self._zoom_level * 0.8

    def zoom_in(self):
        factor = 1.15
        new_zoom = self._zoom_level * factor
        if new_zoom <= 5.0:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    def zoom_out(self):
        factor = 1 / 1.15
        new_zoom = self._zoom_level * factor
        if new_zoom >= self._min_zoom:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    # ------------------------------------------------------------------ #
    #  Spinner (drawn via drawForeground)                                  #
    # ------------------------------------------------------------------ #

    def _spinner_tick(self):
        self._spinner_angle = (self._spinner_angle - 8) % 360
        self.viewport().update()

    def show_spinner(self, label: str = "Computing layout…"):
        self._spinner_label   = label
        self._spinner_visible = True
        self._spinner_angle   = 0
        self._spinner_timer.start(16)   # ~60 fps

    def hide_spinner(self):
        self._spinner_timer.stop()
        self._spinner_visible = False
        self.viewport().update()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self._spinner_visible:
            return

        # Reset to viewport (device) coordinates so the overlay is always
        # full-screen regardless of the current scene transform / zoom level.
        painter.save()
        painter.resetTransform()

        w = painter.device().width()
        h = painter.device().height()
        cx, cy = w // 2, h // 2
        r = 32

        painter.setRenderHint(QPainter.Antialiasing)

        # dim backdrop
        painter.fillRect(0, 0, w, h, QColor(10, 10, 20, 170))

        arc_rect = QRectF(cx - r, cy - r - 20, r * 2, r * 2)

        # faint full circle
        pen_bg = QPen(QColor(255, 255, 255, 35), 5)
        pen_bg.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_bg)
        painter.drawEllipse(arc_rect)

        # spinning arc
        pen_arc = QPen(QColor("#1f6feb"), 5)
        pen_arc.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_arc)
        painter.drawArc(arc_rect, self._spinner_angle * 16, 270 * 16)

        # label
        painter.setPen(QColor("#c9d1d9"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(0, cy + r - 10, w, 40),
            Qt.AlignHCenter | Qt.AlignTop,
            self._spinner_label,
        )

        painter.restore()

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor
        if event.angleDelta().y() > 0:
            factor = zoom_in_factor
        else:
            factor = zoom_out_factor
        new_zoom = self._zoom_level * factor
        if self._min_zoom <= new_zoom <= 5.0:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    def apply_force_layout_positions(self, positions: dict):
        """Apply force-directed positions and fit the result into view."""
        self._scene.apply_positions(positions)
        if self._scene._nodes:
            bounds = self._scene.itemsBoundingRect()
            # Reset the scene rect to the current items so the old (stale, possibly
            # off-centre) auto-grown rect doesn't skew the fitInView calculation.
            self._scene.setSceneRect(bounds.adjusted(-200, -200, 200, 200))
            self.fitInView(bounds.adjusted(-100, -100, 100, 100), Qt.KeepAspectRatio)
            self._sync_zoom_after_fit()

    def fit_view(self):
        if self._scene._nodes:
            bounds = self._scene.itemsBoundingRect()
            self._scene.setSceneRect(bounds.adjusted(-200, -200, 200, 200))
            self.fitInView(bounds.adjusted(-50, -50, 50, 50), Qt.KeepAspectRatio)
            self._sync_zoom_after_fit()
        else:
            self.resetTransform()
            self._zoom_level = 1.0
            self._min_zoom   = 0.05

    def reset_zoom(self):
        self.resetTransform()
        self._zoom_level = 1.0

    @property
    def topology_scene(self) -> TopologyScene:
        return self._scene

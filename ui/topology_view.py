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
)
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QObject, QLineF
from PySide6.QtGui import (
    QPen, QBrush, QColor, QPainter, QFont, QLinearGradient,
    QPolygonF, QPainterPath, QPainterPathStroker,
)

from core.device_manager import Device, DeviceType


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
}

NODE_W = 90
NODE_H = 70
ICON_SIZE = 30


class DeviceNode(QGraphicsItem):
    """Visual representation of a network device on the canvas."""

    Type = QGraphicsItem.UserType + 1

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.device = device
        self._color_scheme = DEVICE_COLORS.get(device.device_type, DEVICE_COLORS[DeviceType.SERVER])
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(1)
        self._edges: list = []

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
        colors = self._color_scheme
        selected = self.isSelected()

        # Shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(-NODE_W / 2 + 3, -NODE_H / 2 + 3, NODE_W, NODE_H, 8, 8)

        # Body gradient
        grad = QLinearGradient(0, -NODE_H / 2, 0, NODE_H / 2)
        fill = colors["fill"]
        grad.setColorAt(0, fill.lighter(120))
        grad.setColorAt(1, fill)
        painter.setBrush(QBrush(grad))

        border_color = QColor("#f39c12") if selected else colors["border"]
        border_width = 3 if selected else 1.5
        painter.setPen(QPen(border_color, border_width))
        painter.drawRoundedRect(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H, 8, 8)

        # Icon letter
        painter.setPen(QPen(colors["text"]))
        icon_font = QFont("Arial", 14, QFont.Bold)
        painter.setFont(icon_font)
        icon_rect = QRectF(-NODE_W / 2 + 5, -NODE_H / 2 + 4, NODE_W - 10, ICON_SIZE)
        painter.drawText(icon_rect, Qt.AlignCenter, colors["icon"])

        # Device type label
        type_font = QFont("Arial", 7)
        painter.setFont(type_font)
        painter.setPen(QPen(colors["text"].lighter(150)))
        type_rect = QRectF(-NODE_W / 2 + 2, -NODE_H / 2 + ICON_SIZE - 2, NODE_W - 4, 14)
        painter.drawText(type_rect, Qt.AlignCenter,
                         self.device.device_type.value.capitalize())

        # Device name
        name_font = QFont("Arial", 8, QFont.Bold)
        painter.setFont(name_font)
        painter.setPen(QPen(colors["text"]))
        name_rect = QRectF(-NODE_W / 2, NODE_H / 2 - 26, NODE_W, 14)
        painter.drawText(name_rect, Qt.AlignCenter, self.device.name)

        # IP address
        ip_font = QFont("Courier", 7)
        painter.setFont(ip_font)
        painter.setPen(QPen(colors["text"].lighter(130)))
        ip_rect = QRectF(-NODE_W / 2, NODE_H / 2 - 14, NODE_W, 12)
        painter.drawText(ip_rect, Qt.AlignCenter, self.device.ip_address)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for edge in self._edges:
                edge.adjust()
        return super().itemChange(change, value)

    def center_point(self) -> QPointF:
        return self.scenePos()


class LinkEdge(QGraphicsLineItem):
    """Visual link between two device nodes."""

    def __init__(self, src_node: DeviceNode, dst_node: DeviceNode, parent=None):
        super().__init__(parent)
        self.src_node = src_node
        self.dst_node = dst_node
        self._broken = False
        src_node.add_edge(self)
        dst_node.add_edge(self)
        self.setZValue(0)
        self.setPen(QPen(QColor("#7f8c8d"), 2, Qt.SolidLine, Qt.RoundCap))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.adjust()

    def set_broken(self, broken: bool):
        self._broken = broken
        self.update()

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.moveTo(self.line().p1())
        p.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(16)
        return stroker.createStroke(p)

    def adjust(self):
        src = self.src_node.scenePos()
        dst = self.dst_node.scenePos()
        self.setLine(QLineF(src, dst))

    def paint(self, painter, option, widget=None):
        selected = self.isSelected()
        if self._broken:
            color = QColor("#e74c3c")      # red
            width = 2.5
            style = Qt.DashLine
        elif selected:
            color = QColor("#f39c12")      # orange when selected
            width = 3
            style = Qt.SolidLine
        else:
            color = QColor("#7f8c8d")
            width = 2
            style = Qt.SolidLine

        painter.setPen(QPen(color, width, style, Qt.RoundCap))
        painter.drawLine(self.line())

        # Midpoint indicator: X for broken, dot for normal
        line = self.line()
        mid = QPointF((line.x1() + line.x2()) / 2, (line.y1() + line.y2()) / 2)
        painter.setBrush(QBrush(color))
        if self._broken:
            painter.setPen(QPen(color, 2))
            painter.drawLine(QPointF(mid.x() - 5, mid.y() - 5), QPointF(mid.x() + 5, mid.y() + 5))
            painter.drawLine(QPointF(mid.x() + 5, mid.y() - 5), QPointF(mid.x() - 5, mid.y() + 5))
        else:
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(mid, 4, 4)


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
            # Remove associated edges
            to_remove = [k for k in self._edges if device_id in k]
            for k in to_remove:
                edge = self._edges.pop(k)
                edge.src_node.remove_edge(edge)
                edge.dst_node.remove_edge(edge)
                self.removeItem(edge)
            self.removeItem(node)

    def add_link_edge(self, src_id: str, dst_id: str) -> Optional[LinkEdge]:
        key = tuple(sorted([src_id, dst_id]))
        if key in self._edges:
            return None
        src_node = self._nodes.get(src_id)
        dst_node = self._nodes.get(dst_id)
        if not src_node or not dst_node:
            return None
        edge = LinkEdge(src_node, dst_node)
        self.addItem(edge)
        self._edges[key] = edge
        return edge

    def remove_link_edge(self, src_id: str, dst_id: str):
        key = tuple(sorted([src_id, dst_id]))
        edge = self._edges.pop(key, None)
        if edge:
            edge.src_node.remove_edge(edge)
            edge.dst_node.remove_edge(edge)
            self.removeItem(edge)

    def get_node(self, device_id: str) -> Optional[DeviceNode]:
        return self._nodes.get(device_id)

    def set_edge_broken(self, src_id: str, dst_id: str, broken: bool):
        key = tuple(sorted([src_id, dst_id]))
        edge = self._edges.get(key)
        if edge:
            edge.set_broken(broken)

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
            # Emit position changes for moved nodes
            for dev_id, node in self._nodes.items():
                if node.isSelected():
                    p = node.scenePos()
                    self.device_moved.emit(dev_id, p.x(), p.y())

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

        # Check edge
        for (src_id, dst_id), edge in self._edges.items():
            if edge.contains(event.scenePos() - edge.pos()):
                self.edge_right_clicked.emit(src_id, dst_id, event.screenPos())
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


class TopologyView(QGraphicsView):
    """The main topology canvas widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = TopologyScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor("#1a1a2e")))
        self._zoom_level = 1.0

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor
        if event.angleDelta().y() > 0:
            factor = zoom_in_factor
        else:
            factor = zoom_out_factor
        new_zoom = self._zoom_level * factor
        if 0.1 <= new_zoom <= 5.0:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    def fit_view(self):
        if self._scene._nodes:
            self.fitInView(self._scene.itemsBoundingRect().adjusted(-50, -50, 50, 50),
                           Qt.KeepAspectRatio)
        else:
            self.resetTransform()
            self._zoom_level = 1.0

    def reset_zoom(self):
        self.resetTransform()
        self._zoom_level = 1.0

    @property
    def topology_scene(self) -> TopologyScene:
        return self._scene

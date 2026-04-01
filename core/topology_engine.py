"""
Topology Engine - Manages the network topology using NetworkX.
"""
from __future__ import annotations
import networkx as nx
from typing import List, Tuple, Optional, Dict, Any
from core.device_manager import Device, DeviceType


class TopologyEngine:
    """Manages network topology as a NetworkX graph."""

    def __init__(self):
        self.graph = nx.Graph()
        self._node_positions: Dict[str, Tuple[float, float]] = {}

    # ------------------------------------------------------------------ #
    #  Nodes (Devices)                                                     #
    # ------------------------------------------------------------------ #

    def add_device(self, device: Device, x: float = 0.0, y: float = 0.0):
        self.graph.add_node(device.id, device=device)
        self._node_positions[device.id] = (x, y)

    def remove_device(self, device_id: str):
        if self.graph.has_node(device_id):
            self.graph.remove_node(device_id)
        self._node_positions.pop(device_id, None)

    def get_device(self, device_id: str) -> Optional[Device]:
        if self.graph.has_node(device_id):
            return self.graph.nodes[device_id].get("device")
        return None

    def get_all_devices(self) -> List[Device]:
        return [data["device"] for _, data in self.graph.nodes(data=True)]

    def set_position(self, device_id: str, x: float, y: float):
        self._node_positions[device_id] = (x, y)

    def get_position(self, device_id: str) -> Tuple[float, float]:
        return self._node_positions.get(device_id, (0.0, 0.0))

    # ------------------------------------------------------------------ #
    #  Edges (Links)                                                       #
    # ------------------------------------------------------------------ #

    def add_link(self, src_id: str, dst_id: str,
                 src_iface: int = 0, dst_iface: int = 0) -> bool:
        if src_id == dst_id:
            return False
        if self.graph.has_edge(src_id, dst_id):
            return False
        if not (self.graph.has_node(src_id) and self.graph.has_node(dst_id)):
            return False
        self.graph.add_edge(src_id, dst_id,
                            src_iface=src_iface,
                            dst_iface=dst_iface,
                            src_node=src_id,
                            dst_node=dst_id)
        # Update interface connected_to fields
        src_dev = self.get_device(src_id)
        dst_dev = self.get_device(dst_id)
        if src_dev and src_iface < len(src_dev.interfaces):
            src_dev.interfaces[src_iface].connected_to_device = dst_id
            src_dev.interfaces[src_iface].connected_to_iface = dst_iface
        if dst_dev and dst_iface < len(dst_dev.interfaces):
            dst_dev.interfaces[dst_iface].connected_to_device = src_id
            dst_dev.interfaces[dst_iface].connected_to_iface = src_iface
        return True

    def remove_link(self, src_id: str, dst_id: str):
        if self.graph.has_edge(src_id, dst_id):
            edge_data = self.graph[src_id][dst_id]
            # Clear interface connected_to
            src_dev = self.get_device(src_id)
            dst_dev = self.get_device(dst_id)
            src_iface = edge_data.get("src_iface", 0)
            dst_iface = edge_data.get("dst_iface", 0)
            if src_dev and src_iface < len(src_dev.interfaces):
                src_dev.interfaces[src_iface].connected_to_device = None
                src_dev.interfaces[src_iface].connected_to_iface = None
            if dst_dev and dst_iface < len(dst_dev.interfaces):
                dst_dev.interfaces[dst_iface].connected_to_device = None
                dst_dev.interfaces[dst_iface].connected_to_iface = None
            self.graph.remove_edge(src_id, dst_id)

    def get_links(self) -> List[Tuple[str, str, dict]]:
        return [(u, v, d) for u, v, d in self.graph.edges(data=True)]

    def get_neighbors(self, device_id: str) -> List[Device]:
        neighbors = []
        for neighbor_id in self.graph.neighbors(device_id):
            dev = self.get_device(neighbor_id)
            if dev:
                neighbors.append(dev)
        return neighbors

    def get_link_data(self, src_id: str, dst_id: str) -> Optional[dict]:
        if self.graph.has_edge(src_id, dst_id):
            return dict(self.graph[src_id][dst_id])
        return None

    def break_link(self, src_id: str, dst_id: str):
        """Mark a link as broken (wire disconnected). Sets oper_status=2 on both interfaces."""
        if not self.graph.has_edge(src_id, dst_id):
            return
        edge = self.graph[src_id][dst_id]
        edge["broken"] = True
        self._set_iface_oper_status(src_id, dst_id, edge, 2)

    def restore_link(self, src_id: str, dst_id: str):
        """Restore a broken link. Sets oper_status=1 on both interfaces."""
        if not self.graph.has_edge(src_id, dst_id):
            return
        edge = self.graph[src_id][dst_id]
        edge["broken"] = False
        self._set_iface_oper_status(src_id, dst_id, edge, 1)

    def is_link_broken(self, src_id: str, dst_id: str) -> bool:
        if self.graph.has_edge(src_id, dst_id):
            return bool(self.graph[src_id][dst_id].get("broken", False))
        return False

    def _set_iface_oper_status(self, src_id: str, dst_id: str, edge: dict, status: int):
        src_dev = self.get_device(src_id)
        dst_dev = self.get_device(dst_id)
        if edge.get("src_node") == src_id:
            si, di = edge.get("src_iface", 0), edge.get("dst_iface", 0)
        else:
            si, di = edge.get("dst_iface", 0), edge.get("src_iface", 0)
        if src_dev and si < len(src_dev.interfaces):
            src_dev.interfaces[si].oper_status = status
        if dst_dev and di < len(dst_dev.interfaces):
            dst_dev.interfaces[di].oper_status = status

    # ------------------------------------------------------------------ #
    #  Analysis                                                            #
    # ------------------------------------------------------------------ #

    def get_switches(self) -> List[Device]:
        return [d for d in self.get_all_devices() if d.device_type == DeviceType.SWITCH]

    def get_paths(self, src_id: str, dst_id: str) -> List[List[str]]:
        try:
            return list(nx.all_simple_paths(self.graph, src_id, dst_id))
        except nx.NetworkXNoPath:
            return []

    def is_connected(self) -> bool:
        return nx.is_connected(self.graph) if self.graph.number_of_nodes() > 0 else False

    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    # ------------------------------------------------------------------ #
    #  Serialization                                                       #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            dev = data.get("device")
            pos = self._node_positions.get(node_id, (0, 0))
            nodes.append({
                "id": node_id,
                "position": {"x": pos[0], "y": pos[1]},
                "device": dev.to_dict() if dev else None,
            })

        edges = []
        for u, v, data in self.graph.edges(data=True):
            edges.append({
                "src": u,
                "dst": v,
                "src_iface": data.get("src_iface", 0),
                "dst_iface": data.get("dst_iface", 0),
                "broken": data.get("broken", False),
            })

        return {"nodes": nodes, "edges": edges}

    def from_dict(self, data: Dict[str, Any]):
        from core.device_manager import Device
        self.graph.clear()
        self._node_positions.clear()

        for node_data in data.get("nodes", []):
            dev_data = node_data.get("device")
            if dev_data:
                device = Device.from_dict(dev_data)
                pos = node_data.get("position", {"x": 0, "y": 0})
                self.add_device(device, x=pos["x"], y=pos["y"])

        for edge_data in data.get("edges", []):
            self.add_link(
                edge_data["src"], edge_data["dst"],
                edge_data.get("src_iface", 0),
                edge_data.get("dst_iface", 0),
            )
            if edge_data.get("broken", False):
                self.break_link(edge_data["src"], edge_data["dst"])

    def clear(self):
        self.graph.clear()
        self._node_positions.clear()

    # ------------------------------------------------------------------ #
    #  Templates                                                           #
    # ------------------------------------------------------------------ #

    def apply_template(self, template_name: str, device_manager, ip_manager):
        """Apply a pre-built topology template."""
        from core.device_manager import DeviceType, Vendor, Device
        import random

        templates = {
            "data_center": self._template_data_center,
            "enterprise_lan": self._template_enterprise_lan,
            "campus_network": self._template_campus_network,
        }
        fn = templates.get(template_name)
        if fn:
            fn(device_manager, ip_manager)

    def _make_device(self, name, dtype, vendor, ip_manager, ifaces=4):
        from core.device_manager import Device
        return Device(
            name=name,
            device_type=dtype,
            vendor=vendor,
            ip_address=ip_manager.next_ip(),
            interface_count=ifaces,
        )

    def _template_data_center(self, dm, ip_mgr):
        from core.device_manager import DeviceType, Vendor
        spacing = 180
        # Core routers
        r1 = self._make_device("Core-R1", DeviceType.ROUTER, Vendor.CISCO, ip_mgr, 8)
        r2 = self._make_device("Core-R2", DeviceType.ROUTER, Vendor.CISCO, ip_mgr, 8)
        dm.add_device(r1); dm.add_device(r2)
        self.add_device(r1, 300, 100); self.add_device(r2, 500, 100)
        self.add_link(r1.id, r2.id)

        # Aggregation switches
        positions = [(200, 280), (400, 280), (600, 280)]
        switches = []
        for i, (x, y) in enumerate(positions):
            s = self._make_device(f"Agg-SW{i+1}", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 24)
            dm.add_device(s); self.add_device(s, x, y); switches.append(s)
            self.add_link(r1.id, s.id)
            self.add_link(r2.id, s.id)

        # Servers per rack
        for si, sw in enumerate(switches):
            for j in range(3):
                srv = self._make_device(f"SRV{si*3+j+1}", DeviceType.SERVER, Vendor.LINUX, ip_mgr, 2)
                dm.add_device(srv)
                sx = positions[si][0] + (j - 1) * 100
                self.add_device(srv, sx, 430)
                self.add_link(sw.id, srv.id)

    def _template_enterprise_lan(self, dm, ip_mgr):
        from core.device_manager import DeviceType, Vendor
        # Core router
        cr = self._make_device("Edge-Router", DeviceType.ROUTER, Vendor.CISCO, ip_mgr, 8)
        dm.add_device(cr); self.add_device(cr, 400, 80)

        # Distribution switches
        dist_positions = [(200, 230), (600, 230)]
        dist_switches = []
        for i, (x, y) in enumerate(dist_positions):
            s = self._make_device(f"Dist-SW{i+1}", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 24)
            dm.add_device(s); self.add_device(s, x, y); dist_switches.append(s)
            self.add_link(cr.id, s.id)

        # Access switches
        for di, ds in enumerate(dist_switches):
            for j in range(2):
                access_sw = self._make_device(f"Access-SW{di*2+j+1}", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 24)
                dm.add_device(access_sw)
                ax = dist_positions[di][0] + (j - 0.5) * 160
                self.add_device(access_sw, ax, 380)
                self.add_link(ds.id, access_sw.id)
                # Servers
                for k in range(2):
                    srv = self._make_device(f"PC-{di*4+j*2+k+1}", DeviceType.SERVER, Vendor.LINUX, ip_mgr, 1)
                    dm.add_device(srv)
                    self.add_device(srv, ax + (k - 0.5) * 100, 520)
                    self.add_link(access_sw.id, srv.id)

    def _template_campus_network(self, dm, ip_mgr):
        from core.device_manager import DeviceType, Vendor
        # Internet router
        ir = self._make_device("Internet-GW", DeviceType.ROUTER, Vendor.JUNIPER, ip_mgr, 8)
        dm.add_device(ir); self.add_device(ir, 400, 60)

        # Core switches
        cs1 = self._make_device("Core-SW1", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 48)
        cs2 = self._make_device("Core-SW2", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 48)
        dm.add_device(cs1); dm.add_device(cs2)
        self.add_device(cs1, 250, 200); self.add_device(cs2, 550, 200)
        self.add_link(ir.id, cs1.id); self.add_link(ir.id, cs2.id)
        self.add_link(cs1.id, cs2.id)

        buildings = [
            ("Building-A", 150, 350),
            ("Building-B", 400, 350),
            ("Building-C", 650, 350),
        ]
        for i, (bldg, bx, by) in enumerate(buildings):
            fl_sw = self._make_device(f"{bldg}-SW", DeviceType.SWITCH, Vendor.CISCO, ip_mgr, 24)
            dm.add_device(fl_sw); self.add_device(fl_sw, bx, by)
            self.add_link(cs1.id if i % 2 == 0 else cs2.id, fl_sw.id)
            for j in range(3):
                srv = self._make_device(f"{bldg}-WS{j+1}", DeviceType.SERVER, Vendor.LINUX, ip_mgr, 1)
                dm.add_device(srv)
                self.add_device(srv, bx + (j - 1) * 110, 480)
                self.add_link(fl_sw.id, srv.id)

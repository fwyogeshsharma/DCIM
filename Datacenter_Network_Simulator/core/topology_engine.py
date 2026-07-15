"""
Topology Engine - Manages the network topology using NetworkX.
"""
from __future__ import annotations
import threading
import networkx as nx
from typing import List, Tuple, Optional, Dict, Any
from core.device_manager import Device, DeviceType


class TopologyEngine:
    """Manages network topology as a NetworkX multi-graph.

    MultiGraph is used so that two nodes can share more than one edge (e.g. a
    PDU that has both a *power* edge and a *management* edge to the same OOB
    switch).  Each edge is keyed by its layer string ("production", "management",
    "power") so we can look up or remove a specific layer edge without touching
    the others.
    """

    def __init__(self):
        self.graph = nx.MultiGraph()
        # Fleet Lifecycle adds/removes nodes and edges from its scheduler thread
        # while the API (graph/floorplan endpoints) and the state-store ticker
        # iterate the graph (get_links / get_edges_by_layer / get_all_devices).
        # networkx graphs are not thread-safe, so a reentrant lock serialises
        # every graph mutation against every full-graph iteration — otherwise a
        # live add/remove raises "dictionary changed size during iteration".
        self._lock = threading.RLock()
        self._node_positions: Dict[str, Tuple[float, float]] = {}
        # Floor-plan extent block (rooms/aisles/pitches) from the loaded topology.
        # Not graph data — kept so a live floor-plan export can pair the room
        # geometry with the current (incl. fleet-added) device placements.
        self.floorplan: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    #  Nodes (Devices)                                                     #
    # ------------------------------------------------------------------ #

    def add_device(self, device: Device, x: float = 0.0, y: float = 0.0):
        with self._lock:
            self.graph.add_node(device.id, device=device)
            self._node_positions[device.id] = (x, y)

    def remove_device(self, device_id: str):
        with self._lock:
            if self.graph.has_node(device_id):
                self.graph.remove_node(device_id)
            self._node_positions.pop(device_id, None)

    def get_device(self, device_id: str) -> Optional[Device]:
        if self.graph.has_node(device_id):
            return self.graph.nodes[device_id].get("device")
        return None

    def get_all_devices(self) -> List[Device]:
        with self._lock:
            return [data["device"] for _, data in self.graph.nodes(data=True)]

    def set_position(self, device_id: str, x: float, y: float):
        with self._lock:
            self._node_positions[device_id] = (x, y)

    def get_position(self, device_id: str) -> Tuple[float, float]:
        return self._node_positions.get(device_id, (0.0, 0.0))

    # ------------------------------------------------------------------ #
    #  Edges (Links)                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _next_free_iface(device) -> int:
        """Return index of the first interface not yet connected to any device."""
        for i, iface in enumerate(device.interfaces):
            if iface.connected_to_device is None:
                return i
        return len(device.interfaces) - 1  # all occupied — reuse last

    @staticmethod
    def _edge_key(src_id: str, dst_id: str, layer: str) -> str:
        """MultiGraph edge key. One edge per (pair, layer) for most layers, but
        cooling is keyed by direction so supply + return both coexist."""
        if layer == "cooling":
            return f"cooling:{src_id}->{dst_id}"
        return layer

    @staticmethod
    def _select_edges(edges, layer: Optional[str]) -> dict:
        """Pick the {key: data} edges matching *layer*. Matches a direct key
        first (production/management/power), else by the 'layer' attribute
        (cooling uses directional keys). Returns all edges when layer is None."""
        if layer is None:
            return dict(edges)
        if layer in edges:
            return {layer: edges[layer]}
        return {k: d for k, d in edges.items()
                if d.get("layer", "production") == layer}

    def add_link(self, src_id: str, dst_id: str,
                 src_iface: Optional[int] = None,
                 dst_iface: Optional[int] = None,
                 layer: str = "production") -> bool:
        if src_id == dst_id:
            return False
        # Reject same-layer duplicates only; different layers between the same
        # pair are intentional (e.g. power + management to the same OOB switch).
        # Cooling supply & return are distinct DIRECTIONAL flows on one pair
        # (CDU→server cold-plate supply vs server→CDU warm return), so cooling
        # edges are keyed by direction — both survive instead of the second
        # being rejected as a duplicate.
        edge_key = self._edge_key(src_id, dst_id, layer)
        with self._lock:
            if self.graph.has_edge(src_id, dst_id, key=edge_key):
                return False
            if not (self.graph.has_node(src_id) and self.graph.has_node(dst_id)):
                return False
            src_dev = self.get_device(src_id)
            dst_dev = self.get_device(dst_id)
            # Honour an explicitly-chosen port on any layer (the manual link builder
            # passes them); fall back to the next free port when not given — the
            # long-standing auto behaviour every other caller relies on.
            if src_iface is None:
                src_iface = self._next_free_iface(src_dev) if src_dev else 0
            if dst_iface is None:
                dst_iface = self._next_free_iface(dst_dev) if dst_dev else 0
            self.graph.add_edge(src_id, dst_id,
                                key=edge_key,
                                src_iface=src_iface,
                                dst_iface=dst_iface,
                                src_node=src_id,
                                dst_node=dst_id,
                                layer=layer)
            if layer in ("production", "management"):
                if src_dev and src_iface < len(src_dev.interfaces):
                    src_dev.interfaces[src_iface].connected_to_device = dst_id
                    src_dev.interfaces[src_iface].connected_to_iface = dst_iface
                if dst_dev and dst_iface < len(dst_dev.interfaces):
                    dst_dev.interfaces[dst_iface].connected_to_device = src_id
                    dst_dev.interfaces[dst_iface].connected_to_iface = src_iface
            return True

    def remove_link(self, src_id: str, dst_id: str, layer: Optional[str] = None):
        """Remove link(s) between src and dst.

        If *layer* is given only that layer's edge is removed; otherwise every
        edge between the pair is removed.
        """
        with self._lock:
            if not self.graph.has_edge(src_id, dst_id):
                return
            src_dev = self.get_device(src_id)
            dst_dev = self.get_device(dst_id)

            def _clear_iface(edge_data: dict):
                si = edge_data.get("src_iface", 0)
                di = edge_data.get("dst_iface", 0)
                if src_dev and si < len(src_dev.interfaces):
                    src_dev.interfaces[si].connected_to_device = None
                    src_dev.interfaces[si].connected_to_iface = None
                if dst_dev and di < len(dst_dev.interfaces):
                    dst_dev.interfaces[di].connected_to_device = None
                    dst_dev.interfaces[di].connected_to_iface = None

            # Copy first — removing edges while iterating mutates the dict
            sel = (self._select_edges(self.graph[src_id][dst_id], layer)
                   if layer is not None else dict(self.graph[src_id][dst_id]))
            for key, edge_data in sel.items():
                _clear_iface(edge_data)
                self.graph.remove_edge(src_id, dst_id, key=key)

    def get_links(self) -> List[Tuple[str, str, dict]]:
        with self._lock:
            return [(u, v, d) for u, v, d in self.graph.edges(data=True)]

    def get_edges_by_layer(self, layer: str) -> List[Tuple[str, str, dict]]:
        with self._lock:
            return [(u, v, d) for u, v, d in self.graph.edges(data=True)
                    if d.get("layer", "production") == layer]

    def get_all_layers(self) -> list:
        with self._lock:
            layers = {d.get("layer", "production")
                      for _, _, d in self.graph.edges(data=True)}
        return sorted(layers)

    def get_neighbors(self, device_id: str) -> List[Device]:
        with self._lock:
            neighbor_ids = list(self.graph.neighbors(device_id)) \
                if self.graph.has_node(device_id) else []
        neighbors = []
        for neighbor_id in neighbor_ids:
            dev = self.get_device(neighbor_id)
            if dev:
                neighbors.append(dev)
        return neighbors

    def get_adjacency(self, device_id: str) -> List[Tuple[str, dict]]:
        """Snapshot of (neighbor_id, {edge_key: data}) for one node, taken under
        the graph lock so callers can iterate it safely while the fleet mutates
        the graph from another thread."""
        with self._lock:
            if not self.graph.has_node(device_id):
                return []
            return [(nbr, dict(edges))
                    for nbr, edges in self.graph.adj[device_id].items()]

    def get_link_data(self, src_id: str, dst_id: str,
                      layer: Optional[str] = None) -> Optional[dict]:
        """Return edge data for the given pair.

        If *layer* is specified, return data for that specific layer edge.
        Otherwise return data for the first (lowest-key) edge found.
        """
        if not self.graph.has_edge(src_id, dst_id):
            return None
        edges = self.graph[src_id][dst_id]
        if layer is not None:
            sel = self._select_edges(edges, layer)
            if sel:
                return dict(next(iter(sel.values())))
        first_key = next(iter(edges))
        return dict(edges[first_key])

    def break_link(self, src_id: str, dst_id: str, layer: Optional[str] = None):
        """Mark the production link as broken; sets oper_status=2 on its interfaces.

        Only production links are breakable. Management, power and cooling/water
        links stay up regardless of the requested layer.
        """
        if layer not in (None, "production"):
            return
        with self._lock:
            if not self.graph.has_edge(src_id, dst_id):
                return
            edges = self.graph[src_id][dst_id]
            targets = self._select_edges(edges, "production")
            for key, edge in targets.items():
                edge["broken"] = True
                self._set_iface_oper_status(src_id, dst_id, edge, 2)

    def restore_link(self, src_id: str, dst_id: str, layer: Optional[str] = None):
        """Restore the broken production link; sets oper_status=1 on its interfaces.

        Mirrors break_link — only production links are affected.
        """
        if layer not in (None, "production"):
            return
        with self._lock:
            if not self.graph.has_edge(src_id, dst_id):
                return
            edges = self.graph[src_id][dst_id]
            targets = self._select_edges(edges, "production")
            for key, edge in targets.items():
                edge["broken"] = False
                self._set_iface_oper_status(src_id, dst_id, edge, 1)

    def is_link_broken(self, src_id: str, dst_id: str,
                       layer: Optional[str] = None) -> bool:
        if not self.graph.has_edge(src_id, dst_id):
            return False
        edges = self.graph[src_id][dst_id]
        sel = self._select_edges(edges, layer) if layer is not None else edges
        return any(e.get("broken", False) for e in sel.values())

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
        with self._lock:
            try:
                return list(nx.all_simple_paths(self.graph, src_id, dst_id))
            except nx.NetworkXNoPath:
                return []

    def is_connected(self) -> bool:
        with self._lock:
            return nx.is_connected(self.graph) if self.graph.number_of_nodes() > 0 else False

    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    # ------------------------------------------------------------------ #
    #  Serialization                                                       #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
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
                    "layer": data.get("layer", "production"),
                })

        out: Dict[str, Any] = {"nodes": nodes, "edges": edges}
        if self.floorplan:
            out["floorplan"] = self.floorplan
        return out

    def from_dict(self, data: Dict[str, Any]):
        from core.device_manager import Device
        with self._lock:
            self.graph.clear()
            self._node_positions.clear()
            self.floorplan = data.get("floorplan", {}) or {}

            for node_data in data.get("nodes", []):
                dev_data = node_data.get("device")
                if dev_data:
                    device = Device.from_dict(dev_data)
                    pos = node_data.get("position", {"x": 0, "y": 0})
                    self.add_device(device, x=pos["x"], y=pos["y"])

            for edge_data in data.get("edges", []):
                layer = edge_data.get("layer", "production")
                self.add_link(
                    edge_data["src"], edge_data["dst"],
                    edge_data.get("src_iface", 0),
                    edge_data.get("dst_iface", 0),
                    layer=layer,
                )
                if edge_data.get("broken", False):
                    self.break_link(edge_data["src"], edge_data["dst"], layer=layer)

    def clear(self):
        with self._lock:
            self.graph.clear()
            self._node_positions.clear()

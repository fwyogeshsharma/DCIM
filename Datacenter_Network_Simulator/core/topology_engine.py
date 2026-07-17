"""
Topology Engine - Manages the network topology using NetworkX.
"""
from __future__ import annotations
import threading
import networkx as nx
from typing import List, Tuple, Optional, Dict, Any
from core.device_manager import Device, DeviceType, feed_side


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

    # Layers that actually terminate on an Ethernet interface. Everything else is
    # a different kind of cable entirely: a power link is a C13/C14 cord from a PDU
    # OUTLET to a PSU inlet, a cooling link is a PIPE between loop connections.
    # Neither has an ifIndex, so neither gets an iface — see add_link.
    ETHERNET_LAYERS = ("production", "management")

    @staticmethod
    def _next_free_iface(device) -> int:
        """Return index of the first interface not yet connected to any device."""
        for i, iface in enumerate(device.interfaces):
            if iface.connected_to_device is None:
                return i
        return len(device.interfaces) - 1  # all occupied — reuse last

    @staticmethod
    def _power_ends(src_dev, dst_dev):
        """Orient a power edge: (supply, load, supply_is_src), or None if neither.

        The supply is the end with OUTLETS and the load is the end with PSUs — both
        conditions, not either. "Has outlets" alone is not enough: on an rpp -> pdu
        feed the PDU has outlets but is the LOAD, and its own feed is a breaker
        position on the RPP panel, not an outlet. Requiring a PSU on the far end
        rejects that cleanly, along with pdu -> sensor (a DPX2 has no PSU; it hangs
        off the sensor port) and mcc -> pump. Those keep null terminations.
        """
        if src_dev is not None and dst_dev is not None:
            if src_dev.outlets and dst_dev.psus:
                return src_dev, dst_dev, True
            if dst_dev.outlets and src_dev.psus:
                return dst_dev, src_dev, False
        return None

    def fabric_ifaces(self, device_id: str) -> set:
        """Iface indices on *device_id* carrying a production link to ANOTHER SWITCH —
        a leaf's spine uplinks and its MLAG peer-link.

        These are fabric ports, not server slots, and they must not be counted as
        server-facing capacity. Index position cannot answer this: core.rack_capacity's
        (downlink, uplink) split assumes a real chassis layout (a 93180YC-FX's 48×25G
        come before its 6×100G uplinks), but the curated topology wires its spines onto
        ports 0-3 — the FIRST ports of the downlink range. So the question "is this port
        facing the fabric?" is answered by what is actually plugged into it, read from
        the EDGES, not by where it sits.

        Read via src_node/dst_node, never the (u, v) the graph reports: the graph is
        undirected, so edges() names each link from whichever end networkx walks first,
        which is not the end src_iface was recorded against.
        """
        out: set = set()
        with self._lock:
            if not self.graph.has_node(device_id):
                return out
            for peer in self.graph[device_id]:
                peer_dev = self.get_device(peer)
                if peer_dev is None or peer_dev.device_type != DeviceType.SWITCH:
                    continue
                for _key, d in self.graph[device_id][peer].items():
                    if d.get("layer") != "production":
                        continue
                    myif = (d.get("src_iface") if d.get("src_node") == device_id
                            else d.get("dst_iface"))
                    if myif is not None:
                        out.add(myif)
        return out

    def _used_power_terminations(self, device_id: str):
        """(outlet indices, psu indices) already taken on this device, read from the
        EDGES — the same source of truth the Ethernet port picker uses."""
        outlets, psus = set(), set()
        for u, v, d in self.graph.edges(data=True):
            if d.get("layer") != "power":
                continue
            supply, load = d.get("supply_node"), d.get("load_node")
            if supply == device_id and d.get("outlet") is not None:
                outlets.add(d["outlet"])
            if load == device_id and d.get("psu") is not None:
                psus.add(d["psu"])
        return outlets, psus

    def next_free_outlet(self, pdu_id: str, want_type: str = "C13"):
        """First unused outlet of *want_type* on this PDU, or None when full.

        A PDU with no free outlet of the right type cannot take another cord — you
        cannot plug a C14 into thin air. Callers surface that as a refusal rather
        than overbooking a receptacle.
        """
        dev = self.get_device(pdu_id)
        if dev is None or not dev.outlets:
            return None
        used, _ = self._used_power_terminations(pdu_id)
        for o in dev.outlets:
            if o.type == want_type and o.index not in used:
                return o.index
        return None

    def power_feeds(self, device_id: str) -> dict:
        """psu index -> the cord feeding it: {supply_id, supply_name, outlet, feed}.

        Derived from the EDGES on every call, never cached on the PSU: a PSU's feed
        is a fact about the cord, so caching it on the device is the same mistake as
        Interface.connected_to_device — it goes stale the moment anything re-cords.
        Walks only this device's adjacency (not every edge) because Redfish calls it
        per request, and takes the engine lock because fleet churn mutates the graph
        from its own thread.
        """
        feeds: dict = {}
        with self._lock:
            if not self.graph.has_node(device_id):
                return feeds
            for peer in self.graph[device_id]:
                for _key, d in self.graph[device_id][peer].items():
                    if d.get("layer") != "power" or d.get("load_node") != device_id:
                        continue
                    psu = d.get("psu")
                    if psu is None:
                        continue
                    sup = self.get_device(d.get("supply_node"))
                    feeds[psu] = {
                        "supply_id": d.get("supply_node"),
                        "supply_name": sup.name if sup else d.get("supply_node"),
                        "supply_model": getattr(sup, "model_name", "") if sup else "",
                        "outlet": d.get("outlet"),
                        "feed": feed_side(sup.name) if sup else "",
                    }
        return feeds

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
                 layer: str = "production",
                 outlet: Optional[int] = None,
                 psu: Optional[int] = None) -> bool:
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
            power_ends = None
            if layer not in self.ETHERNET_LAYERS:
                # Power and cooling do not land on an interface at all. Allocating
                # one here is what put every power cord and every pipe on iface 0 /
                # eth0: a lie that then reads back as a real termination. Carry None
                # and let the pipe stay unmodelled rather than mismodelled.
                src_iface = dst_iface = None
                if layer == "power":
                    power_ends = self._power_ends(src_dev, dst_dev)
                    if power_ends is not None:
                        supply, load, _ = power_ends
                        # The cord is decided by the PSU's inlet: a C14 takes a C13
                        # outlet, a C20 takes a C19. Picking by anything else would
                        # let a 16A load hang off a 10A receptacle.
                        want = "C19" if load.psus[0].inlet == "C20" else "C13"
                        used_out, used_psu = self._used_power_terminations(supply.id)
                        if outlet is None:
                            outlet = self.next_free_outlet(supply.id, want)
                            if outlet is None:
                                return False      # PDU out of outlets of this type
                        elif outlet in used_out:
                            return False          # receptacle already occupied
                        if psu is None:
                            _, used_psu_load = self._used_power_terminations(load.id)
                            psu = next((p.index for p in load.psus
                                        if p.index not in used_psu_load), None)
                            if psu is None:
                                return False      # every PSU already corded
            else:
                # Honour an explicitly-chosen port (the manual link builder passes
                # them); fall back to the next free port when not given — the
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
            if power_ends is not None:
                supply, load, _ = power_ends
                # supply_node/load_node, not src/dst: power FLOWS, and which end
                # feeds which is not the same question as which end the caller
                # happened to name first.
                self.graph[src_id][dst_id][edge_key].update(
                    outlet=outlet, psu=psu,
                    supply_node=supply.id, load_node=load.id)
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
                # None on power/cooling — those edges hold no iface. The old `, 0`
                # default was worse than a crash: pulling a power cord cleared the
                # device's iface 0 termination cache, silently unlinking whatever
                # ETHERNET link happened to live on port 0.
                si = edge_data.get("src_iface")
                di = edge_data.get("dst_iface")
                if src_dev and si is not None and si < len(src_dev.interfaces):
                    src_dev.interfaces[si].connected_to_device = None
                    src_dev.interfaces[si].connected_to_iface = None
                if dst_dev and di is not None and di < len(dst_dev.interfaces):
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
                # src_iface/dst_iface are recorded against src_node/dst_node, and an
                # undirected edges() walk can report (u, v) the other way round —
                # emitting u/v here would pair each end with the far side's port.
                edges.append({
                    "src": data.get("src_node", u),
                    "dst": data.get("dst_node", v),
                    # null, not 0, on power/cooling — see add_link. Defaulting to 0
                    # here would re-mint the fiction on every save.
                    "src_iface": data.get("src_iface"),
                    "dst_iface": data.get("dst_iface"),
                    "broken": data.get("broken", False),
                    "layer": data.get("layer", "production"),
                })
                if data.get("outlet") is not None:
                    edges[-1].update(
                        outlet=data["outlet"], psu=data.get("psu"),
                        supply_node=data.get("supply_node"),
                        load_node=data.get("load_node"))

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
                # No 0 default: a missing iface means "unknown", which add_link
                # resolves per layer (auto-pick for Ethernet, None for power/cooling).
                self.add_link(
                    edge_data["src"], edge_data["dst"],
                    edge_data.get("src_iface"),
                    edge_data.get("dst_iface"),
                    layer=layer,
                    outlet=edge_data.get("outlet"),
                    psu=edge_data.get("psu"),
                )
                if edge_data.get("broken", False):
                    self.break_link(edge_data["src"], edge_data["dst"], layer=layer)

    def clear(self):
        with self._lock:
            self.graph.clear()
            self._node_positions.clear()

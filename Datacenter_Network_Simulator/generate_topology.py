"""
Topology generator script - produces large example JSON files.
Run from the project root:  python generate_topology.py
"""
import math
import sys, json, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.device_manager import Device, DeviceType, Vendor
from core.ip_manager import IPManager

_SERVER_VENDORS = [Vendor.DELL, Vendor.HPE, Vendor.LENOVO, Vendor.SUPERMICRO]
_SENSOR_VENDORS = [Vendor.RARITAN, Vendor.VERTIV, Vendor.APC]
_OOB_MODELS = {
    Vendor.CISCO_SYSTEMS: "Cisco Catalyst 1000-48T",
    Vendor.HPE:           "HPE Aruba 2530-48G",
    Vendor.DELL:          "Dell N1148T-ON",
}
_SENSOR_MODELS = {
    Vendor.RARITAN: "Raritan DPX2-T1H1",
    Vendor.VERTIV:  "Vertiv Geist GTHD",
    Vendor.APC:     "APC NetBotz 250",
}

TOPOLOGIES_DIR = Path("topologies")
TOPOLOGIES_DIR.mkdir(exist_ok=True)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

class TopologyBuilder:
    def __init__(self, ip_subnet="10.0.0.0/16"):
        self.ip = IPManager(subnet=ip_subnet, start_offset=1)
        self.nodes = []
        self.edges = []
        self._edge_set = set()
        self._iface_next: dict = {}   # device_id -> next free interface index
        self._all_devices: list = []  # all Device objects ever added

    def add(self, name, dtype, vendor, ifaces, x, y,
            community="public", port=161, ip_address=None, mgmt_only=False):
        """Add a device node.

        mgmt_only=True: device lives only on the management network (OOB switches,
        sensors, PDUs, UPS).  No production IP is allocated; ip_address is left
        empty so SNMP/binding uses mgmt_ip only.
        """
        if ip_address is not None:
            actual_ip = ip_address
        elif mgmt_only:
            actual_ip = ""   # no prod IP; mgmt_ip assigned later by wire_to_mgmt
        else:
            actual_ip = self.ip.next_ip()
        dev = Device(
            name=name,
            device_type=dtype,
            vendor=vendor,
            ip_address=actual_ip,
            snmp_port=port,
            snmp_community=community,
            interface_count=ifaces,
        )
        self._iface_next[dev.id] = 0
        self._all_devices.append(dev)
        self.nodes.append({
            "id": dev.id,
            "position": {"x": round(x), "y": round(y)},
            "device": dev.to_dict(),
        })
        return dev

    def _alloc_iface(self, dev, iface_count: int) -> int:
        """Return next free interface index for a device, clamped to its count."""
        idx = self._iface_next.get(dev.id, 0)
        self._iface_next[dev.id] = idx + 1
        return min(idx, iface_count - 1)

    def link(self, a, b, ai=None, bi=None, layer="production"):
        key = (tuple(sorted([a.id, b.id])), layer)
        if key not in self._edge_set:
            self._edge_set.add(key)
            if ai is None:
                ai = self._alloc_iface(a, a.interface_count)
            if bi is None:
                bi = self._alloc_iface(b, b.interface_count)
            self.edges.append({
                "src": a.id, "dst": b.id,
                "src_iface": ai, "dst_iface": bi,
                "layer": layer,
            })

    # ---------------------------------------------------------------- #
    #  Management network                                               #
    # ---------------------------------------------------------------- #

    def add_mgmt_network(self, devices: list, dc_id: int = 1,
                         mgmt_subnet_base: str = "192.168",
                         oob_vendor: Vendor = Vendor.CISCO_SYSTEMS,
                         ports_per_oob: int = 46,
                         n_sensors: int = 2) -> dict:
        """Create OOB management switches, wire all devices via mgmt layer,
        and add environmental sensors.

        Returns {"oob_switches": [...], "sensors": [...]}.
        """
        if not devices:
            return {"oob_switches": [], "sensors": []}

        # Collect positions of all devices in this group
        dev_ids = {dev.id for dev in devices}
        pos_map: dict = {}
        for node in self.nodes:
            if node["id"] in dev_ids:
                pos_map[node["id"]] = (node["position"]["x"], node["position"]["y"])

        if not pos_map:
            return {"oob_switches": [], "sensors": []}

        xs = [p[0] for p in pos_map.values()]
        ys = [p[1] for p in pos_map.values()]
        x_min, x_max = min(xs), max(xs)
        y_max = max(ys)
        oob_y = y_max + 260

        # How many OOB switches needed
        n_oob = max(1, math.ceil(len(devices) / ports_per_oob))
        if n_oob == 1:
            oob_xs = [(x_min + x_max) / 2]
        else:
            oob_xs = [x_min + i * (x_max - x_min) / (n_oob - 1) for i in range(n_oob)]

        # Separate management IP pool per DC
        mgmt_ip_pool = IPManager(
            subnet=f"{mgmt_subnet_base}.{dc_id}.0/24",
            start_offset=0,
        )

        # Create OOB switches
        oob_switches = []
        oob_model = _OOB_MODELS.get(oob_vendor, "Cisco Catalyst 1000-48T")
        for i, ox in enumerate(oob_xs):
            oob_mgmt_ip = mgmt_ip_pool.next_ip()
            oob = self.add(
                f"OOB-SW-DC{dc_id}-{i+1:02d}",
                DeviceType.OOB_SWITCH,
                oob_vendor,
                ports_per_oob + 4,
                ox, oob_y,
                mgmt_only=True,   # no prod IP; management-only device
            )
            for node in self.nodes:
                if node["id"] == oob.id:
                    node["device"]["model_name"] = oob_model
                    node["device"]["mgmt_ip"] = oob_mgmt_ip
                    node["device"]["snmp_community"] = oob_mgmt_ip
                    break
            oob_switches.append(oob)

        # Uplink mesh between OOB switches
        for i in range(len(oob_switches) - 1):
            self.link(oob_switches[i], oob_switches[i + 1], layer="management")

        # Update device_positions map with OOB switch positions
        for oob in oob_switches:
            for node in self.nodes:
                if node["id"] == oob.id:
                    pos_map[oob.id] = (node["position"]["x"], node["position"]["y"])
                    break

        # Assign mgmt IPs and wire each device to nearest OOB switch
        for device in devices:
            dev_x = pos_map.get(device.id, ((x_min + x_max) / 2, 0))[0]
            nearest = min(
                oob_switches,
                key=lambda o: abs(pos_map[o.id][0] - dev_x)
            )
            dev_mgmt_ip = mgmt_ip_pool.next_ip()
            for node in self.nodes:
                if node["id"] == device.id:
                    node["device"]["mgmt_ip"] = dev_mgmt_ip
                    node["device"]["snmp_community"] = dev_mgmt_ip
                    break
            self.link(device, nearest, layer="management")

        # Add environmental sensors
        sensors = []
        sensor_y = oob_y + 160
        for s in range(n_sensors):
            sv = _SENSOR_VENDORS[s % len(_SENSOR_VENDORS)]
            sx = x_min + (s + 0.5) * (x_max - x_min + 100) / max(n_sensors, 1) - 50
            sensor_mgmt_ip = mgmt_ip_pool.next_ip()
            sensor = self.add(
                f"SENSOR-DC{dc_id}-{s+1:02d}",
                DeviceType.SENSOR,
                sv,
                1,
                sx, sensor_y,
                mgmt_only=True,   # no prod IP; management-only device
            )
            sensors.append(sensor)
            sensor_model = _SENSOR_MODELS.get(sv, "Raritan DPX2-T1H1")
            for node in self.nodes:
                if node["id"] == sensor.id:
                    node["device"]["model_name"] = sensor_model
                    node["device"]["mgmt_ip"] = sensor_mgmt_ip
                    node["device"]["snmp_community"] = sensor_mgmt_ip
                    break
            nearest = min(
                oob_switches,
                key=lambda o: abs(pos_map[o.id][0] - sx)
            )
            self.link(sensor, nearest, layer="management")

        return {"oob_switches": oob_switches, "sensors": sensors,
                "mgmt_ip_pool": mgmt_ip_pool}

    # ---------------------------------------------------------------- #
    #  Power chain                                                       #
    # ---------------------------------------------------------------- #

    def add_power_chain(self, devices: list, dc_id: int = 1,
                        ups_vendor: Vendor = Vendor.APC,
                        pdu_vendor: Vendor = Vendor.APC,
                        floor_pdu_vendor: Vendor = Vendor.EATON,
                        devices_per_rack: int = 20) -> dict:
        """Create floor PDU → UPS → rack PDU → device power-layer edges.

        Returns dict with keys 'floor_pdu', 'ups_list', 'rack_pdus'.
        """
        if not devices:
            return {}

        pos_map: dict = {}
        dev_ids = {dev.id for dev in devices}
        for node in self.nodes:
            if node["id"] in dev_ids:
                pos_map[node["id"]] = (node["position"]["x"], node["position"]["y"])

        xs = [p[0] for p in pos_map.values()]
        ys = [p[1] for p in pos_map.values()]
        x_min, x_max = min(xs), max(xs)
        y_max = max(ys)
        power_y = y_max + 520

        # Floor PDU (single, centred) — management-only, no prod IP
        floor_pdu = self.add(
            f"FPDU-DC{dc_id}", DeviceType.FLOOR_PDU, floor_pdu_vendor,
            1, (x_min + x_max) / 2, power_y, mgmt_only=True,
        )
        for node in self.nodes:
            if node["id"] == floor_pdu.id:
                node["device"]["model_name"] = "Eaton PDU 80kVA"
                break

        # Split devices into racks
        n_racks = max(1, math.ceil(len(devices) / devices_per_rack))
        rack_width = (x_max - x_min + 200) / n_racks
        ups_list = []
        rack_pdus = []

        for r in range(n_racks):
            rack_cx = x_min + (r + 0.5) * rack_width - 100
            ups_y = power_y - 160
            rack_pdu_y = power_y - 320

            ups = self.add(
                f"UPS-DC{dc_id}-R{r+1}", DeviceType.UPS, ups_vendor,
                1, rack_cx, ups_y, mgmt_only=True,
            )
            for node in self.nodes:
                if node["id"] == ups.id:
                    node["device"]["model_name"] = "APC Smart-UPS 3000"
                    break
            ups_list.append(ups)
            self.link(floor_pdu, ups, layer="power")

            rack_pdu = self.add(
                f"PDU-DC{dc_id}-R{r+1}", DeviceType.PDU, pdu_vendor,
                1, rack_cx, rack_pdu_y, mgmt_only=True,
            )
            for node in self.nodes:
                if node["id"] == rack_pdu.id:
                    node["device"]["model_name"] = "APC AP8941"
                    break
            rack_pdus.append(rack_pdu)
            self.link(ups, rack_pdu, layer="power")

            # Assign rack devices to this rack PDU by x-proximity
            rack_x_start = x_min + r * rack_width - 100
            rack_x_end = rack_x_start + rack_width
            for device in devices:
                dx = pos_map.get(device.id, (0, 0))[0]
                if rack_x_start <= dx < rack_x_end:
                    for node in self.nodes:
                        if node["id"] == device.id:
                            node["device"]["power_source"] = rack_pdu.id
                            node["device"]["ups_backup"] = ups.id
                            break
                    self.link(rack_pdu, device, layer="power")

        return {"floor_pdu": floor_pdu, "ups_list": ups_list, "rack_pdus": rack_pdus}

    def wire_to_mgmt(self, devices: list, oob_switches: list,
                     mgmt_ip_pool=None):
        """Wire additional devices (e.g. power chain) to their nearest OOB switch.

        If mgmt_ip_pool is provided (the IPManager returned by add_mgmt_network),
        each device also gets a mgmt_ip assigned so the DCIM can reach it via SNMP.
        """
        if not devices or not oob_switches:
            return
        oob_pos = {}
        for oob in oob_switches:
            for node in self.nodes:
                if node["id"] == oob.id:
                    oob_pos[oob.id] = node["position"]["x"]
                    break
        for device in devices:
            dev_x = 0
            for node in self.nodes:
                if node["id"] == device.id:
                    dev_x = node["position"]["x"]
                    break
            nearest = min(oob_switches, key=lambda o: abs(oob_pos[o.id] - dev_x))
            self.link(device, nearest, layer="management")
            if mgmt_ip_pool:
                mgmt_ip = mgmt_ip_pool.next_ip()
                for node in self.nodes:
                    if node["id"] == device.id:
                        node["device"]["mgmt_ip"] = mgmt_ip
                        node["device"]["snmp_community"] = mgmt_ip
                        break

    def add_ev2_monitors(self, pdus: list, dc_id: int = 1,
                         circuits_per_pdu: int = 42,
                         name_prefix: str = "R",
                         mgmt_ip_pool=None) -> list:
        """Mount one Verdigris EV2 on every PDU with CT coils on each circuit.

        Physical model:
          - Rack PDU  → EV2 CT coils on each outlet (server-level granularity)
          - Floor PDU → EV2 CT coils on each breaker circuit (rack-level granularity)

        circuits_per_pdu: 24 / 42 / 84 — must match the PDU breaker/outlet count.
        name_prefix:      "R" for rack PDUs  →  EV2-DCx-R01
                          "FP" for floor PDU →  EV2-DCx-FP01

        Each EV2 is positioned beside its PDU on the canvas and linked via a
        power-layer edge (representing the physical CT wiring).

        Returns list of EV2 Device objects added.
        """
        if not pdus:
            return []

        pdu_ids = {p.id for p in pdus}
        pdu_pos: dict = {}
        for node in self.nodes:
            if node["id"] in pdu_ids:
                pdu_pos[node["id"]] = node["position"]

        ev2_devices = []
        for p_idx, pdu in enumerate(pdus):
            pos = pdu_pos.get(pdu.id)
            if not pos:
                continue

            # Place EV2 directly beside the PDU (80 px to the right)
            cx = pos["x"] + 80
            cy = pos["y"]

            ev2 = self.add(
                f"EV2-DC{dc_id}-{name_prefix}{p_idx+1:02d}",
                DeviceType.ENERGY_MONITOR,
                Vendor.VERDIGRIS,
                1,          # 1 logical interface (BACnet/IP)
                cx, cy,
                mgmt_only=True,
            )
            for node in self.nodes:
                if node["id"] == ev2.id:
                    node["device"]["model_name"]   = f"Verdigris EV2-{circuits_per_pdu}"
                    node["device"]["monitored_pdu"] = pdu.id
                    # Flag which level this monitor sits at
                    node["device"]["pdu_level"] = (
                        "floor" if name_prefix == "FP" else "rack"
                    )
                    break

            # Power edge = CT coil wiring
            self.link(ev2, pdu, layer="power")

            if mgmt_ip_pool:
                mgmt_ip = mgmt_ip_pool.next_ip()
                for node in self.nodes:
                    if node["id"] == ev2.id:
                        node["device"]["mgmt_ip"]        = mgmt_ip
                        node["device"]["snmp_community"] = mgmt_ip
                        break

            ev2_devices.append(ev2)

        return ev2_devices

    def to_dict(self):
        layers = sorted({e.get("layer", "production") for e in self.edges})
        return {
            "metadata": {
                "has_management_layer": "management" in layers,
                "has_power_layer": "power" in layers,
            },
            "nodes": self.nodes,
            "edges": self.edges,
        }

    def save(self, filename):
        path = TOPOLOGIES_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        n_dev = len(self.nodes)
        n_lnk = len(self.edges)
        prod  = sum(1 for e in self.edges if e.get("layer", "production") == "production")
        mgmt  = sum(1 for e in self.edges if e.get("layer") == "management")
        pwr   = sum(1 for e in self.edges if e.get("layer") == "power")
        print(f"  Saved {path.name}  ({n_dev} devices, {n_lnk} links"
              f"  [prod:{prod} mgmt:{mgmt} power:{pwr}])")
        return path


# ------------------------------------------------------------------ #
#  Topology 1: Large Three-Tier Data Center  (~106 devices)           #
# ------------------------------------------------------------------ #

def build_three_tier_datacenter():
    """
    2 Core Routers
    4 Aggregation Switches (2 per pod)
    10 Top-of-Rack Switches  (5 per pod)
    90 Servers               (9 per ToR)
    Total: 106 devices, 135 links
    """
    t = TopologyBuilder("10.1.0.0/16")

    # Canvas sizing
    POD_W       = 750    # width of one pod
    CORE_Y      = 80
    AGG_Y       = 280
    TOR_Y       = 480
    SRV_Y       = 680
    CANVAS_CX   = 750    # total canvas centre-x

    # ---- Core layer (2 routers) ----
    core_r1 = t.add("Core-R1", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,    8, 600, CORE_Y)
    core_r2 = t.add("Core-R2", DeviceType.ROUTER, Vendor.JUNIPER_NETWORKS,  8, 900, CORE_Y)
    t.link(core_r1, core_r2)

    all_tors = []
    prod_devices = [core_r1, core_r2]

    for pod_idx, (pod_label, pod_cx) in enumerate([("A", 450), ("B", 1050)]):
        # ---- Aggregation layer (2 per pod) ----
        agg_xs = [pod_cx - 150, pod_cx + 150]
        aggs = []
        for ai, ax in enumerate(agg_xs):
            agg = t.add(f"Agg-SW{pod_idx*2+ai+1}",
                        DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, ax, AGG_Y)
            aggs.append(agg)
            prod_devices.append(agg)
            t.link(core_r1, agg)
            t.link(core_r2, agg)

        # Cross-link the two aggregation switches in each pod
        t.link(aggs[0], aggs[1])

        # ---- Top-of-Rack layer (5 per pod) ----
        tor_xs = [pod_cx + (i - 2) * 140 for i in range(5)]
        for ti, tx in enumerate(tor_xs):
            tor = t.add(f"ToR-{pod_label}{ti+1}",
                        DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, tx, TOR_Y)
            all_tors.append(tor)
            prod_devices.append(tor)
            # Each ToR connects to both agg switches in its pod
            t.link(aggs[0], tor)
            t.link(aggs[1], tor)

            # ---- Server layer (9 per ToR) ----
            srv_count = 9
            srv_xs = [tx + (si - (srv_count-1)/2) * 80 for si in range(srv_count)]
            for si, sx in enumerate(srv_xs):
                # Alternate vendors for variety
                vendor = _SERVER_VENDORS[si % len(_SERVER_VENDORS)]
                srv = t.add(f"SRV-{pod_label}{ti+1}-{si+1:02d}",
                            DeviceType.SERVER, vendor, 2, sx, SRV_Y)
                prod_devices.append(srv)
                t.link(tor, srv)

    # ---- Management network ----
    mgmt = t.add_mgmt_network(prod_devices, dc_id=1, n_sensors=2)

    # ---- Power chain (OOB switches and sensors also run on electricity) ----
    all_powered = prod_devices + mgmt['oob_switches'] + mgmt['sensors']
    power = t.add_power_chain(all_powered, dc_id=1)
    t.wire_to_mgmt([power['floor_pdu']] + power['ups_list'] + power['rack_pdus'],
                   mgmt['oob_switches'], mgmt_ip_pool=mgmt['mgmt_ip_pool'])

    return t


# ------------------------------------------------------------------ #
#  Topology 2: Spine-Leaf Data Center  (~112 devices)                 #
# ------------------------------------------------------------------ #

def build_spine_leaf():
    """
    Modern spine-leaf (Clos) fabric:
      4 Spine switches  (Cisco, 32-port)
      8 Leaf  switches  (Cisco, 48-port)
      1 Border router   (Juniper)
      1 OOB management switch
      96 Servers         (12 per leaf)
      2  Storage nodes
    Total: 112 devices
    """
    t = TopologyBuilder("10.2.0.0/16")

    SPINE_Y     = 100
    LEAF_Y      = 340
    SRV_Y       = 560
    SPINE_XS    = [250, 450, 750, 950]
    LEAF_XS     = [100, 250, 400, 550, 650, 800, 950, 1100]

    # ---- Border router ----
    border = t.add("Border-GW", DeviceType.ROUTER, Vendor.JUNIPER_NETWORKS, 8, 600, -80)

    # ---- OOB management switch ----
    oob = t.add("OOB-MGMT-SW", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, 1250, 100)
    t.link(border, oob)

    # ---- Spine layer ----
    spines = []
    for i, sx in enumerate(SPINE_XS):
        sp = t.add(f"Spine-{i+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 32, sx, SPINE_Y)
        spines.append(sp)
        t.link(border, sp)

    # Full mesh between spine switches
    for i in range(len(spines)):
        for j in range(i+1, len(spines)):
            t.link(spines[i], spines[j])

    # ---- Leaf layer ----
    leaves = []
    for li, lx in enumerate(LEAF_XS):
        leaf = t.add(f"Leaf-{li+1:02d}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, lx, LEAF_Y)
        leaves.append(leaf)
        t.link(oob, leaf)
        # Every leaf connects to every spine (full Clos)
        for sp in spines:
            t.link(sp, leaf)

    # ---- Servers (12 per leaf) ----
    srv_count = 12
    for li, leaf in enumerate(leaves):
        base_x = LEAF_XS[li]
        for si in range(srv_count):
            sx = base_x + (si - (srv_count-1)/2) * 70
            vendor = random.choice(_SERVER_VENDORS)
            srv = t.add(f"SRV-L{li+1:02d}-{si+1:02d}",
                        DeviceType.SERVER, vendor, 2, sx, SRV_Y)
            t.link(leaf, srv)

    # ---- Storage nodes (on leaf 1 and leaf 2) ----
    for si, leaf in enumerate(leaves[:2]):
        base_x = LEAF_XS[si]
        stor = t.add(f"Storage-{si+1}", DeviceType.SERVER, Vendor.IBM,
                     4, base_x + 120, SRV_Y + 160)
        t.link(leaf, stor)

    # ---- Management network ----
    prod_devices = t._all_devices[:]  # snapshot before mgmt/power devices are added
    mgmt = t.add_mgmt_network(prod_devices, dc_id=1, n_sensors=2)

    # ---- Power chain (OOB switches and sensors also run on electricity) ----
    all_powered = prod_devices + mgmt['oob_switches'] + mgmt['sensors']
    power = t.add_power_chain(all_powered, dc_id=1)
    t.wire_to_mgmt([power['floor_pdu']] + power['ups_list'] + power['rack_pdus'],
                   mgmt['oob_switches'], mgmt_ip_pool=mgmt['mgmt_ip_pool'])

    return t


# ------------------------------------------------------------------ #
#  Topology 3: Multi-Site Enterprise WAN  (~104 devices)              #
# ------------------------------------------------------------------ #

def build_enterprise_wan():
    """
    3 sites connected via WAN routers:
      Site HQ  : WAN router + core switch + 3 access switches + 24 servers/PCs
      Site A   : WAN router + 2 access switches + 16 PCs
      Site B   : WAN router + 2 access switches + 16 PCs
      Internet : 2 ISP routers + 1 firewall
    Total: ~104 devices
    """
    t = TopologyBuilder("172.16.0.0/12")

    # ---- Internet / WAN cloud ----
    isp1 = t.add("ISP1-Router",   DeviceType.ROUTER, Vendor.JUNIPER_NETWORKS, 8,  200, 60)
    isp2 = t.add("ISP2-Router",   DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,   8,  500, 60)
    fw   = t.add("Firewall-GW",   DeviceType.ROUTER, Vendor.HPE, 4,  350, 180)
    t.link(isp1, fw)
    t.link(isp2, fw)

    # ---- HQ Site (x-offset 0, y-offset 300) ----
    hq_wan  = t.add("HQ-WAN-R",     DeviceType.ROUTER, Vendor.CISCO_SYSTEMS, 8,  350, 340)
    hq_core = t.add("HQ-Core-SW",   DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, 350, 500)
    t.link(fw, hq_wan)
    t.link(hq_wan, hq_core)

    hq_access_xs = [150, 350, 550]
    hq_accesses = []
    for i, ax in enumerate(hq_access_xs):
        ac = t.add(f"HQ-Access-{i+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 24, ax, 660)
        hq_accesses.append(ac)
        t.link(hq_core, ac)

    # 8 devices per HQ access switch
    for ai, ac in enumerate(hq_accesses):
        base_x = hq_access_xs[ai]
        for di in range(8):
            dx = base_x + (di - 3.5) * 60
            srv = t.add(f"HQ-PC-{ai*8+di+1:02d}",
                        DeviceType.SERVER, Vendor.LENOVO, 1, dx, 820)
            t.link(ac, srv)

    # HQ servers: 2 dedicated rack switches, 10 servers each
    for rack in range(2):
        srv_sw = t.add(f"HQ-Rack-SW{rack+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS,
                       24, 680 + rack * 180, 500)
        t.link(hq_core, srv_sw)
        for si in range(10):
            srv = t.add(f"HQ-SRV-{rack*10+si+1:02d}",
                        DeviceType.SERVER, Vendor.DELL, 2,
                        580 + rack * 180 + (si - 4.5) * 65, 660)
            t.link(srv_sw, srv)

    # ---- Site A — dist switch + 3 access switches + 10 PCs each ----
    site_a_wan = t.add("SiteA-WAN-R", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS, 4, 1150, 340)
    t.link(fw, site_a_wan)
    siteA_dist = t.add("SiteA-Dist-SW", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 24, 1150, 480)
    t.link(site_a_wan, siteA_dist)
    for i, ax in enumerate([1000, 1150, 1300]):
        ac = t.add(f"SiteA-Access-{i+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 24, ax, 620)
        t.link(siteA_dist, ac)
        for di in range(10):
            dx = ax + (di - 4.5) * 50
            pc = t.add(f"SiteA-PC-{i*10+di+1:02d}",
                       DeviceType.SERVER, Vendor.LENOVO, 1, dx, 780)
            t.link(ac, pc)

    # ---- Site B — dist switch + 3 access switches + 10 PCs each ----
    site_b_wan = t.add("SiteB-WAN-R", DeviceType.ROUTER, Vendor.JUNIPER_NETWORKS, 4, 1700, 340)
    t.link(fw, site_b_wan)
    siteB_dist = t.add("SiteB-Dist-SW", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 24, 1700, 480)
    t.link(site_b_wan, siteB_dist)
    for i, ax in enumerate([1550, 1700, 1850]):
        ac = t.add(f"SiteB-Access-{i+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 24, ax, 620)
        t.link(siteB_dist, ac)
        for di in range(10):
            dx = ax + (di - 4.5) * 50
            pc = t.add(f"SiteB-PC-{i*10+di+1:02d}",
                       DeviceType.SERVER, Vendor.HPE, 1, dx, 780)
            t.link(ac, pc)

    # ---- Management network (single site — all sites share one mgmt domain) ----
    prod_devices = t._all_devices[:]
    mgmt = t.add_mgmt_network(prod_devices, dc_id=1, n_sensors=1)

    # ---- Power chain (HQ only; OOB switches and sensors also run on electricity) ----
    hq_devices = [d for d in prod_devices if d.name.startswith("HQ")]
    all_powered = hq_devices + mgmt['oob_switches'] + mgmt['sensors']
    power = t.add_power_chain(all_powered, dc_id=1)
    t.wire_to_mgmt([power['floor_pdu']] + power['ups_list'] + power['rack_pdus'],
                   mgmt['oob_switches'], mgmt_ip_pool=mgmt['mgmt_ip_pool'])

    return t


# ------------------------------------------------------------------ #
#  Topology 4: Hyper-Scale Cloud Pod  (~130 devices)                  #
# ------------------------------------------------------------------ #

def build_hyperscale_pod():
    """
    Hyper-scale cloud pod (Google/AWS style):
      2  Cluster routers    (Cisco)
      2  Fabric switches    (Cisco, 64-port)
      4  Pod switches       (Cisco, 48-port)
      8  ToR switches       (Cisco, 48-port)
      112 Compute servers   (Linux, 14 per ToR)
      4  GPU servers        (Linux)
      4  Storage servers    (Linux)
      2  Load balancers     (Generic)
    Total: 138 devices
    """
    t = TopologyBuilder("10.100.0.0/16")

    CLUSTER_Y = 60
    FABRIC_Y  = 220
    POD_Y     = 400
    TOR_Y     = 580
    SRV_Y     = 760

    # ---- Cluster routers ----
    cr1 = t.add("Cluster-R1", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,   8,  500, CLUSTER_Y)
    cr2 = t.add("Cluster-R2", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,   8,  900, CLUSTER_Y)
    t.link(cr1, cr2)

    # Load balancers at the top
    lb1 = t.add("LB-1", DeviceType.SERVER, Vendor.CISCO_SYSTEMS, 4, 300, CLUSTER_Y)
    lb2 = t.add("LB-2", DeviceType.SERVER, Vendor.CISCO_SYSTEMS, 4, 1100, CLUSTER_Y)
    t.link(cr1, lb1)
    t.link(cr2, lb2)

    # ---- Fabric switches (2) ----
    fab1 = t.add("Fabric-SW1", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 64, 550, FABRIC_Y)
    fab2 = t.add("Fabric-SW2", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 64, 850, FABRIC_Y)
    t.link(cr1, fab1); t.link(cr1, fab2)
    t.link(cr2, fab1); t.link(cr2, fab2)
    t.link(fab1, fab2)

    # ---- Pod switches (4) ----
    pod_xs = [300, 520, 780, 1000]
    pods = []
    for i, px in enumerate(pod_xs):
        pod = t.add(f"Pod-SW{i+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, px, POD_Y)
        pods.append(pod)
        t.link(fab1, pod)
        t.link(fab2, pod)

    # ---- ToR switches (2 per pod) ----
    tors = []
    for pi, pod in enumerate(pods):
        base_x = pod_xs[pi]
        for ti in range(2):
            tx = base_x + (ti - 0.5) * 160
            tor = t.add(f"ToR-P{pi+1}-{ti+1}", DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48,
                        tx, TOR_Y)
            tors.append(tor)
            t.link(pod, tor)

    # ---- Compute servers (14 per ToR) ----
    srv_per_tor = 14
    for ti, tor in enumerate(tors):
        pod_idx = ti // 2
        tor_idx = ti % 2
        cx = pod_xs[pod_idx] + (tor_idx - 0.5) * 160
        for si in range(srv_per_tor):
            sx = cx + (si - (srv_per_tor - 1) / 2) * 50
            srv = t.add(f"Compute-T{ti+1}-{si+1:02d}",
                        DeviceType.SERVER, _SERVER_VENDORS[si % len(_SERVER_VENDORS)], 2, sx, SRV_Y)
            t.link(tor, srv)

    # ---- GPU servers (4, on first 4 ToRs) ----
    for gi, tor in enumerate(tors[:4]):
        pod_idx = gi // 2
        cx = pod_xs[pod_idx] + ((gi % 2) - 0.5) * 160
        gpu = t.add(f"GPU-Server-{gi+1}", DeviceType.SERVER, Vendor.SUPERMICRO, 4,
                    cx + 200, SRV_Y + 120)
        t.link(tor, gpu)

    # ---- Storage servers (4, on last 4 ToRs) ----
    for si, tor in enumerate(tors[4:]):
        pod_idx = (si + 4) // 2
        cx = pod_xs[pod_idx] + (((si+4) % 2) - 0.5) * 160
        sto = t.add(f"Storage-{si+1}", DeviceType.SERVER, Vendor.IBM, 4,
                    cx - 200, SRV_Y + 120)
        t.link(tor, sto)

    # ---- Management network ----
    prod_devices = t._all_devices[:]
    mgmt = t.add_mgmt_network(prod_devices, dc_id=1, n_sensors=4)

    # ---- Power chain (OOB switches and sensors also run on electricity) ----
    all_powered = prod_devices + mgmt['oob_switches'] + mgmt['sensors']
    power = t.add_power_chain(all_powered, dc_id=1)
    t.wire_to_mgmt([power['floor_pdu']] + power['ups_list'] + power['rack_pdus'],
                   mgmt['oob_switches'], mgmt_ip_pool=mgmt['mgmt_ip_pool'])

    return t


# ------------------------------------------------------------------ #
#  Topology 5: Dual DC Enterprise  (DC1 ~202 devices, DC2 ~149 devices)#
# ------------------------------------------------------------------ #

def _build_dc(t: "TopologyBuilder", dc: str, n_spine: int, n_leaf: int,
              srv_per_leaf: int, x_offset: int,
              switch_vendor: Vendor = Vendor.CISCO_SYSTEMS,
              core_model: str = "Cisco Nexus 9364C",
              spine_model: str = "Cisco Nexus 93180YC-FX",
              leaf_model: str = "Cisco Nexus 93180YC-FX"):
    """
    Build one datacenter into topology builder t using the large_4dc_enterprise
    architecture:

      ER1/ER2 → FW1/FW2 → LB1/LB2 → CORE1/CORE2
        → SPx (spine, full mesh to cores)
          → LFx (leaf, full Clos to all spines)
            → SRV (compute servers, srv_per_leaf each)
      Special servers (DHCP×2, DNS×2, NTP×2, MON×2, STOR×2) on LF01..LF10
    """
    # Y levels
    ER_Y    = 0
    FW_Y    = 180
    LB_Y    = 360
    CORE_Y  = 540
    SPINE_Y = 720
    LEAF_Y  = 900
    SRV_Y   = 1080

    dc_w      = n_leaf * 160          # canvas width for this DC
    dc_cx     = x_offset + dc_w // 2  # centre-x

    # ── Edge routers ────────────────────────────────────────────────
    er1 = t.add(f"{dc}-ER1", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,  8,
                dc_cx - 120, ER_Y)
    er2 = t.add(f"{dc}-ER2", DeviceType.ROUTER, Vendor.CISCO_SYSTEMS,  8,
                dc_cx + 120, ER_Y)
    _set_model(t, er1, "Cisco ASR 1001-X")
    _set_model(t, er2, "Cisco ASR 1001-X")

    # ── Firewalls ────────────────────────────────────────────────────
    fw1 = t.add(f"{dc}-FW1", DeviceType.FIREWALL, Vendor.PALO_ALTO_NETWORKS, 12,
                dc_cx - 120, FW_Y)
    fw2 = t.add(f"{dc}-FW2", DeviceType.FIREWALL, Vendor.PALO_ALTO_NETWORKS, 12,
                dc_cx + 120, FW_Y)
    _set_model(t, fw1, "PA-5220"); _set_model(t, fw2, "PA-5220")
    for er in (er1, er2):
        for fw in (fw1, fw2):
            t.link(er, fw)

    # ── Load balancers ───────────────────────────────────────────────
    lb1 = t.add(f"{dc}-LB1", DeviceType.LOAD_BALANCER, Vendor.F5_NETWORKS, 10,
                dc_cx - 120, LB_Y)
    lb2 = t.add(f"{dc}-LB2", DeviceType.LOAD_BALANCER, Vendor.F5_NETWORKS, 10,
                dc_cx + 120, LB_Y)
    _set_model(t, lb1, "BIG-IP i5800"); _set_model(t, lb2, "BIG-IP i5800")
    for fw in (fw1, fw2):
        for lb in (lb1, lb2):
            t.link(fw, lb)

    # ── Core switches ────────────────────────────────────────────────
    core1 = t.add(f"{dc}-CORE1", DeviceType.SWITCH, switch_vendor, 64,
                  dc_cx - 120, CORE_Y)
    core2 = t.add(f"{dc}-CORE2", DeviceType.SWITCH, switch_vendor, 64,
                  dc_cx + 120, CORE_Y)
    _set_model(t, core1, core_model)
    _set_model(t, core2, core_model)
    for lb in (lb1, lb2):
        for core in (core1, core2):
            t.link(lb, core)

    # ── Spine switches (full mesh to both cores) ─────────────────────
    spine_xs = [x_offset + int((i + 0.5) * dc_w / n_spine) for i in range(n_spine)]
    spines = []
    for i, sx in enumerate(spine_xs):
        sp = t.add(f"{dc}-SP{i+1}", DeviceType.SWITCH, switch_vendor, 54,
                   sx, SPINE_Y)
        _set_model(t, sp, spine_model)
        spines.append(sp)
        t.link(core1, sp)
        t.link(core2, sp)

    # ── Leaf switches (full Clos to all spines) ──────────────────────
    leaf_xs = [x_offset + int((i + 0.5) * dc_w / n_leaf) for i in range(n_leaf)]
    leaves = []
    for i, lx in enumerate(leaf_xs):
        lf = t.add(f"{dc}-LF{i+1:02d}", DeviceType.SWITCH, switch_vendor, 54,
                   lx, LEAF_Y)
        _set_model(t, lf, leaf_model)
        leaves.append(lf)
        for sp in spines:
            t.link(sp, lf)

    # ── Compute servers ──────────────────────────────────────────────
    srv_idx = 1
    for li, lf in enumerate(leaves):
        lx = leaf_xs[li]
        for si in range(srv_per_leaf):
            sx = lx + (si - (srv_per_leaf - 1) / 2) * 60
            vendor = _SERVER_VENDORS[srv_idx % len(_SERVER_VENDORS)]
            srv = t.add(f"{dc}-SRV{srv_idx:03d}", DeviceType.SERVER, vendor, 2,
                        sx, SRV_Y)
            t.link(lf, srv)
            srv_idx += 1

    # ── Special servers (one per leaf, LF01–LF10) ────────────────────
    specials = [
        ("DHCP1", Vendor.DELL),   ("DHCP2", Vendor.DELL),
        ("DNS1",  Vendor.HPE),    ("DNS2",  Vendor.HPE),
        ("NTP1",  Vendor.DELL),   ("NTP2",  Vendor.DELL),
        ("MON1",  Vendor.LENOVO), ("MON2",  Vendor.LENOVO),
        ("STOR1", Vendor.IBM),    ("STOR2", Vendor.IBM),
    ]
    for (sname, svendor), lf in zip(specials, leaves):
        lx = leaf_xs[leaves.index(lf)]
        srv = t.add(f"{dc}-{sname}", DeviceType.SERVER, svendor, 2,
                    lx + 90, SRV_Y + 140)
        t.link(lf, srv)

    # Collect all devices added by this DC build (snapshot before return)
    all_dc_devices = [d for d in t._all_devices
                      if d.name.startswith(f"{dc}-")]

    return er1, er2, all_dc_devices


def _set_model(t: "TopologyBuilder", dev, model_name: str):
    """Patch the model_name into the already-stored node dict."""
    for node in t.nodes:
        if node["id"] == dev.id:
            node["device"]["model_name"] = model_name
            return


def build_dual_dc_enterprise():
    """
    Two datacenters connected via redundant DCI links between their edge routers.

    DC1: Cisco fabric  → 202 prod devices + OOB mgmt + sensors + power chain
    DC2: Dell fabric   → 149 prod devices + OOB mgmt + sensors + power chain

    Inter-DC (DCI):  DC1-ER1 ↔ DC2-ER1  (primary)
                     DC1-ER2 ↔ DC2-ER2  (secondary)
    """
    t = TopologyBuilder("10.50.0.0/16")

    DC1_X_OFFSET = 0
    DC2_X_OFFSET = 1800

    dc1_er1, dc1_er2, dc1_devices = _build_dc(
        t, "DC1", n_spine=4, n_leaf=10, srv_per_leaf=17,
        x_offset=DC1_X_OFFSET)
    dc2_er1, dc2_er2, dc2_devices = _build_dc(
        t, "DC2", n_spine=3, n_leaf=8, srv_per_leaf=15,
        x_offset=DC2_X_OFFSET,
        switch_vendor=Vendor.DELL,
        core_model="Dell Z9264F-ON",
        spine_model="Dell Z9264F-ON",
        leaf_model="Dell S5248F-ON")

    # ── DCI links ────────────────────────────────────────────────────
    t.link(dc1_er1, dc2_er1)   # primary
    t.link(dc1_er2, dc2_er2)   # secondary

    # ── Management networks (separate OOB domain per DC) ─────────────
    mgmt1 = t.add_mgmt_network(dc1_devices, dc_id=1,
                                oob_vendor=Vendor.CISCO_SYSTEMS, n_sensors=4)
    mgmt2 = t.add_mgmt_network(dc2_devices, dc_id=2,
                                oob_vendor=Vendor.DELL, n_sensors=2)

    # ── Power chains (OOB switches and sensors also run on electricity) ──
    dc1_power = t.add_power_chain(
        dc1_devices + mgmt1['oob_switches'] + mgmt1['sensors'], dc_id=1,
        ups_vendor=Vendor.APC, pdu_vendor=Vendor.APC,
        floor_pdu_vendor=Vendor.EATON)
    dc2_power = t.add_power_chain(
        dc2_devices + mgmt2['oob_switches'] + mgmt2['sensors'], dc_id=2,
        ups_vendor=Vendor.EATON, pdu_vendor=Vendor.RARITAN,
        floor_pdu_vendor=Vendor.VERTIV)

    # ── EV2 energy monitors — Floor PDU level (breaker circuits → per-rack) ──
    # Each floor PDU has 24 breaker circuits feeding individual racks
    ev2_fpdu_dc1 = t.add_ev2_monitors(
        [dc1_power['floor_pdu']], dc_id=1,
        circuits_per_pdu=24, name_prefix="FP",
        mgmt_ip_pool=mgmt1['mgmt_ip_pool'])
    ev2_fpdu_dc2 = t.add_ev2_monitors(
        [dc2_power['floor_pdu']], dc_id=2,
        circuits_per_pdu=24, name_prefix="FP",
        mgmt_ip_pool=mgmt2['mgmt_ip_pool'])

    # ── EV2 energy monitors — Rack PDU level (outlet circuits → per-server) ──
    # Each rack PDU has 42 outlet circuits feeding individual servers
    ev2_rack_dc1 = t.add_ev2_monitors(
        dc1_power['rack_pdus'], dc_id=1,
        circuits_per_pdu=42, name_prefix="R",
        mgmt_ip_pool=mgmt1['mgmt_ip_pool'])
    ev2_rack_dc2 = t.add_ev2_monitors(
        dc2_power['rack_pdus'], dc_id=2,
        circuits_per_pdu=42, name_prefix="R",
        mgmt_ip_pool=mgmt2['mgmt_ip_pool'])

    all_ev2_dc1 = ev2_fpdu_dc1 + ev2_rack_dc1
    all_ev2_dc2 = ev2_fpdu_dc2 + ev2_rack_dc2

    # Wire power devices into their DC's OOB management network (with IP assignment)
    t.wire_to_mgmt(
        [dc1_power['floor_pdu']] + dc1_power['ups_list'] + dc1_power['rack_pdus'],
        mgmt1['oob_switches'], mgmt_ip_pool=mgmt1['mgmt_ip_pool'])
    t.wire_to_mgmt(
        [dc2_power['floor_pdu']] + dc2_power['ups_list'] + dc2_power['rack_pdus'],
        mgmt2['oob_switches'], mgmt_ip_pool=mgmt2['mgmt_ip_pool'])

    # Wire EV2 monitors to OOB (IPs already assigned in add_ev2_monitors)
    t.wire_to_mgmt(all_ev2_dc1, mgmt1['oob_switches'], mgmt_ip_pool=None)
    t.wire_to_mgmt(all_ev2_dc2, mgmt2['oob_switches'], mgmt_ip_pool=None)

    return t


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    random.seed(42)   # Reproducible metrics

    print("Generating example topologies...")
    print()

    builders = [
        ("large_datacenter_3tier.json",      build_three_tier_datacenter),
        ("large_datacenter_spine_leaf.json", build_spine_leaf),
        ("large_enterprise_wan.json",        build_enterprise_wan),
        ("large_hyperscale_pod.json",        build_hyperscale_pod),
        ("dual_dc_enterprise.json",          build_dual_dc_enterprise),
    ]

    for filename, fn in builders:
        t = fn()
        t.save(filename)

    print()
    print("Done. Load any file via File > Open Topology.")

"""
Topology generator script - produces large example JSON files.
Run from the project root:  python _generate_topology.py
"""
import sys, json, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.device_manager import Device, DeviceType, Vendor
from core.ip_manager import IPManager

_SERVER_VENDORS = [Vendor.DELL, Vendor.HPE, Vendor.LENOVO, Vendor.SUPERMICRO]

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

    def add(self, name, dtype, vendor, ifaces, x, y,
            community="public", port=161):
        dev = Device(
            name=name,
            device_type=dtype,
            vendor=vendor,
            ip_address=self.ip.next_ip(),
            snmp_port=port,
            snmp_community=community,
            interface_count=ifaces,
        )
        self._iface_next[dev.id] = 0
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

    def link(self, a, b, ai=None, bi=None):
        key = tuple(sorted([a.id, b.id]))
        if key not in self._edge_set:
            self._edge_set.add(key)
            if ai is None:
                ai = self._alloc_iface(a, a.interface_count)
            if bi is None:
                bi = self._alloc_iface(b, b.interface_count)
            self.edges.append({
                "src": a.id, "dst": b.id,
                "src_iface": ai, "dst_iface": bi,
            })

    def to_dict(self):
        return {"nodes": self.nodes, "edges": self.edges}

    def save(self, filename):
        path = TOPOLOGIES_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        n_dev = len(self.nodes)
        n_lnk = len(self.edges)
        print(f"  Saved {path.name}  ({n_dev} devices, {n_lnk} links)")
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

    for pod_idx, (pod_label, pod_cx) in enumerate([("A", 450), ("B", 1050)]):
        # ---- Aggregation layer (2 per pod) ----
        agg_xs = [pod_cx - 150, pod_cx + 150]
        aggs = []
        for ai, ax in enumerate(agg_xs):
            agg = t.add(f"Agg-SW{pod_idx*2+ai+1}",
                        DeviceType.SWITCH, Vendor.CISCO_SYSTEMS, 48, ax, AGG_Y)
            aggs.append(agg)
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
                t.link(tor, srv)

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

    return t


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    random.seed(42)   # Reproducible metrics

    print("Generating example topologies...")
    print()

    builders = [
        ("large_datacenter_3tier.json",    build_three_tier_datacenter),
        ("large_datacenter_spine_leaf.json", build_spine_leaf),
        ("large_enterprise_wan.json",      build_enterprise_wan),
        ("large_hyperscale_pod.json",      build_hyperscale_pod),
    ]

    for filename, fn in builders:
        t = fn()
        t.save(filename)

    print()
    print("Done. Load any file via File > Open Topology.")

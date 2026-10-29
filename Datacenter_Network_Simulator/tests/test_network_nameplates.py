"""A switch is rated at what its data sheet says, not at a type default.

Every switch used to fall through to 250 W - a ToR guess that sat on a
64 x 100G spine and on a 48-port access switch alike - and the Power page
budgeted racks off it. The catalog now carries the vendors' typical figures
and the shipped topology has been re-stamped with them.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.device_manager import DEFAULT_NAMEPLATE_W, DeviceType, nameplate_power_w

TOPOLOGY = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"


def test_network_skus_carry_their_data_sheet_figure():
    assert nameplate_power_w(DeviceType.SWITCH, "Cisco Nexus 9364C") == 429
    assert nameplate_power_w(DeviceType.SWITCH, "Cisco Nexus 93180YC-FX") == 260
    assert nameplate_power_w(DeviceType.SWITCH, "Dell Z9264F-ON") == 900
    assert nameplate_power_w(DeviceType.FIREWALL, "PA-5220") == 870
    assert nameplate_power_w(DeviceType.LOAD_BALANCER, "BIG-IP i5800") == 265
    assert nameplate_power_w(DeviceType.OOB_SWITCH, "Dell N1148T-ON") == 30
    # An unknown switch still gets the type default rather than nothing.
    assert nameplate_power_w(DeviceType.SWITCH, "Acme 48T") == DEFAULT_NAMEPLATE_W[DeviceType.SWITCH]


def test_the_shipped_topology_is_stamped_per_sku():
    """No switch, router, firewall or balancer in the fixture still carries the
    bare type default where the catalog knows its SKU."""
    topo = json.loads(TOPOLOGY.read_text(encoding="utf-8-sig"))
    stale = []
    for node in topo["nodes"]:
        d = node["device"]
        if d.get("device_type") not in ("switch", "router", "firewall", "load_balancer", "oob_switch"):
            continue
        dt = DeviceType(d["device_type"])
        want = nameplate_power_w(dt, d.get("model_name") or "")
        if int(d.get("power_draw_w") or 0) != want:
            stale.append((d["name"], d.get("model_name"), d.get("power_draw_w"), want))
    assert not stale, stale[:5]

"""Move the plant header instruments off their own IPs and onto a Modbus gateway.

WHAT WAS WRONG
--------------
Each of the 12 chilled-/condenser-water header instruments (CHWS/CHWR/CWS/CWR/
CTB/FLOW per DC) carried its own management IP and its own SNMP agent. No such
device exists. A chilled-water supply thermowell is an RTD in a pipe wired to a
transmitter; it has no processor, no IP stack and no network port. What a real
site puts on the network is the GATEWAY: an RS-485 trunk of transmitters brought
onto Ethernet by a Moxa MGate / Schneider Link150 / Eaton PXG class device, which
the BMS polls over Modbus TCP by unit id.

WHAT THIS DOES
--------------
Per datacenter:
  * adds one MODBUS_GATEWAY in the Central Plant room, on the BMS management
    plane, taking the .30 host address in that room's /24;
  * strips mgmt_ip / SNMP from the six instruments in that room and marks them
    modbus_role="rtu_slave" with unit ids 1..6 on that gateway;
  * records the trunk membership on the gateway (modbus_children), in unit-id
    order, which is also the ENTITY-SENSOR index order the gateway republishes.

WHAT STAYS THE SAME
-------------------
The instruments remain Devices with their names, models and roles intact, so
_probe_role still identifies them, the cooling model still reads them, and the
22 plant-probe trap rules in core/trap_rules.py still match them by model name.
Their readings still reach an NMS over SNMP — as indexed rows in the gateway's
ENTITY-SENSOR table rather than as six separate agents.

Traps raised on these instruments now source from the gateway IP. That is not a
compromise: Modbus has no unsolicited messaging, so a transmitter physically
cannot send a trap, and on a real site the gateway raises it on the instrument's
behalf.

Usage:
    python tools/migrate_plant_sensors.py topologies/dual_dc_enterprise.json
    python tools/migrate_plant_sensors.py <file> --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Unit ids are assigned in this order and MUST NOT be reshuffled later: the
# gateway republishes the trunk as an ENTITY-SENSOR table indexed in this order,
# and an index that moves is an index every existing poller template reads wrong.
PROBE_ORDER = ["CHWS", "CHWR", "FLOW", "CWS", "CWR", "CTB"]

GATEWAY_VENDOR = "Moxa"
GATEWAY_MODEL = "Moxa MGate MB3480"


def _prefix(name: str) -> str:
    return str(name).split("-")[0].upper()


def migrate(path: Path, dry_run: bool = False) -> int:
    topo = json.loads(path.read_text(encoding="utf-8"))
    nodes = topo["nodes"]

    # Group the header instruments by datacenter.
    by_dc: dict = {}
    for n in nodes:
        d = n["device"]
        if d.get("device_type") != "sensor":
            continue
        if not str(d.get("model_name", "")).startswith("Plant "):
            continue
        if _prefix(d.get("name", "")) not in PROBE_ORDER:
            continue
        by_dc.setdefault(d.get("datacenter") or "", []).append(n)

    if not by_dc:
        print("No plant header instruments found — nothing to do.")
        return 0

    existing = {n["device"].get("name") for n in nodes}
    # Every address in use anywhere, so the gateway cannot silently collide.
    all_ips = {v for n in nodes for k in ("ip_address", "mgmt_ip")
               for v in [n["device"].get(k)] if v}
    added, converted = [], []

    for dc, probe_nodes in sorted(by_dc.items()):
        probe_nodes.sort(key=lambda n: PROBE_ORDER.index(_prefix(n["device"]["name"])))
        sample = probe_nodes[0]["device"]

        # The gateway takes over the LOWEST address the trunk is releasing rather
        # than claiming a fresh one. Six instruments hand back six addresses and
        # one comes back as the gateway, so the plant subnet ends up five
        # addresses lighter — and the choice cannot collide with anything, since
        # the address was already this room's and is being freed in this pass.
        released = sorted(
            (p["device"].get("mgmt_ip", "") for p in probe_nodes if p["device"].get("mgmt_ip")),
            key=lambda ip: int(ip.split(".")[-1]))
        if not released:
            print(f"  {dc}: instruments already have no addresses — skipping")
            continue
        gw_ip = released[0]
        gw_name = f"MBGW1-{dc}-CP"
        if gw_name in existing:
            print(f"  {gw_name} already present — skipping {dc}")
            continue

        children = [p["device"]["name"] for p in probe_nodes]
        for unit, pn in enumerate(probe_nodes, start=1):
            d = pn["device"]
            converted.append((d["name"], d.get("mgmt_ip", ""), unit))
            # The whole point of the migration: no address, no agent.
            d["mgmt_ip"] = ""
            d["ip_address"] = ""
            d["snmp_port"] = 0
            d["snmp_community"] = ""
            d["metrics_enabled"] = False
            d["modbus_role"] = "rtu_slave"
            d["modbus_unit_id"] = unit
            d["modbus_gateway_ip"] = gw_ip
            # An RTD in a pipe has no Ethernet port. Leaving eth0 behind would
            # keep the instrument looking like a network node in the canvas, the
            # port counts and the interface tables — the exact fiction this
            # migration exists to remove.
            d["interfaces"] = []
            d["interface_count"] = 0
            d["interface_groups"] = []

        # Deterministic locally-administered MAC derived from the address, so a
        # re-run reproduces the same topology instead of churning the diff.
        octets = [int(o) for o in gw_ip.split(".")]
        mac = "02:%02x:%02x:%02x:%02x:%02x" % (
            octets[1], octets[2], octets[3], (octets[3] * 7) & 0xFF, 0x01)

        gw_node = {
            "id": f"mbgw{dc.lower()}01",
            "position": dict(sample.get("position", {"x": 0, "y": 0}))
            if isinstance(sample.get("position"), dict) else {"x": 0, "y": 0},
            "device": {
                "name": gw_name,
                "device_type": "modbus_gateway",
                "vendor": GATEWAY_VENDOR,
                "model_name": GATEWAY_MODEL,
                "ip_address": "",
                "mgmt_ip": gw_ip,
                "snmp_port": 161,
                "snmp_community": gw_ip,
                "metrics_enabled": True,
                # One Ethernet port on the BMS management plane. The RS-485
                # trunks are not interfaces in this model — they carry no IP and
                # no MAC, and the unit id is the only address a slave has.
                "interface_count": 1,
                "interface_groups": [
                    {"iface_type": "Gigabit Ethernet (1 Gbps)", "count": 1}],
                "interfaces": [{
                    "index": 1, "name": "eth0", "speed": 1000000000,
                    "oper_status": 1, "in_octets": 0, "out_octets": 0,
                    "in_errors": 0, "out_errors": 0, "in_discards": 0,
                    "out_discards": 0,
                    "mac_address": mac, "connected_to_device": None,
                    "connected_to_iface": None, "role": "mgmt",
                }],
                "modbus_role": "gateway",
                "modbus_children": children,
                "datacenter": sample.get("datacenter", ""),
                "datacenter_city": sample.get("datacenter_city", ""),
                "country": sample.get("country", ""),
                "room": sample.get("room", ""),
                "floor": sample.get("floor", ""),
                "rack_row": sample.get("rack_row", 0),
                "rack_num": sample.get("rack_num", 0),
                "power_draw_w": 15,
                "rated_power_w": 25,
            },
        }
        # Position is a canvas coordinate; nudge so it does not sit exactly on a
        # probe it replaced.
        gw_node["position"] = {"x": gw_node["position"].get("x", 0) + 40,
                               "y": gw_node["position"].get("y", 0) + 40}
        # The gateway's address was one of the trunk's, so it is free by now.
        remaining = all_ips - {ip for _n, ip, _u in converted if ip}
        if gw_ip in remaining:
            print(f"  ERROR: {gw_ip} is still claimed elsewhere — aborting {dc}",
                  file=sys.stderr)
            return 1
        nodes.append(gw_node)
        added.append((gw_name, gw_ip, len(children)))

    print(f"\nGateways added ({len(added)}):")
    for name, ip, n in added:
        print(f"  {name:18s} {ip:14s} trunk of {n}")
    print(f"\nInstruments converted to RTU slaves ({len(converted)}):")
    for name, old_ip, unit in converted:
        print(f"  {name:18s} unit {unit}   released {old_ip}")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = path.with_suffix(path.suffix + ".premodbusgw.bak")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {path}\nBackup {backup}")
    print(f"Released {len(converted)} management addresses.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("topology", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.topology.exists():
        print(f"No such file: {args.topology}", file=sys.stderr)
        return 2
    return migrate(args.topology, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

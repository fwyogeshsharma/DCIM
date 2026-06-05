"""Populate power_draw_w (watts) on every load device in a topology so the
BACnet energy meters can derive their kW from real downstream draw.

Infrastructure (pdu/rpp/ups/generator/energy_monitor) stays 0 — it passes
power through, it is not itself the load. Draw is deterministic per device
(seeded from the hex id) so re-runs are stable. Re-runnable / idempotent-ish:
overwrites power_draw_w each run.

Typical result on dual_dc_enterprise: IT ~140 kW, cooling ~70 kW
(chiller plant ~42 + CRAH fans ~28), PUE ~1.5.
"""
import json
import sys
from pathlib import Path

# device_type -> (base watts, jitter span). Final = base + (seed % span).
LOAD_PROFILE = {
    "server":        (280, 270),
    "switch":        (180,  80),
    "router":        (320, 120),
    "firewall":      (260, 100),
    "load_balancer": (220,  80),
    "oob_switch":    ( 60,  50),
    "sensor":        (  4,   6),
    "crah":          (3000, 1000),   # air handler = fans + valve only (chiller does the work)
    "chiller":       (6500, 1000),   # compressors + evaporator/condenser (dominant cooling load)
    "pump":          (1100,  800),   # CHW / condenser-water VFD pump motor
    "cooling_tower": ( 650,  300),   # tower fan motor (heat rejection)
    # valves / plant sensors are instrumentation -> ~0 W, left at default 0
}

def main(path: str):
    p = Path(path)
    d = json.loads(p.read_text())
    totals: dict = {}
    for n in d["nodes"]:
        dev = n["device"]
        dt = dev.get("device_type")
        prof = LOAD_PROFILE.get(dt)
        if prof is None:
            dev["power_draw_w"] = 0          # infrastructure / pass-through
            continue
        base, span = prof
        seed = int(dev["id"], 16) if all(c in "0123456789abcdef" for c in dev["id"]) else len(dev["id"])
        w = base + (seed % span)
        dev["power_draw_w"] = w
        totals[dt] = totals.get(dt, 0) + w
    p.write_text(json.dumps(d, indent=2))
    print(f"Populated power_draw_w in {p.name}:")
    for dt, w in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {dt:14} {w/1000:8.1f} kW")
    print(f"  {'TOTAL load':14} {sum(totals.values())/1000:8.1f} kW")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else str(Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"))

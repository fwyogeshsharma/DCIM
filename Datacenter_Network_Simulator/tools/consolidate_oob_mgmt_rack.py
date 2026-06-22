"""
Consolidate DC1 / Server Hall A / F1 / Row 1 OOB switches into a single
management rack (RACK9).

Real-DC rationale: dedicating a full floor tile + rack + 2 metered PDUs to a
single 1U out-of-band management switch (~60-99 W) is not how production DCs are
built. OOB/management switches are consolidated into one management rack (with
patch panels / console servers) or run as ToR in the racks they manage. This
script collapses 6 OOB switches (racks 9-14) into RACK9 and decommissions the
5 freed racks and their 10 now-orphaned PDUs.

Idempotency: re-running after consolidation is a no-op (racks 10-14 absent).
"""
import json
import shutil
import os

TOPO = "topologies/dual_dc_enterprise.json"
FP = "topologies/dual_dc_enterprise_floorplan.json"

MGMT_RACK = "DC1:ServerHallA:F1:R1:RACK9"
FREED_RACKS = [f"DC1:ServerHallA:F1:R1:RACK{n}" for n in range(10, 15)]

# OOB switches moving into the mgmt rack, with target rack unit
MOVED_SWITCHES = {
    "94f1c5b0": 40,  # OOB-SW-DC1-01
    "ffe07bcf": 38,  # OOB-SW-DC1-02
    "46aac8d9": 36,  # OOB-SW-DC1-03
    "54c037ab": 34,  # OOB-SW-DC1-04
    "e69c9a56": 32,  # OOB-SW-DC1-05
}
# Per-switch original A/B PDUs (to be removed) -> redirect power to RACK9 PDUs
SWITCH_OLD_PDUS = {
    "94f1c5b0": ("134c6c92", "680ac07a"),
    "ffe07bcf": ("63f2ae24", "4bf50b52"),
    "46aac8d9": ("284d82e5", "98326856"),
    "54c037ab": ("3d1a85dd", "02627f73"),
    "e69c9a56": ("e1805081", "a9d3d7c7"),
}
R9_PDU_A = "1931e9ee"   # PDU-DC1-SHA-R1-9-A
R9_PDU_B = "894a05e4"   # PDU-DC1-SHA-R1-9-B
R9_FEED_A = "PDU-DC1-SHA-R1-9-A"
R9_FEED_B = "PDU-DC1-SHA-R1-9-B"

REMOVED_PDUS = {p for pair in SWITCH_OLD_PDUS.values() for p in pair}


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    shutil.copy2(path, path + ".bak")
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def main():
    topo = load(TOPO)
    fp = load(FP)

    racks = {r["rack_id"]: r for r in fp["racks"]}
    if MGMT_RACK not in racks or all(r not in racks for r in FREED_RACKS):
        print("Nothing to do (already consolidated or mgmt rack missing).")
        return

    # ---- map A/B old PDU -> R9 PDU ----
    pdu_redirect = {}
    for sw, (a, b) in SWITCH_OLD_PDUS.items():
        pdu_redirect[a] = R9_PDU_A
        pdu_redirect[b] = R9_PDU_B

    # ---- 1. rewrite edges ----
    new_edges = []
    dropped = repointed = 0
    for e in topo["edges"]:
        s, d = e["src"], e["dst"]
        if s in REMOVED_PDUS:
            if d in MOVED_SWITCHES and e.get("layer") == "power":
                e["src"] = pdu_redirect[s]      # power feed now from R9 PDU
                new_edges.append(e)
                repointed += 1
            else:
                dropped += 1                     # PDU mgmt uplink, etc.
            continue
        if d in REMOVED_PDUS:
            dropped += 1                         # upstream RPP -> PDU
            continue
        new_edges.append(e)
    topo["edges"] = new_edges

    # ---- 2. remove decommissioned PDU nodes ----
    before = len(topo["nodes"])
    topo["nodes"] = [n for n in topo["nodes"] if n["id"] not in REMOVED_PDUS]
    removed_nodes = before - len(topo["nodes"])

    # ---- 3. floorplan: move switches, decommission racks + PDUs ----
    fp["devices"] = [d for d in fp["devices"] if d["id"] not in REMOVED_PDUS]
    dmap = {d["id"]: d for d in fp["devices"]}
    for sw, ru in MOVED_SWITCHES.items():
        d = dmap[sw]
        d["rack_id"] = MGMT_RACK
        d["rack_unit"] = ru
        d["feed_a"] = R9_FEED_A
        d["feed_b"] = R9_FEED_B

    # rebuild mgmt rack membership: existing OOB-CORE + R9 PDUs + moved switches
    r9 = racks[MGMT_RACK]
    kept = [x for x in r9["device_ids"] if x not in REMOVED_PDUS]
    switch_ids = list(MOVED_SWITCHES.keys())
    # order: switches first, PDUs last
    pdus = [x for x in kept if x in (R9_PDU_A, R9_PDU_B)]
    core = [x for x in kept if x not in pdus]
    r9["device_ids"] = core + switch_ids + pdus
    r9["device_count"] = len(r9["device_ids"])
    r9["it_power_draw_w"] = sum(
        dmap[x]["power_draw_w"]
        for x in r9["device_ids"]
        if dmap.get(x, {}).get("device_type") not in ("pdu",)
        and "power_draw_w" in dmap.get(x, {})
    )

    fp["racks"] = [r for r in fp["racks"] if r["rack_id"] not in FREED_RACKS]

    # ---- 4. summary counts ----
    fp["summary"]["racks"] = len(fp["racks"])
    fp["summary"]["devices"] = len(fp["devices"])

    save(TOPO, topo)
    save(FP, fp)

    print("Consolidation complete.")
    print(f"  edges repointed (power feed -> R9 PDU): {repointed}")
    print(f"  edges dropped (PDU mgmt/upstream):       {dropped}")
    print(f"  PDU nodes removed:                       {removed_nodes}")
    print(f"  racks removed:                           {len(FREED_RACKS)}")
    print(f"  mgmt rack {MGMT_RACK}: {r9['device_count']} devices, "
          f"{r9['it_power_draw_w']} W IT")
    print(f"  summary: racks={fp['summary']['racks']} devices={fp['summary']['devices']}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()

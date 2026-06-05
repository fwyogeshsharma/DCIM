"""Phase 4 runtime test: start the BACnet controller on loopback with one EV2
meter + all four chiller-plant device types, tick the telemetry, read the
snapshot the API serves, and fire a real Who-Is to confirm I-Am over the wire.
"""
import socket
import time

from simulator.bacnet_controller import BACnetController
from core.bacnet_object_model import build_npdu, build_bvll

PORT = 47912
EV2_IP = "127.0.0.2"
PLANT = [
    ("127.0.0.3", "CHILLER-DC1-1", "chiller", 7.0),
    ("127.0.0.4", "CHWP-DC1-1",    "pump",    1.8),
    ("127.0.0.5", "CT-DC1-1",      "cooling_tower", 0.75),
    ("127.0.0.6", "VLV-DC1-CHW",   "valve",   0.0),
]

def main():
    ctrl = BACnetController()
    ctrl.set_log_callback(lambda m, l="info": None)
    ctrl.start(
        device_ips=[EV2_IP],
        base_instance=40001,
        circuits_map={EV2_IP: (42, 6)},
        rated_kw_map={EV2_IP: 30.0},
        port=PORT,
        plant_devices=[{"ip": ip, "name": nm, "device_type": dt, "rated_kw": kw}
                       for ip, nm, dt, kw in PLANT],
    )
    # advance telemetry
    for _ in range(20):
        ctrl.tick(30.0)

    print("=== BACnet snapshot (what /ev2 + /plant metrics serve) ===")
    snaps = ctrl.get_telemetry_snapshot()
    for s in snaps:
        kind = s["kind"]
        if kind == "ev2":
            kw = s["values"].get("Panel_Total_kW")
            print(f"  [{kind}] {s['name']:14} inst={s['instance']} Panel_Total_kW={kw}")
        else:
            v = s["values"]
            key = {"plant:chiller": "Active_Power", "plant:pump": "Motor_Power",
                   "plant:cooling_tower": "Fan_Power", "plant:valve": "Position"}[kind]
            extra = {"plant:chiller": "CHW_Supply_Temp", "plant:pump": "Speed",
                     "plant:cooling_tower": "Basin_Temp", "plant:valve": "Status_Modulating"}[kind]
            print(f"  [{kind:20}] {s['name']:14} inst={s['instance']} "
                  f"{key}={v.get(key)} {extra}={v.get(extra)} pts={len(v)}")

    # --- real Who-Is over UDP -> collect I-Am device instances ---
    apdu = b"\x10\x08"                      # unconfirmed-req, service 8 = Who-Is (global)
    pkt = build_bvll(build_npdu(apdu, expects_reply=False), broadcast=True)
    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli.bind(("127.0.0.1", 0))
    cli.settimeout(2.0)
    cli.sendto(pkt, ("127.0.0.1", PORT))
    iam_instances = set()
    t0 = time.time()
    while time.time() - t0 < 2.0:
        try:
            data, addr = cli.recvfrom(4096)
        except socket.timeout:
            break
        # find I-Am object-identifier app tag 0xC4 -> next 4 bytes = (type<<22)|instance
        i = data.find(b"\xc4")
        if i >= 0 and i + 5 <= len(data):
            val = int.from_bytes(data[i + 1:i + 5], "big")
            iam_instances.add(val & 0x3FFFFF)
    cli.close()
    print(f"=== Who-Is -> I-Am over UDP: {len(iam_instances)} devices replied ===")
    print("    instances:", sorted(iam_instances))
    ctrl.stop()

if __name__ == "__main__":
    main()

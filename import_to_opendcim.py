"""
Import all topology JSON devices into openDCIM (dcim MySQL database).
- Skips devices whose PrimaryIP already exists in fac_Device
- Maps simulator device_type to openDCIM DeviceType
- Distributes devices round-robin across existing cabinets
"""

import json
import glob
import subprocess
import sys
from datetime import datetime

MYSQL = r"C:\xampp\mysql\bin\mysql.exe"
DB_ARGS = [MYSQL, "-u", "dcim", "-pdcim", "dcim"]

TOPOLOGY_DIR = r"C:\Users\harii\PyCharmMiscProject\SNMP Network Simulator\topologies"

DEVICE_TYPE_MAP = {
    "router":        "Appliance",
    "switch":        "Switch",
    "server":        "Server",
    "firewall":      "Appliance",
    "load_balancer": "Appliance",
    "storage":       "Storage Array",
}

def mysql_query(sql):
    result = subprocess.run(
        DB_ARGS,
        input=sql,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"MySQL error: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout

def get_existing_ips():
    out = mysql_query("SELECT PrimaryIP FROM fac_Device;")
    lines = out.strip().splitlines()
    return set(lines[1:]) if len(lines) > 1 else set()

def get_cabinet_ids():
    out = mysql_query("SELECT CabinetID FROM fac_Cabinet ORDER BY CabinetID;")
    lines = out.strip().splitlines()
    return [int(x) for x in lines[1:]] if len(lines) > 1 else [1]

def escape(s):
    return str(s).replace("'", "''").replace("\\", "\\\\")

def main():
    existing_ips = get_existing_ips()
    cabinet_ids = get_cabinet_ids()
    print(f"Existing devices: {len(existing_ips)}, Cabinets: {cabinet_ids}")

    files = sorted(glob.glob(f"{TOPOLOGY_DIR}/*.json"))
    devices = []
    seen_ips = set(existing_ips)

    for path in files:
        with open(path) as f:
            data = json.load(f)
        topo_name = path.split("\\")[-1].replace(".json", "")
        for node in data.get("nodes", []):
            dev = node.get("device", {})
            ip = dev.get("ip_address", "")
            if not ip or ip in seen_ips:
                continue
            seen_ips.add(ip)
            devices.append({
                "label":      escape(dev.get("name", dev.get("id", "unknown"))),
                "ip":         escape(ip),
                "community":  escape(ip),  # snmp_community = ip_address
                "dtype":      DEVICE_TYPE_MAP.get(dev.get("device_type", "").lower(), "Server"),
                "topo":       topo_name,
            })

    if not devices:
        print("No new devices to import.")
        return

    print(f"Importing {len(devices)} new devices...")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql_lines = []
    for i, d in enumerate(devices):
        cab = cabinet_ids[i % len(cabinet_ids)]
        sql_lines.append(
            f"INSERT INTO fac_Device "
            f"(Label, SerialNo, AssetTag, PrimaryIP, SNMPVersion, "
            f"v3SecurityLevel, v3AuthProtocol, v3AuthPassphrase, v3PrivProtocol, v3PrivPassphrase, "
            f"SNMPCommunity, SNMPFailureCount, Hypervisor, APIUsername, APIPassword, APIPort, ProxMoxRealm, "
            f"Owner, EscalationTimeID, EscalationID, PrimaryContact, "
            f"Cabinet, Position, Height, Ports, FirstPortNum, TemplateID, "
            f"NominalWatts, PowerSupplyCount, DeviceType, ChassisSlots, RearChassisSlots, "
            f"ParentDevice, MfgDate, InstallDate, WarrantyCo, WarrantyExpire, "
            f"Notes, Status, HalfDepth, BackSide, AuditStamp, Weight) VALUES ("
            f"'{d['label']}', '', '', '{d['ip']}', '2c', "
            f"'', '', '', '', '', "
            f"'{d['community']}', 0, '', '', '', 0, '', "
            f"0, 0, 0, 0, "
            f"{cab}, 0, 1, 0, 1, 0, "
            f"0, 1, '{d['dtype']}', 0, 0, "
            f"0, '0000-00-00', '0000-00-00', '', NULL, "
            f"'Imported from {d['topo']}', 'Production', 0, 0, '{now}', 0);"
        )

    sql = "\n".join(sql_lines)
    result = subprocess.run(DB_ARGS, input=sql, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # Verify
    out = mysql_query("SELECT COUNT(*) as total FROM fac_Device;")
    total = out.strip().splitlines()[-1]
    print(f"Done. Total devices in openDCIM: {total}")

if __name__ == "__main__":
    main()
"""
Network Simulator Container Entrypoint.

Startup sequence:
  1. Parse YAML config
  2. Load topology JSON → DeviceManager + TopologyEngine
  3. Generate SNMP datasets
  4. Bind IP aliases on loopback
  5. Start SNMPSim subprocess
  6. Start gNMI server
  7. Start sFlow controller
  8. Start DeviceStateStore (ticker)
  9. Wire HeadlessTrapEngine → DCIMBridge
 10. Start DCIMBridge async loop
 11. Serve FastAPI fault injection on HTTP
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn
import yaml

# ── Ensure Datacenter_Network_Simulator is importable ────────────────────────
# In Docker the layout is: /app/entrypoint.py + /app/Datacenter_Network_Simulator/
# In dev the layout may differ — try both locations.
_HERE = Path(__file__).resolve().parent
for _candidate in [
    _HERE / "Datacenter_Network_Simulator",   # Docker: /app/Datacenter_Network_Simulator
    _HERE.parent / "Datacenter_Network_Simulator",  # dev: project root
    Path("/app/Datacenter_Network_Simulator"),
]:
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

# ── Suppress gRPC verbosity ───────────────────────────────────────────────────
os.environ.setdefault("GRPC_VERBOSITY", "NONE")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("entrypoint")


# ─────────────────────────────────────────────────────────────────────────────
#  Config loading
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = os.environ.get("CONFIG_FILE", "/app/config/network-a.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    log.info("Loaded config from %s  (network_id=%s)", cfg_path, cfg.get("network_id"))
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
#  Topology loading
# ─────────────────────────────────────────────────────────────────────────────

def load_topology(cfg: dict):
    from core.device_manager import DeviceManager
    from core.topology_engine import TopologyEngine

    topo_name = cfg.get("topology", "large_datacenter_3tier.json")
    topo_dir  = Path(os.environ.get("TOPOLOGIES_DIR", "/app/Datacenter_Network_Simulator/topologies"))
    topo_path = topo_dir / topo_name

    log.info("Loading topology: %s", topo_path)
    with open(topo_path) as f:
        topo_data = json.load(f)

    dm = DeviceManager()
    topo = TopologyEngine()
    topo.from_dict(topo_data)

    # Populate DeviceManager from topology graph
    for device in topo.get_all_devices():
        dm.add_device(device)

    device_count = dm.count()
    log.info("Topology loaded: %d devices", device_count)
    return dm, topo


# ─────────────────────────────────────────────────────────────────────────────
#  Dataset generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_datasets(dm, topo, datasets_dir: str):
    from core.snmprec_generator import SNMPRecGenerator
    from core.gnmi_data_generator import GNMIDataGenerator

    snmp_dir = str(Path(datasets_dir) / "snmp")
    gnmi_dir = str(Path(datasets_dir) / "gnmi")
    Path(snmp_dir).mkdir(parents=True, exist_ok=True)
    Path(gnmi_dir).mkdir(parents=True, exist_ok=True)

    log.info("Generating SNMP datasets → %s", snmp_dir)
    snmp_gen = SNMPRecGenerator(output_dir=snmp_dir)
    snmp_gen.generate_all(topo)

    log.info("Generating gNMI datasets → %s", gnmi_dir)
    gnmi_gen = GNMIDataGenerator(output_dir=gnmi_dir)
    for dev in dm.get_all_devices():
        gnmi_gen.generate_device(dev, topo)

    return snmp_dir, gnmi_dir


# ─────────────────────────────────────────────────────────────────────────────
#  Main async entry
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    cfg = load_config()

    network_id    = cfg.get("network_id", "network-a")
    tick_interval = float(cfg.get("tick_interval", 30))
    datasets_dir  = os.environ.get("DATASETS_DIR", "/app/datasets")
    dcim_url      = os.environ.get("DCIM_SERVER_URL", "http://dcim-server:8080/api/v1")
    snmp_port     = int(cfg.get("snmp_port", 161))
    gnmi_port     = int(cfg.get("gnmi_port", 50051))
    api_port      = int(os.environ.get("API_PORT", 8090))
    iface         = os.environ.get("BIND_INTERFACE", "lo")

    protocols = cfg.get("protocols", {})
    snmp_enabled  = protocols.get("snmp", True)
    gnmi_enabled  = protocols.get("gnmi", True)
    sflow_enabled = protocols.get("sflow", True)
    traps_enabled = protocols.get("traps", True)

    sflow_cfg       = cfg.get("sflow", {})
    collector_ip    = sflow_cfg.get("collector_ip",   os.environ.get("SFLOW_COLLECTOR_IP", "aggregator"))
    collector_port  = int(sflow_cfg.get("collector_port", 6343))
    sflow_interval  = int(sflow_cfg.get("interval_sec",  30))

    trap_cfg        = cfg.get("traps", {})
    trap_receiver   = trap_cfg.get("receiver_ip",   os.environ.get("TRAP_RECEIVER_IP", "aggregator"))
    trap_port       = int(trap_cfg.get("receiver_port", 162))

    # ── Load topology ────────────────────────────────────────────────────────
    dm, topo = load_topology(cfg)
    all_devices = dm.get_all_devices()
    device_ips  = [d.ip_address for d in all_devices if d.ip_address]

    # ── Generate datasets ────────────────────────────────────────────────────
    snmp_dir, gnmi_dir = generate_datasets(dm, topo, datasets_dir)

    # ── Bind IP aliases ──────────────────────────────────────────────────────
    from ip_alias_manager import IPAliasManager
    ip_mgr = IPAliasManager(interface=iface)
    bound = ip_mgr.bind_all(device_ips)
    log.info("Bound %d IP aliases on %s", bound, iface)

    # ── SNMP Simulator ───────────────────────────────────────────────────────
    snmp_ctrl = None
    if snmp_enabled:
        from simulator.snmpsim_controller import SNMPSimController
        snmp_ctrl = SNMPSimController(datasets_dir=snmp_dir)
        snmp_ctrl.set_log_callback(lambda m: log.info("[SNMPSim] %s", m))
        ok = snmp_ctrl.start(device_ips, port=snmp_port)
        log.info("SNMPSim started=%s on port %d", ok, snmp_port)

    # ── gNMI Server ──────────────────────────────────────────────────────────
    gnmi_ctrl = None
    if gnmi_enabled:
        from simulator.gnmi_controller import GNMIController
        gnmi_ctrl = GNMIController(datasets_dir=gnmi_dir)
        gnmi_ctrl.set_log_callback(lambda m: log.info("[gNMI] %s", m))
        ok = gnmi_ctrl.start(device_ips, port=gnmi_port)
        log.info("gNMI started=%s on port %d", ok, gnmi_port)

    # ── DeviceStateStore ─────────────────────────────────────────────────────
    from core.device_state_store import DeviceStateStore
    store = DeviceStateStore(
        device_manager=dm,
        topology=topo,
        datasets_dir=datasets_dir,
        tick_interval=tick_interval,
    )
    if snmp_ctrl:
        store.enable_snmp_sync(snmp_ctrl)

    # ── Rule engine + trap rules ─────────────────────────────────────────────
    from core.rule_engine import RuleEngine
    from core.trap_rules import DEFAULT_RULES
    rule_engine = RuleEngine()
    for rule in DEFAULT_RULES:
        rule_engine.add_rule(rule)

    # ── HeadlessTrapEngine ───────────────────────────────────────────────────
    from headless_trap_engine import HeadlessTrapEngine
    trap_engine = HeadlessTrapEngine()
    trap_engine.set_device_manager(dm)
    if traps_enabled:
        trap_engine.set_receiver(trap_receiver, trap_port)
    trap_engine.start()

    # ── DCIMBridge ───────────────────────────────────────────────────────────
    from dcim_bridge import DCIMBridge
    bridge = DCIMBridge(
        dcim_url=dcim_url,
        network_id=network_id,
        device_manager=dm,
        state_store=store,
        tick_interval=tick_interval,
    )
    trap_engine.set_alert_callback(bridge.on_alert)
    rule_engine.set_action_callback(
        lambda action: trap_engine.on_rule_action(action, action.device_ref)
    )
    store.set_rule_engine_callback(rule_engine.evaluate_fact)

    # ── sFlow ────────────────────────────────────────────────────────────────
    sflow_ctrl = None
    if sflow_enabled:
        from simulator.sflow_controller import SFlowController
        sflow_ctrl = SFlowController()
        sflow_ctrl.set_state_store(store)
        sflow_ctrl.set_topology(topo)
        sflow_ctrl.set_device_manager(dm)
        sflow_ctrl.set_log_callback(lambda m: log.info("[sFlow] %s", m))
        sflow_ctrl.start(
            device_ips=device_ips,
            collector_ip=collector_ip,
            collector_port=collector_port,
            interval=sflow_interval,
        )
        log.info("sFlow started → %s:%d", collector_ip, collector_port)

    # ── Set gNMI state store ─────────────────────────────────────────────────
    if gnmi_ctrl:
        gnmi_ctrl.set_state_store(store)

    # ── Start DeviceStateStore ticker ────────────────────────────────────────
    store.start()
    log.info("DeviceStateStore ticker started (interval=%.0fs)", tick_interval)

    # ── Start DCIMBridge ─────────────────────────────────────────────────────
    await bridge.start()

    # ── Build and expose global state for http_api ────────────────────────────
    import http_api
    http_api.STATE.update({
        "network_id":   network_id,
        "dm":           dm,
        "topo":         topo,
        "store":        store,
        "rule_engine":  rule_engine,
        "trap_engine":  trap_engine,
        "bridge":       bridge,
        "snmp_ctrl":    snmp_ctrl,
        "gnmi_ctrl":    gnmi_ctrl,
        "sflow_ctrl":   sflow_ctrl,
        "ip_mgr":       ip_mgr,
        "cfg":          cfg,
    })

    # ── Graceful shutdown ────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _shutdown():
        log.info("Shutdown signal received")
        loop.create_task(_cleanup())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    async def _cleanup():
        log.info("Stopping services...")
        store.stop()
        await bridge.stop()
        trap_engine.stop()
        if snmp_ctrl:
            snmp_ctrl.stop()
        if gnmi_ctrl:
            gnmi_ctrl.stop()
        if sflow_ctrl:
            sflow_ctrl.stop()
        ip_mgr.cleanup()
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # ── FastAPI server ────────────────────────────────────────────────────────
    config = uvicorn.Config(
        app=http_api.app,
        host="0.0.0.0",
        port=api_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    log.info("HTTP API listening on 0.0.0.0:%d", api_port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

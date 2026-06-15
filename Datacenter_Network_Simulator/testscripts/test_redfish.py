"""
Redfish info probe — fetch and display everything a server BMC exposes.

Point it at a running Redfish/BMC endpoint (started by the simulator) and it
walks the resource tree and prints System, Chassis (Thermal + Power), and
Manager details for that machine.

Usage:
    python testscripts/test_redfish.py <server-ip>
    python testscripts/test_redfish.py 10.1.0.20 --port 443 --user admin --pass password
    python testscripts/test_redfish.py 10.1.0.20 --scheme https --insecure
    python testscripts/test_redfish.py 10.1.0.20 --session     # session token auth (default: HTTP Basic)

    # power
    python testscripts/test_redfish.py <ip> --power-on
    python testscripts/test_redfish.py <ip> --power-off
    python testscripts/test_redfish.py <ip> --reboot
    python testscripts/test_redfish.py <ip> --power-cycle

    # identify LED
    python testscripts/test_redfish.py <ip> --led on
    python testscripts/test_redfish.py <ip> --led off

    # inventory + event log
    python testscripts/test_redfish.py <ip> --refresh
    python testscripts/test_redfish.py <ip> --view-log
    python testscripts/test_redfish.py <ip> --clear-log

    # push-model event subscriptions (EventService / EventDestination)
    python testscripts/test_redfish.py <ip> --list-subs
    python testscripts/test_redfish.py <ip> --subscribe      # listen + subscribe + stream events live (Ctrl-C to stop)
    python testscripts/test_redfish.py <ip> --subscribe --listen-host 10.1.0.5 --listen-port 9000
    python testscripts/test_redfish.py <ip> --unsubscribe <sub-id>

Defaults match the simulator MVP: plain HTTP, port 443, admin/password.
Pure stdlib — no external dependencies.
"""
from __future__ import annotations

import argparse
import atexit
import base64
import json
import ssl
import sys
import urllib.error
import urllib.request

# Windows consoles default to cp1252 and choke on the box-drawing / °/× glyphs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ── tiny ANSI helpers (auto-off when not a TTY) ──────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def bold(s):  return _c("1", s)
def cyan(s):  return _c("36", s)
def green(s): return _c("32", s)
def grey(s):  return _c("90", s)
def red(s):   return _c("31", s)
def yellow(s):return _c("33", s)


class Client:
    def __init__(self, base: str, user: str, pw: str, insecure: bool):
        self.base = base.rstrip("/")
        self._user, self._pw = user, pw
        self._auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        self._ctx = ssl._create_unverified_context() if insecure else None
        # Session-auth state (used instead of Basic once login() succeeds).
        self._token = None
        self._session_url = None

    # ── session auth (opt-in via --session) ──────────────────────────────────
    def login(self) -> bool:
        """Create a Redfish session; switch all later requests to X-Auth-Token.
        Returns True on success. Session creation itself is unauthenticated —
        credentials travel in the body, the token comes back in a header."""
        url = self.base + "/redfish/v1/SessionService/Sessions"
        data = json.dumps({"UserName": self._user, "Password": self._pw}).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8, context=self._ctx) as r:
                self._token = r.headers.get("X-Auth-Token")
                self._session_url = r.headers.get("Location")
                return self._token is not None
        except Exception:
            return False

    def logout(self) -> None:
        """Delete the session token (best-effort, on exit). The DELETE itself
        is authenticated with the token, which _send still has set."""
        if self._token and self._session_url:
            try:
                self._send("DELETE", self._session_url)
            except Exception:
                pass
            self._token = None
            self._session_url = None

    def _send(self, method: str, path: str, body=None):
        url = path if path.startswith("http") else self.base + path
        # Token auth once logged in; otherwise HTTP Basic (the default).
        if self._token:
            headers = {"X-Auth-Token": self._token, "Accept": "application/json"}
        else:
            headers = {"Authorization": self._auth, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8, context=self._ctx) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else None
            except Exception:
                return e.code, None
        except Exception as exc:
            return 0, {"_error": str(exc)}

    def get(self, path: str):
        return self._send("GET", path)

    def post(self, path: str, body=None):
        return self._send("POST", path, body)

    def patch(self, path: str, body):
        return self._send("PATCH", path, body)

    def delete(self, path: str):
        return self._send("DELETE", path)


def kv(label: str, value, width: int = 22):
    if value is None or value == "":
        value = grey("—")
    print(f"  {label.ljust(width)} {value}")


def section(title: str):
    print()
    print(bold(cyan(f"── {title} " + "─" * max(2, 50 - len(title)))))


def members(client: Client, collection: dict) -> list[dict]:
    """Resolve every member of a Redfish collection."""
    out = []
    for m in (collection or {}).get("Members", []):
        oid = m.get("@odata.id")
        if not oid:
            continue
        st, obj = client.get(oid)
        if st == 200 and obj:
            out.append(obj)
    return out


def show_system(sys_obj: dict):
    section(f"System: {sys_obj.get('Name', '?')}")
    kv("Manufacturer",  sys_obj.get("Manufacturer"))
    kv("Model",         sys_obj.get("Model"))
    kv("Serial Number", sys_obj.get("SerialNumber"))
    kv("UUID",          sys_obj.get("UUID"))
    kv("Host Name",     sys_obj.get("HostName"))
    kv("BIOS Version",  sys_obj.get("BiosVersion"))
    ps = sys_obj.get("PowerState")
    kv("Power State",   green(ps) if ps == "On" else yellow(ps))
    led = sys_obj.get("IndicatorLED")
    if led is not None:
        kv("Indicator LED", cyan(led) if led != "Off" else grey(led))
    status = sys_obj.get("Status", {})
    kv("Health",        status.get("Health"))

    proc = sys_obj.get("ProcessorSummary", {})
    kv("Processors",    f"{proc.get('Count', '?')} × {proc.get('Model', '?')}")
    mem = sys_obj.get("MemorySummary", {})
    kv("Total Memory",  f"{mem.get('TotalSystemMemoryGiB', '?')} GiB")

    oem = (sys_obj.get("Oem") or {}).get("Simulator") or {}
    if oem:
        cpu = oem.get("CpuUtilizationPercent")
        kv("CPU Utilization", f"{cpu}%" if cpu is not None else None)
        mp, dp = oem.get("MemoryUtilizationPercent"), oem.get("DiskUtilizationPercent")
        kv("Memory Utilization", f"{mp}%" if mp is not None else None)
        kv("Disk Utilization", f"{dp}%" if dp is not None else None)
        mu, dt, du = oem.get("MemoryUsedBytes"), oem.get("DiskTotalBytes"), oem.get("DiskUsedBytes")
        if mu is not None:
            kv("Memory Used", f"{mu / 1024**3:.1f} GiB")
        if dt:
            kv("Disk", f"{du / 1024**3:.0f} / {dt / 1024**3:.0f} GiB used")
        rx, tx = oem.get("NetworkRxMbps"), oem.get("NetworkTxMbps")
        if rx is not None:
            kv("Network Rx / Tx", f"{rx} / {tx} Mbps")
        ac = oem.get("AlarmCount")
        if ac is not None:
            kv("Alarm Count", red(str(ac)) if ac else green("0"))


def show_chassis(client: Client, ch: dict):
    section(f"Chassis: {ch.get('Name', '?')}")
    kv("Chassis Type", ch.get("ChassisType"))
    kv("Manufacturer", ch.get("Manufacturer"))
    kv("Model",        ch.get("Model"))
    kv("Power State",  ch.get("PowerState"))

    # Thermal
    t_ref = (ch.get("Thermal") or {}).get("@odata.id")
    if t_ref:
        st, thermal = client.get(t_ref)
        if st == 200 and thermal:
            temps = thermal.get("Temperatures", [])
            if temps:
                print(grey("  Temperatures:"))
                for t in temps:
                    reading = t.get("ReadingCelsius")
                    crit = t.get("UpperThresholdCritical")
                    hot = crit is not None and reading is not None and reading >= crit
                    val = f"{reading} °C" if reading is not None else "—"
                    val = red(val) if hot else green(val)
                    crit_s = grey(f"(crit {crit} °C)") if crit is not None else ""
                    print(f"    {str(t.get('Name', '?')).ljust(18)} {val}  {crit_s}")
            fans = thermal.get("Fans", [])
            if fans:
                print(grey("  Fans:"))
                for f in fans:
                    rpm = f.get("Reading")
                    hh = (f.get("Status") or {}).get("Health", "OK")
                    rpm_s = f"{rpm} RPM" if rpm is not None else "—"
                    rpm_s = green(rpm_s) if hh == "OK" else yellow(rpm_s)
                    print(f"    {str(f.get('Name', '?')).ljust(18)} {rpm_s}")

    # Power
    p_ref = (ch.get("Power") or {}).get("@odata.id")
    if p_ref:
        st, power = client.get(p_ref)
        if st == 200 and power:
            for pc in power.get("PowerControl", []):
                w = pc.get("PowerConsumedWatts")
                kv("Power Consumed", f"{w} W" if w is not None else None)
                pm = pc.get("PowerMetrics", {})
                if pm:
                    kv("  Avg / Min / Max",
                       f"{pm.get('AverageConsumedWatts','?')} / "
                       f"{pm.get('MinConsumedWatts','?')} / "
                       f"{pm.get('MaxConsumedWatts','?')} W")
            psus = power.get("PowerSupplies", [])
            if psus:
                print(grey("  Power Supplies:"))
                for ps in psus:
                    out_w = ps.get("LastPowerOutputWatts")
                    hh = (ps.get("Status") or {}).get("Health", "OK")
                    s = f"{out_w} W" if out_w is not None else "—"
                    s = green(s) if hh == "OK" else yellow(s)
                    cap = ps.get("PowerCapacityWatts")
                    cap_s = grey(f"(cap {cap} W)") if cap else ""
                    print(f"    {str(ps.get('Name', '?')).ljust(18)} {s}  {grey(hh)} {cap_s}")


def show_network(client: Client, sys_obj: dict):
    ref = (sys_obj.get("EthernetInterfaces") or {}).get("@odata.id")
    if not ref:
        return
    _, coll = client.get(ref)
    nics = members(client, coll)
    if not nics:
        return
    section(f"Network Interfaces: {sys_obj.get('Name', '?')}")
    for nic in nics:
        oem = (nic.get("Oem") or {}).get("Simulator") or {}
        rx, tx = oem.get("RxMbps"), oem.get("TxMbps")
        link = nic.get("LinkStatus", "?")
        spd = nic.get("SpeedMbps")
        link_s = green(link) if link == "LinkUp" else red(link)
        print(f"  {str(nic.get('Name', '?')).ljust(20)} "
              f"{link_s}  {grey(str(spd) + ' Mbps')}  "
              f"Rx {cyan(str(rx))} / Tx {cyan(str(tx))} Mbps")


def show_storage(client: Client, sys_obj: dict):
    ref = (sys_obj.get("Storage") or {}).get("@odata.id")
    if not ref:
        return
    _, coll = client.get(ref)
    for st_obj in members(client, coll):
        section(f"Storage / RAID: {sys_obj.get('Name', '?')}")
        for ctrl in st_obj.get("StorageControllers", []):
            kv("Controller", ctrl.get("Name"))
            kv("RAID Types", ", ".join(ctrl.get("SupportedRAIDTypes", [])))
        oem = (st_obj.get("Oem") or {}).get("Simulator") or {}
        kv("RAID Type",   oem.get("RaidType"))
        rs = oem.get("RaidStatus")
        kv("RAID Status", green(rs) if rs == "Optimal" else yellow(rs))
        cap, usep = oem.get("VolumeCapacityGiB"), oem.get("VolumeUsedPercent")
        if cap is not None:
            kv("Volume", f"{cap} GiB  ({usep}% used)")
        kv("Health", (st_obj.get("Status") or {}).get("Health"))


def show_logs(client: Client, sys_obj: dict):
    ref = (sys_obj.get("LogServices") or {}).get("@odata.id")
    if not ref:
        return
    _, coll = client.get(ref)
    for svc in members(client, coll):
        if svc.get("Id") != "SEL":
            continue
        section(f"Event Log (SEL): {sys_obj.get('Name', '?')}")
        count = ((svc.get("Oem") or {}).get("Simulator") or {}).get("AlarmCount", 0)
        kv("Alarm Count", red(str(count)) if count else green("0"))
        eref = (svc.get("Entries") or {}).get("@odata.id")
        if eref:
            _, ents = client.get(eref)
            for e in (ents or {}).get("Members", []):
                sev = e.get("Severity", "?")
                msg = e.get("Message", "")
                col = red if sev == "Critical" else yellow
                print(f"    {col(sev.ljust(9))} {msg}")


def show_manager(mgr: dict):
    section(f"Manager (BMC): {mgr.get('Name', '?')}")
    kv("Manager Type",     mgr.get("ManagerType"))
    kv("Manufacturer",     mgr.get("Manufacturer"))
    kv("Model",            mgr.get("Model"))
    kv("Firmware Version", mgr.get("FirmwareVersion"))
    kv("Power State",      mgr.get("PowerState"))
    kv("Health",           (mgr.get("Status") or {}).get("Health"))


def _first_system_path(client: Client, root: dict):
    sref = (root.get("Systems") or {}).get("@odata.id")
    if not sref:
        return None
    _, coll = client.get(sref)
    mem = (coll or {}).get("Members", [])
    return mem[0]["@odata.id"] if mem else None


def _report(st: int, obj, label: str):
    if st in (200, 204):
        print(green(f"  ✓ {label}"))
    else:
        msg = ""
        if isinstance(obj, dict):
            msg = (obj.get("error") or {}).get("message", "") or obj.get("_error", "")
        print(red(f"  ✗ {label} — HTTP {st} {msg}"))


def run_action(client: Client, root: dict, args):
    spath = _first_system_path(client, root)
    if not spath:
        print(red("  No ComputerSystem found (check auth/--port)."))
        sys.exit(1)
    print(bold(f"Server operations on {spath}"))

    def reset(rt):
        st, obj = client.post(spath + "/Actions/ComputerSystem.Reset", {"ResetType": rt})
        _report(st, obj, f"Reset {rt}")

    if args.power_on:    reset("On")
    if args.power_off:   reset("ForceOff")
    if args.reboot:      reset("GracefulRestart")
    if args.power_cycle: reset("PowerCycle")
    if args.led:
        st, obj = client.patch(spath, {"IndicatorLED": "Lit" if args.led == "on" else "Off"})
        _report(st, obj, f"Indicator LED {args.led}")
    if args.refresh:
        st, obj = client.post(spath + "/Actions/Oem/Simulator.RefreshInventory")
        _report(st, obj, "Refresh inventory")
    if args.clear_log:
        st, obj = client.post(spath + "/LogServices/SEL/Actions/LogService.ClearLog")
        _report(st, obj, "Clear event log")
    if args.view_log:
        _, ents = client.get(spath + "/LogServices/SEL/Entries")
        section("Event Log (SEL)")
        rows = (ents or {}).get("Members", [])
        if not rows:
            print(grey("  (empty)"))
        for e in rows:
            sev = e.get("Severity", "?")
            col = red if sev == "Critical" else (yellow if sev == "Warning" else green)
            print(f"  {grey(e.get('Created', ''))}  {col(sev.ljust(9))} {e.get('Message', '')}")


def _subs_url(client: Client, root: dict):
    """Resolve the EventService Subscriptions collection @odata.id."""
    es_ref = (root.get("EventService") or {}).get("@odata.id")
    if not es_ref:
        return None
    st, es = client.get(es_ref)
    if st != 200 or not es:
        return None
    return (es.get("Subscriptions") or {}).get("@odata.id")


def run_events(client: Client, root: dict, args) -> None:
    """Handle the push-model subscription flags, then exit."""
    subs_path = _subs_url(client, root)
    if not subs_path:
        print(red("  EventService not advertised by this BMC."))
        sys.exit(1)

    if args.list_subs:
        section("Event Subscriptions (push model)")
        st, coll = client.get(subs_path)
        rows = (coll or {}).get("Members", []) if st == 200 else []
        if not rows:
            print(grey("  (no subscriptions)"))
        for m in rows:
            _, sub = client.get(m["@odata.id"])
            if sub:
                kv(sub.get("Id", "?"), f"{sub.get('Destination')}  "
                   f"{grey('ctx=' + (sub.get('Context') or '—'))}")
        return

    if args.unsubscribe:
        st, obj = client.delete(f"{subs_path}/{args.unsubscribe}")
        _report(st, obj, f"Unsubscribe {args.unsubscribe}")
        return

    if args.subscribe:
        _serve_receiver(client, root, subs_path, args)


def _print_event(doc: dict) -> None:
    """Pretty-print one delivered Event document."""
    import time
    ts = time.strftime("%H:%M:%S")
    ctx = doc.get("Context", "")
    for ev in doc.get("Events", []):
        sev = ev.get("Severity", "?")
        col = red if sev == "Critical" else (yellow if sev == "Warning" else green)
        print(f"  {grey(ts)}  {col(sev.ljust(9))} "
              f"{cyan(str(ev.get('EventType', '?')).ljust(14))} "
              f"{ev.get('Message', '')}  {grey('ctx=' + ctx)}")


def _serve_receiver(client: Client, root: dict, subs_path: str, args) -> None:
    """Persistent event listener (DCIM-consumer stand-in): bind a receiver,
    auto-subscribe the BMC to it, then stream every pushed event until Ctrl-C.
    Unsubscribes on exit. Trigger events from the panel / other clients and
    watch them arrive here live."""
    import http.server
    import socketserver
    import threading

    class _Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            self.send_response(204)
            self.end_headers()
            try:
                _print_event(json.loads(raw))
            except Exception:
                print(grey(f"  (unparsable push: {raw[:80]!r})"))

        def log_message(self, *a):
            return

    host, port = args.listen_host, args.listen_port
    httpd = socketserver.TCPServer((host, port), _Receiver)
    httpd.allow_reuse_address = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    dest = f"http://{host}:{port}/events"
    section("Push-model event listener")
    print(grey(f"  Receiver listening on {dest}"))

    st, obj = client.post(subs_path, {
        "Destination": dest, "Protocol": "Redfish", "Context": "test_redfish.py"})
    if st != 201:
        _report(st, obj, "Subscribe")
        httpd.shutdown()
        return
    sub_id = obj.get("Id")
    print(green(f"  ✓ Subscribed (id {sub_id})"))
    print(grey("  Waiting for events — trigger from the panel or another "
               "client. Ctrl-C to quit."))
    print()

    try:
        threading.Event().wait()      # block until interrupted
    except KeyboardInterrupt:
        print()
    finally:
        client.delete(f"{subs_path}/{sub_id}")
        print(grey(f"  Cleaned up subscription {sub_id}"))
        httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description="Fetch and display all Redfish info for a server BMC.")
    ap.add_argument("ip", help="server BMC IP address")
    ap.add_argument("--port", type=int, default=443, help="BMC port (default 443)")
    ap.add_argument("--user", default="admin", help="username (default admin)")
    ap.add_argument("--pass", dest="pw", default="password", help="password (default password)")
    ap.add_argument("--scheme", choices=["http", "https"], default="http",
                    help="http (MVP default) or https")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verify (https self-signed)")
    ap.add_argument("--session", action="store_true",
                    help="authenticate via a Redfish session (X-Auth-Token) instead of HTTP Basic; "
                         "default is Basic (--user/--pass on every request)")
    # ── Server Operations ──
    g = ap.add_argument_group("server operations (perform an action, then exit)")
    g.add_argument("--power-on",    action="store_true", help="ComputerSystem.Reset On")
    g.add_argument("--power-off",   action="store_true", help="Reset ForceOff")
    g.add_argument("--reboot",      action="store_true", help="Reset GracefulRestart")
    g.add_argument("--power-cycle", action="store_true", help="Reset PowerCycle")
    g.add_argument("--led",         choices=["on", "off"], help="set indicator LED")
    g.add_argument("--refresh",     action="store_true", help="refresh inventory")
    g.add_argument("--clear-log",   action="store_true", help="clear the event log (SEL)")
    g.add_argument("--view-log",    action="store_true", help="print the event log (SEL) and exit")
    # ── Push-model Event Subscriptions ──
    e = ap.add_argument_group("event subscriptions (push model — EventDestination)")
    e.add_argument("--list-subs",   action="store_true", help="list active push subscriptions and exit")
    e.add_argument("--subscribe",   action="store_true",
                   help="start a local receiver, subscribe to it, and stream every pushed event live until Ctrl-C")
    e.add_argument("--unsubscribe", metavar="ID", help="delete the subscription with this Id and exit")
    e.add_argument("--listen-host", default="127.0.0.1", help="receiver bind/Destination host (default 127.0.0.1)")
    e.add_argument("--listen-port", type=int, default=9000, help="receiver port (default 9000)")
    args = ap.parse_args()

    base = f"{args.scheme}://{args.ip}:{args.port}"
    client = Client(base, args.user, args.pw, args.insecure)

    print(bold(f"Redfish probe → {base}/redfish/v1/"))

    st, root = client.get("/redfish/v1/")
    if st != 200 or not root:
        detail = (root or {}).get("_error") or f"HTTP {st}"
        print(red(f"  Cannot reach ServiceRoot: {detail}"))
        print(grey("  Is Redfish running and is this IP bound? Try --scheme https / --insecure, "
                   "or check --port."))
        sys.exit(1)

    # Opt-in session auth: log in once, use the token, log out on exit.
    if args.session:
        if client.login():
            atexit.register(client.logout)
            print(green("  ✓ Logged in — using session token (X-Auth-Token)"))
        else:
            print(yellow("  ! Session login failed — falling back to HTTP Basic."))

    # If any Server Operation was requested, run it and exit.
    if any([args.power_on, args.power_off, args.reboot, args.power_cycle,
            args.led, args.refresh, args.clear_log, args.view_log]):
        run_action(client, root, args)
        return

    # If any event-subscription flag was requested, run it and exit.
    if any([args.list_subs, args.subscribe, args.unsubscribe]):
        run_events(client, root, args)
        return

    kv("Redfish Version", root.get("RedfishVersion"))

    # Systems
    sref = (root.get("Systems") or {}).get("@odata.id")
    if sref:
        st, coll = client.get(sref)
        if st == 401:
            print(red("  401 Unauthorized — wrong --user/--pass."))
            sys.exit(1)
        for s in members(client, coll):
            show_system(s)
            show_network(client, s)
            show_storage(client, s)
            show_logs(client, s)

    # Chassis
    cref = (root.get("Chassis") or {}).get("@odata.id")
    if cref:
        _, coll = client.get(cref)
        for ch in members(client, coll):
            show_chassis(client, ch)

    # Managers
    mref = (root.get("Managers") or {}).get("@odata.id")
    if mref:
        _, coll = client.get(mref)
        for mgr in members(client, coll):
            show_manager(mgr)

    print()
    print(green("Done."))


if __name__ == "__main__":
    main()

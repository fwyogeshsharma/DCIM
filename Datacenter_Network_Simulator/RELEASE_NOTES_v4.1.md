# Datacenter Network Simulator — Release Notes v4.1

**Release Date:** June 19, 2026
**Version:** 4.1.0
**Compared against:** `Datacenter_Network_Simulator_Release_v4.0`

---

## Overview

Version 4.1 is a usability, realism, and packaging release built on top of the v4.0
Redfish/BMC and liquid-cooling foundation. The headline change is **zero-touch launch**:
`run.bat` (Windows) and `run.sh` (Linux) now self-configure the host — provisioning the
virtual network adapter that hosts device IPs and bootstrapping JWT authentication — so a
fresh checkout runs with a single command. Redfish gains a **push-model event
subscription** stack (EventDestination subscribe / unsubscribe / test-event). Several
fidelity gaps are closed: unconnected switch ports now report down with zero counters on
both SNMP and gNMI, link-break is constrained to production links only and no longer
emits duplicate `LinkDown`/`LinkUp` traps, and a powered-off server's CPU temperature
decays to zero. The release also ships full per-protocol architecture documentation, an
illustrated user guide, and a leaner topology set built around a single demo file.

---

## What's New

### 1. Zero-Touch Launch — Adapter Provisioning & Auth Bootstrap

The launch scripts now prepare the host before starting the app, removing the manual
adapter/credential setup that previous versions required.

**Windows (`run.bat`)**
- Self-elevates via UAC.
- Installs and enables the **Microsoft KM-TEST Loopback Adapter** through the new
  `install_loopback.ps1` — the dedicated NIC that hosts the simulated device IPs, instead
  of polluting a real interface with dozens of secondary addresses.
- Loads `auth.env` into the process before launch.

**Linux (`run.sh`, new)**
- Creates a dedicated kernel `dummy` interface **`dcim0`** (`modprobe dummy` + `ip link`),
  the counterpart of the Windows loopback adapter, and exports
  `DCIM_ADAPTER_FILTER=dcim0` so the binding dropdown lists only that interface.
- Overrides: `DCIM_DUMMY_NIC=<name>`, `DCIM_ADAPTER_FILTER=<a,b>`.
- The dummy NIC is recreated each launch (not persistent across reboot).

> Running `app/main.py` directly skips both steps — do that only when the adapter and
> `DCIM_AUTH_SECRET` are already set up.

### 2. Automatic Authentication Bootstrap (`bootstrap_auth.py`, new)

Both launch scripts run `bootstrap_auth.py` before startup, so JWT auth configures itself
on first run — no manual `set-password` step.

| Situation | Behavior |
|-----------|----------|
| No `auth.env` | Generates a stable `DCIM_AUTH_SECRET` + admin password hash, writes `auth.env` (gitignored, `chmod 600`). Prompts for the admin password on a real TTY; with no TTY, generates a random one and prints it **once**. |
| `auth.env` exists | Prompts *keep / recreate* (default **keep**); recreate rotates the secret and invalidates current logins. |

Non-interactive overrides for CI/scripting: `DCIM_BOOTSTRAP_PASSWORD`,
`DCIM_AUTH_RECREATE=1` (force replace), `DCIM_AUTH_RECREATE=0` (force keep). Default
username `admin` (override with `DCIM_AUTH_USERNAME`). The manual `set-password` flow
remains valid.

### 3. Redfish Event Subscriptions (Push Model)

Every BMC now supports the Redfish EventService push model — register external listeners
and have BMCs POST events to them.

| Endpoint | Purpose |
|----------|---------|
| `GET  /api/redfish/subscriptions` | List all EventDestinations across BMCs |
| `POST /api/redfish/subscribe` | Register a push subscriber on one BMC (destination, context, event types) |
| `POST /api/redfish/unsubscribe` | Delete a subscription by id |
| `POST /api/redfish/test-event` | Fire a test Event from one BMC to all its subscribers |

- **Subscribe form** added to both the desktop and Web Redfish panels.
- New **`reboot_bmc`** server action.
- **Redfish status chip** in the UI and a **Redfish tab on the Console panel**.
- Redfish panel state lifted to the global store (consistent across tab switches).

### 4. Redfish Default Port 443 → 8443

The simulated BMC HTTPS port now defaults to **8443**, avoiding a clash with privileged
port 443 (which needs elevation/root and commonly collides with other services). Update
any DCIM/pollers pointing at `:443`.

### 5. Realism Fixes

**Unconnected ports** — Ports with no peer (`connected_to_device is None`) now report
`oper-status DOWN` and **zero** in/out octets, packets, errors, and discards on both the
SNMP agent and gNMI (`oper_status` previously defaulted to 1, making empty ports look
live with phantom traffic).

**Link break scoped to production** — `break_link` / `restore_link` now affect **only
production links**; management, power, and cooling/water links stay up regardless of the
requested layer, matching how an operator-induced cable pull behaves.

**Duplicate trap fix** — A user-initiated link break/restore already sends an explicit
`LinkDown`/`LinkUp` trap; the new `RuleEngine.sync_iface_history()` resyncs the interface
oper-status snapshot so the next tick sees no transition and the interface rule no longer
fires a **duplicate** `LinkDown`/`LinkUp` (and no spurious `LinkFlap` on repeated toggles).

**CPU temp decay on power-off** — A powered-off server's CPU temperature now decays toward
**0** (chip dissipates no heat) instead of bottoming out at inlet/ambient.

### 6. Live Metrics & Metric-Tick Improvements

- **Server fan RPM** lifted to the device state store and surfaced on the Live Metrics
  Server view.
- `fan_rpm` is now a tickable metric (metric-tick limits, range 0–20000) with a dedicated
  **server section** on the Metric Tick panel.

### 7. gNMI Panel

- gNMI **proxy port is now editable from the Web UI**.
- gNMI proxy state stays in sync across the desktop and Web UIs.

### 8. BACnet Panel

- New **Targets** group showing a per-type device-count breakdown (mirrors the SNMP/gNMI
  panels), built dynamically from BACnet kinds (`ev2` + `plant:*`, mapped to friendly
  labels like "Verdigris EV2", "Cooling Tower").
- Chiller-plant BACnet fix.

### 9. Web UI

- Device List shows **both Mgmt IP and Prod IP** columns.
- BACnet, Redfish, and sFlow panel state lifted to the global store (state and running
  status survive tab switches; duplicate per-panel polling removed).

### 10. Topology Set Reorganized

- Removed the bulky reference topologies: `large_4dc_enterprise`, `large_datacenter_3tier`,
  `large_datacenter_spine_leaf`, `large_hyperscale_pod` (~260k lines of JSON dropped).
- `large_enterprise_wan.json` renamed/reworked into **`demo_single_dc.json`** as the
  default demo topology.
- `dual_dc_enterprise.json` segregated into clearly named **DC1 / DC2** halves.

### 11. Documentation & Tooling

- Per-protocol architecture docs: `docs/SNMP_ARCHITECTURE.md`,
  `docs/GNMI_ARCHITECTURE.md`, `docs/REDFISH_ARCHITECTURE.md`, `docs/SFLOW_ARCHITECTURE.md`.
- `docs/INSTALL.md` (+ `.docx`) — full install guide; expanded README install/auth/adapter
  sections and troubleshooting.
- `docs/SIMULATOR_USER_GUIDE.md` (+ `.docx`) with an illustrated `guide_assets/` screenshot
  set covering every panel and Live Metrics view.
- `tools/md_to_docx.py` — Markdown → DOCX converter for the guides.

### 12. Quality of Life

- Run scripts **build the Web UI before launch**.
- SNMPRec direct-write fallback (Windows, where SNMPSim holds files open) now logs **once**
  at INFO then drops to DEBUG, instead of spamming two INFO lines per device every tick.
- Debug logs disabled by default.
- Test scripts updated (`test_redfish.py` session-based testing, `test_gNMI.py`); legacy
  `testscripts/redfish_info.py` removed.

---

## API Additions

```
GET  /api/redfish/subscriptions
POST /api/redfish/subscribe | unsubscribe | test-event
POST /api/redfish/action          + reboot_bmc
```

---

## Migration Notes

- **Redfish port changed to 8443.** Repoint any pollers/DCIM integrations from `:443`.
  Existing saved panel configs may still carry 443 — clear or update them.
- **Regenerate SNMP/gNMI datasets** if you depend on per-port counters: unconnected ports
  now report down with zero counters; previously they appeared up with synthetic traffic.
- **Large reference topologies removed.** If you loaded `large_4dc_enterprise`,
  `large_datacenter_3tier`, `large_datacenter_spine_leaf`, or `large_hyperscale_pod`,
  switch to `demo_single_dc.json` or `dual_dc_enterprise.json`.
- **First launch self-configures.** On a fresh checkout, run `run.bat` (Windows, accepts
  the UAC prompt) or `sudo ./run.sh` (Linux). `auth.env` is generated automatically — save
  the printed password when running non-interactively. Launching `app/main.py` directly
  bypasses adapter and auth setup.
- **Link break is production-only.** Scripts/tests that broke management/power/cooling
  links via `break_link(..., layer=...)` are now no-ops for non-production layers.
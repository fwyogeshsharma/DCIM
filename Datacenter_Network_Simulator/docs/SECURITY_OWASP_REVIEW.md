# Security review against the OWASP Top 10, and the fix list

**Status:** findings, 2026-11-16. Nothing in this document has been fixed yet.
**Scope:** the simulator's management plane at commit `5b820a8`: the REST API on `:8001`, the web UI, authentication, file handling, and everything that runs a command as root.
**Companion:** the DCIM platform has its own review in the `dcim-platform` repo, `docs/39-security-owasp-review.md`.

## How to read this

The review was a code read against the OWASP Top 10 (2021). Nothing was run,
nothing was sent to a live service and no exploit was attempted.

| Mark | Meaning |
|---|---|
| **C** | Confirmed by reading the lines cited. |
| **S** | Suspected. The dangerous sink is confirmed; a working exploit path was not established. |

Line numbers are as of `5b820a8` and will drift. Search for the function name if a line has moved.

### What is deliberately not a finding

The emulated devices behave like real ones, weaknesses included: SNMP v2c with
a community string, unauthenticated Modbus/TCP and BACnet/IP, Redfish basic
auth with a default password. Real PDUs, BMS controllers and BMCs ship this
way, and a DCIM under test has to cope with it. Removing it would make the
simulator less faithful, not more secure.

What is reviewed is the simulator's **own control plane**: the thing an
operator logs into, which runs as root and can rewrite the host's addressing.

### What sets the severity

This is a lab tool that normally runs on a private host. Against that: it
runs as **root**, it listens on **all interfaces**, and several request fields
reach root-level commands unvalidated. On a shared network, the admin password
is the only thing between another machine and root on this one.

Not covered: dependency CVE lookup, a live penetration test, the desktop Qt UI
(`ui/main_window.py`), path traversal inside Starlette's static file serving,
device and socket caps for an uploaded topology (`fleet_lifecycle.py`), and
snmpsim variation modules executed from disk.

## Verdict by category

| # | Category | Verdict | The gap in one line |
|---|---|---|---|
| A01 | Broken access control | Weak | No roles: every valid token is effectively root. |
| A02 | Cryptographic failures | Partial | Plain HTTP on all interfaces; the live password is in tracked files. |
| A03 | Injection | Weak | Request and topology strings reach root-level commands and file paths unvalidated. |
| A04 | Insecure design | Weak | No login throttling; no upload cap; upload wipes the live estate before validating. |
| A05 | Security misconfiguration | Weak | Binds `0.0.0.0` as root with no privilege drop. |
| A06 | Vulnerable components | Partial | Floor-only Python dependencies, installed as root at launch. |
| A07 | Authentication failures | Partial | No revocation, no password change, 12 h tokens. |
| A08 | Integrity failures | Partial | An uploaded topology is trusted wholesale. |
| A09 | Logging and monitoring | Weak | Logins are not logged; nothing records who did what. |
| A10 | SSRF | Partial | Redfish subscriptions and trap targets accept any host. |

## Fix list, in order of risk

### P1. Do first

- [ ] **F1. Validate everything that reaches a root-level sink.** Closes A03-1, A03-3, A03-4, A01-2, A08-1.
  - Parse `ip_address` and `mgmt_ip` with `ipaddress.IPv4Address` in `Device.from_dict` and in `AddDeviceRequest`. Reject CIDR notation, path separators and anything that is not a single address.
  - Validate the subnet mask as a dotted netmask in the request schema, not later in `int()`.
  - Allow-list the adapter name against `get_interfaces()`, and make `DCIM_ADAPTER_FILTER` a hard restriction on the API rather than a filter on the dropdown.
- [ ] **F2. Remove the generated-PowerShell fallback.** Replace the `.ps1` generation in `core/ip_binder.py` with `netsh` called as an argument list, or delete the fallback. Escape the interface name in `_get_interface_ips_windows`. Closes A03-1, A03-2.
- [ ] **F3. Bind to loopback by default.** `127.0.0.1` unless `--host` is given. Document that exposing the API means putting it behind the Caddy proxy in `deploy/`. Closes A05-1, A02-1.
- [ ] **F4. Rotate the admin password and remove it from tracked files.** Three files carry it; read it from the environment or `auth.env` instead. Rotating matters more than removing: git history keeps the old value. Closes A02-2.

### P2. Next

- [ ] **F5. Login throttling, lockout and logging.** Count failures per account and per source address, back off, and log every attempt with its source. Closes A04-1, A07-2, A09-1.
- [ ] **F6. Close the unauthenticated write.** Put `require_auth` on `POST /api/provider/credentials`; gate `/docs`, `/redoc` and `/openapi.json` behind an environment flag; trim `/api/health`. Closes A01-3, A01-4.
- [ ] **F7. Roles and an actor on every action.** Three roles are enough: read-only; operator (faults, traps, overrides); admin (binding, uploads, protocol start and stop, v3 reveal). Pass the token claims into the handlers, and record the username in the fault ledger and in an audit log. Closes A01-1, A09-2, A09-3.
- [ ] **F8. Restrict outbound targets.** Redfish `destination` must be http or https to an allow-listed range; trap and sFlow receivers the same. Default allow-list: loopback and the managed planes. Closes A10-1, A10-2.

### P3. Then

- [ ] **F9. Safer uploads.** Cap the upload size; validate the new topology fully before clearing the running one. Closes A04-2, A08-1.
- [ ] **F10. Drop root where possible.** Run under a service account with `CAP_NET_ADMIN` and `CAP_NET_BIND_SERVICE`; add systemd hardening to `deploy/dcim.service`; run snmpsim children unprivileged once the sockets are bound. Closes A05-1, A05-2.
- [ ] **F11. Token lifetime and revocation.** Shorter expiry, a logout that revokes, and a password-change endpoint. Closes A07-1.
- [ ] **F12. Dependencies.** Pin Python dependencies with a hashed lock; use `npm ci` in the launcher; stop installing packages as root at launch. Then run `pip-audit` and `npm audit` and triage. Closes A06-1, A06-2, A08-2.

### P4. Small

- [ ] **F13.** Stop returning raw exception text from the API. Closes A05-3.
- [ ] **F14.** Add security headers and a TrustedHost check. Closes A05-4.
- [ ] **F15.** Export only `DCIM_*` keys from `auth.env` in `run.sh`. Closes A05-5.
- [ ] **F16.** Write `auth_users.json` and `sim_settings.json` with mode 0600; restrict `?reveal=true` to admin. Closes A02-4, A02-5.
- [ ] **F17.** Stop passing the token in the SSE URL; use a short-lived single-use ticket. Closes A02-3.
- [ ] **F18.** Bound `provider_tokens`. Closes A04-3.
- [ ] **F19.** Narrow CORS from `*` to the UI's origin. Closes A01-5.

## Findings

### A01 Broken access control

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A01-1 | High | C | No authorisation model. The token carries a `role`, but no router checks it: `require_auth` is attached at router level and the claims are discarded. Any valid token can change host addressing, start and stop root processes, inject faults and upload topologies. | `api/auth.py:169`; `api/main.py:66-86` |
| A01-2 | High | C | `POST /api/binding/adapter` accepts any adapter name. `DCIM_ADAPTER_FILTER` only restricts the dropdown. A caller can select the real NIC and bind addresses from an uploaded topology onto it as root, for example the LAN gateway's address. | `api/routers/binding.py:45-51,103`; `core/ip_binder.py:239`; `run.sh:50-53` |
| A01-3 | Medium | C | Unauthenticated `POST /api/provider/credentials` lets anyone rotate the mock provider's client id and secret and flush its tokens. | `api/main.py:91`; `api/routers/provider.py:63-75` |
| A01-4 | Low | C | Other unauthenticated endpoints: `/` lists the endpoint map; `/api/health` gives simulator state and the bound-address count; `/docs`, `/redoc` and `/openapi.json` are open; `/api/provider/oauth2/token` accepts default client credentials that sit in source. | `api/main.py:25-26,106,156`; `api/models/schemas.py:508-509` |
| A01-5 | Low | C | CORS is `allow_origins=["*"]`. Limited impact, because auth is a bearer header and not a cookie. | `api/main.py:29-34` |

Already right: one auth dependency covers every operational router, and the
event stream has its own token check.

### A02 Cryptographic failures

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A02-1 | Medium | C | The management API is plain HTTP on all interfaces. TLS exists only if the optional Caddy proxy is deployed, so the password and token cross the network in clear otherwise. | `api/main.py:254-263`; `deploy/Caddyfile` |
| A02-2 | Medium | C | The live admin password is hard-coded in tracked files. | `tools/_live_api.py:19`; `tools/export_to_opendcim.py:2179`; `docs/dcim/16-simulator-integration.md:133` |
| A02-3 | Low | C | The token is passed in the URL for the event stream, so it can land in proxy logs and browser history. | `api/auth.py:219-221`; `webui/src/store/useStore.ts:563-564` |
| A02-4 | Low | C | SNMPv3 passphrases are stored in plaintext in `sim_settings.json`, written with the default umask, and `GET /api/snmp/v3?reveal=true` returns them to any token holder. | `core/sim_settings.py:84-89`; `api/routers/snmp.py:496-505` |
| A02-5 | Low | C | `auth_users.json` is written with no permission change. `auth.env` gets 0600 on POSIX but is world-readable on a Windows checkout. | `api/auth.py:119-123`; `bootstrap_auth.py:177` |

Already right: PBKDF2-SHA256 at 200,000 rounds with a 16-byte salt and a
constant-time compare. HS256 with a 48-byte random secret and enforced expiry.
The API refuses to start without a secret. `auth.env`, `auth_users.json` and
`sim_settings.json` are gitignored. There is no built-in default password:
setup prompts for one or generates one.

### A03 Injection

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A03-1 | **Critical on Windows hosts** | C (read, not executed) | PowerShell injection as Administrator. `POST /api/binding/subnet-mask` stores an unvalidated string. If the selected adapter name does not resolve, bind falls back to a generated `.ps1` that interpolates the mask and each address unquoted and runs with `-ExecutionPolicy Bypass`. The caller controls all three inputs: a bogus adapter name to force the fallback, the mask, and the addresses through a topology upload. | `api/models/schemas.py:65-66`; `api/routers/binding.py:54-60`; `core/ip_binder.py:474-479,641-663,709-745` |
| A03-2 | Medium | C | `_get_interface_ips_windows` puts the interface name into a PowerShell command inside single quotes without escaping. Latent: current callers pass names enumerated from the OS. | `core/ip_binder.py:170-173` |
| A03-3 | High | S | Device `ip_address` and `mgmt_ip` are never validated as addresses, from an uploaded topology or from `POST /api/devices`. They become file paths written as root, so a `../` value writes outside `datasets/`. The suffix is fixed; the content is partly shaped by the caller. | `core/device_manager.py:2527-2539`; `core/topology_engine.py:606-618`; `api/models/schemas.py:443-449`; `core/snmprec_generator.py:694-696,796,811`; `core/gnmi_data_generator.py:139-141` |
| A03-4 | Medium | S | The same unvalidated addresses go to `iptables -A DCIM_DARK -d <ip> -j DROP`. A device whose address is `0.0.0.0/0` and which goes dark would drop all inbound traffic to the host. It is an argument list, so there is no shell injection. | `core/dark_firewall.py:104-106`; `core/device_state_store.py:3456-3470` |

Already right: no `shell=True`, `os.system`, `pickle`, unsafe `yaml.load` or
`eval` in application code. Linux `ip` and `iptables` calls are argument
lists. There is no SQL in the runtime. The web UI has no
`dangerouslySetInnerHTML` or `innerHTML`, so React escapes device names.

### A04 Insecure design

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A04-1 | Medium | C | No rate limit or lockout on login. PBKDF2 cost is the only brake, and it also makes login a way to exhaust the CPU. | `api/routers/auth.py:24-34` |
| A04-2 | Medium | C | Uploads are read fully into memory with no size cap. Upload also clears the live device registry before validating the new file, so a malformed file wipes the running estate. | `api/routers/topology.py:79,84-87`; `api/routers/floorplan.py:150` |
| A04-3 | Low | C | `provider_tokens` grows without bound; anyone holding the published default client credentials can mint tokens indefinitely. | `api/routers/provider.py:96-98` |

Fault, trap and override endpoints have no confirmation step. That is
acceptable in a lab tool; the missing actor is covered under A09.

### A05 Security misconfiguration

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A05-1 | High | C | The default bind is `0.0.0.0` and `[::]`, with no host option in headless mode. The process runs as root with no privilege drop and no systemd sandboxing; the unit relies on an external firewall. | `api/main.py:204,268-272`; `app/main.py:356`; `deploy/dcim.service:14` |
| A05-2 | Low | C | snmpsim children are kept at root, so the SNMP parser that faces the network runs as root. | `simulator/snmpsim_controller.py:673,692` |
| A05-3 | Low | C | Raw exception text is returned to clients. | `api/routers/topology.py:81,150`; `binding.py:42`; `traps.py:122` |
| A05-4 | Low | C | No security headers and no TrustedHost middleware. | `api/main.py` |
| A05-5 | Low | C | `run.sh` exports every key found in `auth.env` into root's environment without restricting it to `DCIM_*`. | `run.sh:82` |

Already right: no forced default credential, and no debug or reload flag in
the launch path.

### A06 Vulnerable and outdated components

CVE status is unverified for everything below.

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A06-1 | Medium | C | Every Python dependency is a `>=` floor with no lock and no hashes, installed by `pip install -r` on first launch, as root on Linux. The floors are old: fastapi 0.100, uvicorn 0.23, cryptography 41, protobuf 4.25, grpcio 1.60, Jinja2 3.1.2, python-multipart 0.0.9. | `requirements.txt`; `run.sh:19-24` |
| A06-2 | Low | C | The web UI has a tracked lockfile but the launcher runs `npm install`, not `npm ci`. `dagre ^0.8.5` is long unmaintained. | `run.sh:98`; `webui/package.json` |

### A07 Identification and authentication failures

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A07-1 | Medium | C | No server-side revocation or logout, and no password-change endpoint. A stolen token is valid for 12 h; rotating `DCIM_AUTH_SECRET` and restarting is the only kill switch. | `api/auth.py:51` |
| A07-2 | Medium | C | Brute force is unthrottled. Same as A04-1. | `api/routers/auth.py:24-34` |
| A07-3 | Low | C | The token is kept in `localStorage`. No script-injection sink was found that could read it. | `webui/src/api/client.ts:14-16` |
| A07-4 | Low | C | The only password policy is a minimum of 8 characters. | `bootstrap_auth.py:66,75` |

Already right: a dummy-hash verification for unknown users and a generic 401
prevent username enumeration.

### A08 Software and data integrity failures

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A08-1 | High | C | An uploaded topology is trusted wholesale. No schema or value validation runs before its fields drive root-level address binding, firewall rules and file paths. See A03. | `core/device_manager.py:2527-2539` |
| A08-2 | Medium | C | Unpinned packages are installed from PyPI and npm as root at launch. See A06. | `run.sh` |
| A08-3 | Low | C | Datasets on disk are adopted again on a fingerprint match. That is a consistency check, not an integrity check. | `api/routers/topology.py:107` |

Already right: deserialisation is JSON only, with unknown fields dropped.
There is no auto-update or remote download in the runtime path.

### A09 Logging and monitoring failures

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A09-1 | Medium | C | HTTP access logging is disabled, and login success and failure are not logged at all. | `api/main.py:258-259`; `api/routers/auth.py:25-34` |
| A09-2 | Medium | C | No actor attribution anywhere. The fault ledger records what was injected and a source, not the user. Protocol start and stop is recorded by path only. | `core/fault_ledger.py:228-239`; `api/main.py:50-60` |
| A09-3 | Low | C | Adapter, mask and bind changes, topology upload and v3 credential reveal appear only as transient UI and event-stream messages. | `api/routers/binding.py:50,59` |

Already right: no secrets in log calls. The v3 arguments file is written 0600
and never logged. Fault injections do have a durable ledger.

### A10 Server-side request forgery

| ID | Sev | Conf | Finding | Where |
|---|---|---|---|---|
| A10-1 | Medium | C | `POST /api/redfish/subscribe` stores any `destination` with no scheme or host check, and the simulator then POSTs to it: a blind POST to internal services or cloud metadata from a root host. The emulated Redfish EventService shares the code path, where subscriber headers are also applied. | `api/routers/redfish.py:181-188`; `simulator/redfish_device.py:161-174,187-215` |
| A10-2 | Medium | C | Trap receivers, per-device and per-plane destinations and the sFlow collector accept any address and port. The simulator can be pointed to send UDP from roughly 960 source addresses at an arbitrary host. | `api/routers/snmp.py:400-423`; `api/models/schemas.py:84-91`; `api/routers/sflow.py:70-76` |

## Before fixing

1. Re-read the cited lines. The code has moved since.
2. For anything marked **S**, establish the path first, in a throwaway VM, never on the live host.
3. F1 and F2 change how binding accepts input. Test bind, unbind and the orphan reaper on both Linux and Windows afterwards.
4. F3 changes what the DCIM platform's collectors can reach. Device protocol ports (161, 502, 8443, 47808, 50051) stay on the device addresses; only the management API on `:8001` moves to loopback.
5. Tick the box here when a fix lands, with the commit hash beside it.

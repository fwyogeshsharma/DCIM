# Installation & Deployment Guide

How to install the Datacenter Network Simulator locally on **Windows** and **Linux**,
and how to deploy it headless on a **cloud Linux VM** as a `systemd` service.

---

## Contents

- [Prerequisites](#prerequisites)
- [Local Setup — Windows](#local-setup--windows)
- [Local Setup — Linux](#local-setup--linux)
- [Deployment — Cloud Linux VM (headless + systemd)](#deployment--cloud-linux-vm-headless--systemd)
- [Verifying the Install](#verifying-the-install)
- [Ports & Firewall](#ports--firewall)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Version | Needed for |
|-------------|---------|------------|
| Python | 3.11+ | always |
| Node.js + npm | 18+ | building the web UI (`webui/dist`) |
| OS | Windows 10/11, or Linux (Ubuntu 20.04+) | always |
| Privileges | Administrator (Windows) / root (Linux) | IP binding, loopback/dummy NIC |

The app binds a virtual IP per simulated device, so it needs elevated privileges:

- **Windows** — adds IPs via the Win32 `AddIPAddress` API onto the *Microsoft KM-TEST
  Loopback Adapter*. `run.bat` self-elevates (UAC) and installs that adapter.
- **Linux** — adds IPs via `ip addr add` onto a kernel `dummy` interface (`dcim0`).
  `run.sh` creates it. Must run as `root`.

---

## Local Setup — Windows

### 1. Clone

```bat
git clone <repo-url>
cd Datacenter_Network_Simulator
```

### 2. Virtual environment + dependencies

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 3. Run

```bat
run.bat
```

Optional arguments (forwarded to the app):

```bat
run.bat --headless --port 8000
```

- `--headless` — start the REST API + Web UI only, no Qt desktop GUI.
- `--port <n>` — port for the API + Web UI (default `8000`).

### 4. Authentication

On first run it prompts you to set a password. If credentials already exist, it gives
two options — press `K` to keep the existing one, or `n` to create a new one.

---

## Local Setup — Linux

### 1. Clone

```bash
git clone <repo-url>
cd Datacenter_Network_Simulator
```

### 2. Virtual environment + dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Run

First make `run.sh` executable with Unix line endings (needed if cloned on Windows):

```bash
chmod +x run.sh && sed -i 's/\r$//' run.sh
```

Then run:

```bash
sudo ./run.sh --headless
```

Optional arguments (forwarded to the app):

```bash
sudo ./run.sh --headless --port 8000
```

- `--port <n>` — port for the API + Web UI (default `8000`).

### 4. Authentication

On first run it prompts you to set a password. If credentials already exist, it gives
two options — press `K` to keep the existing one, or `n` to create a new one.

---

## Deployment — Cloud Linux VM (headless + systemd)

Target: a plain Ubuntu/Debian VM (AWS EC2, Azure, GCP, etc.) running the **REST API +
Web UI only** — no desktop GUI. Headless mode uses `QCoreApplication`, so the Qt
`xcb`/X11 libraries from step 3 above are **not** required.

### 1. Base packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
# Node.js 18+ only if you build the web UI on the VM (else build elsewhere and copy webui/dist):
sudo apt install -y nodejs npm
```

### 2. Clone and install (as a deploy user)

```bash
sudo mkdir -p /opt/dcim
sudo chown "$USER" /opt/dcim
git clone <repo-url> /opt/dcim
cd /opt/dcim
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Build the web UI once

The systemd unit below sets `SKIP_WEBUI_BUILD=1`, so build it now (or copy a prebuilt
`webui/dist/` from elsewhere):

```bash
cd /opt/dcim/webui && npm install && npm run build && cd /opt/dcim
```

### 4. Configure authentication (once, interactively)

Do this **before** enabling the service so the admin password is one you chose (a
non-interactive first run would auto-generate a random password and only print it to
the journal). Either let the bootstrap prompt you:

```bash
sudo .venv/bin/python bootstrap_auth.py        # prompts for admin password
```

…or set it non-interactively:

```bash
sudo DCIM_BOOTSTRAP_PASSWORD='choose-a-strong-one' .venv/bin/python bootstrap_auth.py
```

Both write `/opt/dcim/auth.env` (gitignored, `chmod 600`) containing a stable
`DCIM_AUTH_SECRET` + the admin password hash. Default username is `admin`.

### 5. Create the systemd service

The service runs as **root** (required for the `dcim0` dummy NIC and IP binding) and
calls `run.sh`, which sets up the dummy NIC, loads `auth.env`, and starts the app
headless. Create `/etc/systemd/system/dcim.service`:

```ini
[Unit]
Description=Datacenter Network Simulator (headless API + Web UI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/dcim
# Web UI already built in step 3; skip the per-start npm build.
Environment=SKIP_WEBUI_BUILD=1
# dcim0 + IP binding need root and the dummy kernel module.
ExecStartPre=/sbin/modprobe dummy
ExecStart=/opt/dcim/run.sh --headless --port 8000
Restart=always
RestartSec=5
# Optional hardening (keep NET_ADMIN — it is required for IP binding):
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=false

[Install]
WantedBy=multi-user.target
```

> `run.sh` must be executable with Unix line endings:
> `chmod +x /opt/dcim/run.sh && sed -i 's/\r$//' /opt/dcim/run.sh`

### 6. Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dcim.service
sudo systemctl status dcim.service
sudo journalctl -u dcim.service -f          # follow logs
```

The API + Web UI now serve on **http://0.0.0.0:8000** (headless binds all interfaces).

### 7. Expose it safely

The API binds `0.0.0.0` and is protected by JWT auth, but you should still:

- **Restrict inbound TCP 8000** in the cloud security group / firewall to trusted IPs.
- Prefer terminating **TLS at a reverse proxy** (nginx/Caddy) in front of port 8000
  rather than exposing it directly — auth tokens otherwise travel over plain HTTP.

Example UFW rule (if you front it with nginx, open 443 instead and keep 8000 local):

```bash
sudo ufw allow from <your-ip> to any port 8000 proto tcp
```

---

## Verifying the Install

```bash
# Health endpoint is open (no auth):
curl -s http://localhost:8000/api/health

# Log in and get a token:
curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}'
```

- Web UI: **http://<host>:8000/web**
- Swagger docs: **http://<host>:8000/docs** (click **Authorize**, paste the token)
- Linux: `ip link show dcim0` should exist; the binding dropdown lists only `dcim0`.
- Windows: `Get-NetAdapter | ? InterfaceDescription -like '*KM-TEST Loopback*'` Status `Up`.

---

## Ports & Firewall

| Port | Protocol | Purpose | Expose externally? |
|------|----------|---------|--------------------|
| 8000 | TCP | REST API + Web UI | Yes — restrict to trusted IPs / behind TLS proxy |
| 161 | UDP | SNMP per-device virtual IPs (on `dcim0`) | No (internal to the host) |
| 162 | UDP | SNMP trap receiver | Only if external devices send traps |
| 57400 | TCP | gNMI server | No (local) |
| 1161 | UDP | SNMP management endpoint (SET) | No (local) |
| 6343 | UDP | sFlow collector | No (local) |

On a cloud VM the only port that normally needs an inbound rule is **8000** (or 443 if
fronted by a reverse proxy).

---

## Troubleshooting

**`./run.sh: command not found` / bad interpreter (Linux)**
— Missing exec bit or CRLF line endings: `chmod +x run.sh && sed -i 's/\r$//' run.sh`.

**Service starts but API never comes up (`journalctl` shows an auth error)**
— `DCIM_AUTH_SECRET` not set. Run step 4 to create `auth.env`, then
`sudo systemctl restart dcim.service`.

**Binding dropdown empty / `dcim0` missing (Linux)**
— Not root, or `dummy` module unavailable. Service must run as root;
`sudo modprobe dummy` then check `ip link show dcim0`.

**KM-TEST Loopback Adapter not installed (Windows)**
— `run.bat` installs it via `install_loopback.ps1`. If it reports failure, install
manually: Device Manager → Action → Add legacy hardware → Network adapters → Microsoft
→ *Microsoft KM-TEST Loopback Adapter*.

**Headless import error mentioning `libGL` / Qt**
— Even headless imports PySide6. Install the minimal libs: `sudo apt install -y libgl1
libglib2.0-0`. The full `xcb` list is only needed for the desktop GUI.

**Port 57400 fails to bind (Windows)**
— Hyper-V/WinNAT reserved it. See the WinNAT fix in [README.md](../README.md#ports-used).

**Login fails with "Invalid username or password" (Linux)**
— The `$` in the hash was mangled. `auth.env` values must use **single** quotes
(`'pbkdf2_sha256$...'`). Re-run `bootstrap_auth.py` or fix the quoting and restart.

# Datacenter Network Simulator

A desktop application that simulates a full datacenter network — SNMP, gNMI, sFlow, trap rules, and a live topology view — with a built-in REST API and web UI.

---

## Contents

- [Requirements](#requirements)
- [Windows Setup](#windows-setup)
- [Linux Setup](#linux-setup)
- [Authentication](#authentication)
- [Running the App](#running-the-app)
- [Web UI (Development Mode)](#web-ui-development-mode)
- [Building a Standalone Executable](#building-a-standalone-executable)
- [Ports Used](#ports-used)

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.11 or newer |
| Node.js | 18 or newer _(only for web UI development)_ |
| OS | Windows 10/11 or Linux (Ubuntu 20.04+) |

> **Linux only:** IP binding requires `root`. Run the app with `sudo`.

---

## Windows Setup

### 1. Clone the repository

```bat
git clone <repo-url>
cd Datacenter_Network_Simulator
```

### 2. Create a virtual environment

```bat
python -m venv .venv
```

### 3. Install Python dependencies

```bat
.venv\Scripts\pip install -r requirements.txt
```

### 4. Run

```bat
run.bat
```

Or directly:

```bat
.venv\Scripts\python app/main.py
```

> `run.bat` self-elevates (UAC prompt) and, before launch, installs + enables the
> **Microsoft KM-TEST Loopback Adapter** via `install_loopback.ps1` (the adapter that
> hosts the simulated device IPs) and bootstraps authentication. Running `app/main.py`
> directly skips these — do it only when the adapter and `DCIM_AUTH_SECRET` are already
> set up.

---

## Linux Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd Datacenter_Network_Simulator
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
```

### 3. Install Python dependencies

```bash
.venv/bin/pip install -r requirements.txt
```

### 4. Install Qt platform dependencies

On Ubuntu/Debian, Qt requires several system libraries:

```bash
sudo apt update
sudo apt install -y \
    libgl1 libglib2.0-0 libxcb-cursor0 \
    libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xinerama0 libxcb-xfixes0 \
    fonts-noto-color-emoji
```

### 5. Run

```bash
sudo ./run.sh
```

Or directly:

```bash
sudo .venv/bin/python app/main.py
```

> `sudo` is required for binding virtual IP addresses to network interfaces (port 161 and IP aliasing). Without it, SNMP and IP binding features will not work.

### Network adapter for device IPs (dummy NIC)

The simulator assigns each virtual device its own IP. Those IPs must live on a
network interface. Binding them to a real NIC (`eth0`/`ens33`) works but pollutes
it with dozens of secondary IPs that get ARP-advertised on the physical LAN.

Instead, `run.sh` creates a dedicated **kernel `dummy` interface** (`dcim0`) — the
Linux counterpart of the Windows *KM-TEST Loopback Adapter*. Device IPs are bound
there, isolated from real NICs, with no cable or link required. On each launch (as
root) `run.sh` automatically:

1. `modprobe dummy` and creates `dcim0` if it does not already exist, then brings it up.
2. Exports `DCIM_ADAPTER_FILTER=dcim0` so the binding dropdown lists **only** `dcim0`.

No action needed — just `sudo ./run.sh`. To do it manually:

```bash
sudo modprobe dummy
sudo ip link add dcim0 type dummy
sudo ip link set dcim0 up
```

| Override | Effect |
|----------|--------|
| `DCIM_DUMMY_NIC=<name>` | Use a different dummy interface name (default `dcim0`). |
| `DCIM_ADAPTER_FILTER=<a,b>` | Pre-set your own adapter filter; `run.sh` then leaves it untouched (e.g. to bind a real NIC). |

> The dummy NIC is **not persistent** across reboot — `run.sh` recreates it each
> launch. For a permanent interface, configure it in systemd-networkd or netplan.
> There is no "enable adapter" step on Linux: unlike Windows loopback, `lo` already
> hosts arbitrary IPs, but the app deliberately hides `lo` and uses `dcim0` instead.

---

## Authentication

The REST API and Web UI require **username + password** login. The server issues a
short-lived **JWT** (HS256); the browser sends it as a bearer token, and the live
event stream (SSE) passes it as a query param. The implementation is pure standard
library — no extra dependencies.

Two environment variables gate the API:

| Variable | Purpose |
|----------|---------|
| `DCIM_AUTH_SECRET` | JWT signing secret. **Required** — the API will not start without it. |
| `DCIM_AUTH_PASSWORD_HASH` | PBKDF2 hash of the password (single-user mode). |

Optional: `DCIM_AUTH_USERNAME` (default `admin`), `DCIM_AUTH_TTL_HOURS` (default 12),
`DCIM_AUTH_USERS_FILE` (default `auth_users.json`).

> Without `DCIM_AUTH_SECRET` the API thread logs an error and does **not** start.
> On the desktop app, the Qt GUI still runs — only the API / Web UI are disabled.

### Automatic setup (recommended)

Both `run.bat` and `run.sh` run `bootstrap_auth.py` before launch, so authentication
configures itself on first run — no manual `set-password` step:

- **No `auth.env`** → generates a stable `DCIM_AUTH_SECRET` + an admin password hash
  and writes `auth.env` (gitignored, `chmod 600`). In a real console it prompts you
  to choose the admin password; with no TTY it generates a random one and prints it
  **once** — save it.
- **`auth.env` already exists** → prompts *Keep existing credentials or create new?*
  (default **keep**). Choosing *new* rotates the secret and invalidates current logins.

Default username is `admin` (override with `DCIM_AUTH_USERNAME`). Non-interactive
overrides for CI / scripting:

| Variable | Effect |
|----------|--------|
| `DCIM_BOOTSTRAP_PASSWORD` | Use this admin password instead of prompting. |
| `DCIM_AUTH_RECREATE=1` | Force-replace existing `auth.env`. |
| `DCIM_AUTH_RECREATE=0` | Force-keep existing `auth.env`. |

> Windows: `run.bat` self-elevates (UAC) and also loads `auth.env` into the process
> before starting the app. The generated secret/hash live in a plaintext gitignored
> file — fine for a simulator, not production secret management.

The manual steps below remain valid if you prefer to set credentials yourself.

### Generate credentials

```bash
python -m api.auth set-password
```

Prompts for username + password, then prints `DCIM_AUTH_SECRET` and
`DCIM_AUTH_PASSWORD_HASH`. Reuse the **same** secret across restarts, or existing
logins are invalidated.

### Set the variables — Windows

Persist them at User scope (so every launch inherits them):

```powershell
[Environment]::SetEnvironmentVariable('DCIM_AUTH_SECRET', '<secret>', 'User')
[Environment]::SetEnvironmentVariable('DCIM_AUTH_PASSWORD_HASH', '<hash>', 'User')
```

### Set the variables — Linux

`run.sh` auto-loads an `auth.env` file from the project root (the values survive
`sudo`, which otherwise strips the caller's environment):

```bash
cp auth.env.example auth.env
python -m api.auth set-password     # paste the printed values into auth.env
chmod 600 auth.env
```

> **Use single quotes in `auth.env`.** The PBKDF2 hash contains `$` characters that
> double quotes would let the shell mangle. `run.sh` parses the file literally, but
> single quotes are safe everywhere. `auth.env` and `auth_users.json` are gitignored.

### Multiple users

```bash
python -m api.auth add-user         # prompts username / password / role
```

Writes `auth_users.json`. Once that file exists it **fully replaces** the
env-defined user — so add `admin` to it too when switching to multi-user. The token
carries each user's `role`, ready for future per-route authorization.

### Get a token (CLI / scripting)

```bash
curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}'
```

---

## Running the App

When the app starts it:

1. Opens the Qt desktop GUI
2. Starts the REST API server at `http://localhost:8000`
3. Serves the built web UI at `http://localhost:8000/web`

Open your browser at **http://localhost:8000/web** and **log in** with your username
and password (see [Authentication](#authentication)).

The REST API docs (Swagger) are available at **http://localhost:8000/docs** — click
**Authorize** and paste a token to call protected endpoints. `GET /api/health` is
open (no auth).

### Headless mode (Linux server — no GUI)

Run the API + Web UI without a display, on a chosen port:

```bash
sudo ./run.sh --headless --port 8001
```

`--port` selects the API/Web port (default 8000). `run.sh` loads `auth.env` first.

---

## Web UI (Development Mode)

Run the Vite dev server for live-reload frontend development. The desktop app (or standalone `uvicorn`) must be running first so the API is available.

```bash
cd webui
npm install
npm run dev
```

The dev server starts at **http://localhost:5173** and proxies all `/api/*` requests to `http://localhost:8000`.

### Build the web UI for production

```bash
cd webui
npm run build
```

Output goes to `webui/dist/`. The Python app serves this directory automatically.

---

## Building a Standalone Executable

### Windows

```bat
build_windows.bat
```

Output: `dist\Datacenter-Network-Simulator.exe`

The exe bundles Python, all dependencies, and the built web UI into a single file. No installation required on the target machine.

### Linux

```bash
chmod +x build_linux.sh
./build_linux.sh
```

Output: `dist/Datacenter-Network-Simulator`

Run the built binary with:

```bash
sudo dist/Datacenter-Network-Simulator
```

---

## Ports Used

| Port | Protocol | Purpose |
|------|----------|---------|
| 8000 | TCP | REST API + Web UI |
| 161 | UDP | SNMP simulator (per-device virtual IPs) |
| 162 | UDP | SNMP trap receiver |
| 57400 | TCP | gNMI server |
| 1161 | UDP | SNMP management endpoint (SET commands) |
| 6343 | UDP | sFlow collector |

> **Windows gNMI port conflict:** Hyper-V and WinNAT sometimes reserve port 57400. If the gNMI server fails to bind, run in an elevated PowerShell:
> ```powershell
> net stop winnat
> netsh int ipv4 add excludedportrange protocol=tcp startport=57400 numberofports=1
> net start winnat
> ```

---

## Troubleshooting

**`ModuleNotFoundError` on startup**
— Virtual environment not activated or `pip install -r requirements.txt` not run.

**Qt `xcb` plugin error on Linux**
— Install the system libraries listed in step 4 of the Linux setup.

**SNMP binding fails on Linux**
— App must be run with `sudo`. Without root, port 161 and IP aliasing are blocked by the OS.

**Binding dropdown is empty or missing `dcim0` (Linux)**
— The dummy NIC was not created. Run with `sudo` (creation needs root), or create it
manually: `sudo modprobe dummy && sudo ip link add dcim0 type dummy && sudo ip link set
dcim0 up`. If you set `DCIM_ADAPTER_FILTER` to a name that no interface matches, the
list will be empty — unset it or point it at a real interface.

**Web UI shows blank page**
— Either run `npm run build` in `webui/` first, or use the dev server (`npm run dev`) during development.

**Port 57400 already in use on Windows**
— Apply the WinNAT port exclusion fix above.

**Web UI login fails with "Invalid username or password" (Linux)**
— The `$` in `DCIM_AUTH_PASSWORD_HASH` was mangled by the shell. In `auth.env`, wrap
the hash in **single** quotes (`'pbkdf2_sha256$...'`), then restart. Verify the hash
matches the password you set with `python -m api.auth set-password`. Default username
is `admin` unless `DCIM_AUTH_USERNAME` is set.

**REST API / Web UI never starts (no error in the GUI)**
— `DCIM_AUTH_SECRET` is not set. Generate it with `python -m api.auth set-password`
and export it (Windows User env) or put it in `auth.env` (Linux). The API refuses to
serve without a signing secret.

**`./run.sh: command not found` on Linux**
— Missing exec bit or Windows line endings. Fix with:
> ```bash
> chmod +x run.sh
> sed -i 's/\r$//' run.sh
> ```

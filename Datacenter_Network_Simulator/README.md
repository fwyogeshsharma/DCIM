# Datacenter Network Simulator

A desktop application that simulates a full datacenter network — SNMP, gNMI, sFlow, trap rules, and a live topology view — with a built-in REST API and web UI.

---

## Contents

- [Requirements](#requirements)
- [Windows Setup](#windows-setup)
- [Linux Setup](#linux-setup)
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

---

## Running the App

When the app starts it:

1. Opens the Qt desktop GUI
2. Starts the REST API server at `http://localhost:8000`
3. Serves the built web UI at `http://localhost:8000/web`

Open your browser at **http://localhost:8000/web** to use the web interface.

The REST API docs (Swagger) are available at **http://localhost:8000/docs**.

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

**Web UI shows blank page**
— Either run `npm run build` in `webui/` first, or use the dev server (`npm run dev`) during development.

**Port 57400 already in use on Windows**
— Apply the WinNAT port exclusion fix above.

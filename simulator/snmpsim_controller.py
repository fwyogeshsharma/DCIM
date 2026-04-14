"""
SNMPSim Controller - Manages the snmpsim process lifecycle.

Each simulated device gets its own IP:161 endpoint so external monitoring
tools can poll them as if they were real hardware.  SNMPSim is started with
one --agent-udpv4-endpoint flag per device IP.

Dataset directory layout expected by snmpsim-lextudio:
    datasets/snmp/
        <device_ip>.snmprec         # e.g. datasets/snmp/192.168.1.10.snmprec
"""
from __future__ import annotations
import os
import sys
import subprocess
import threading
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Callable, List


class SNMPSimController:
    """Start, stop, and monitor the snmpsim process."""

    def __init__(self, datasets_dir: str = "datasets/snmp"):
        self.datasets_dir = str(Path(datasets_dir).resolve())
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._log_callback: Optional[Callable[[str], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None
        self._ready_callback: Optional[Callable[[], None]] = None
        self._running = False
        self._ready = False   # True once snmpsim is actually listening
        self._active_endpoints: List[str] = []

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def set_log_callback(self, cb: Callable[[str], None]):
        self._log_callback = cb

    def set_status_callback(self, cb: Callable[[str], None]):
        self._status_callback = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        """Called once SNMPSim is actually listening on its UDP endpoint."""
        self._ready_callback = cb

    def _log(self, msg: str):
        if self._log_callback:
            self._log_callback(msg)

    def _set_status(self, status: str):
        if self._status_callback:
            self._status_callback(status)

    # ------------------------------------------------------------------ #
    #  State                                                               #
    # ------------------------------------------------------------------ #

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def is_ready(self) -> bool:
        """True once SNMPSim has logged its 'Listening at UDP/IPv4 endpoint' line."""
        return self._ready

    def get_pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    def get_active_endpoints(self) -> List[str]:
        return list(self._active_endpoints)

    # ------------------------------------------------------------------ #
    #  Executable discovery                                                #
    # ------------------------------------------------------------------ #

    def _find_snmpsim(self) -> Optional[str]:
        candidates = [
            "snmpsim-command-responder",
            "snmpsim-command-responder.exe",
            "snmpsimd",
            "snmpsimd.py",
            "snmpsimd.exe",
        ]
        for name in candidates:
            path = shutil.which(name)
            if path:
                return path
        python_scripts = Path(sys.executable).parent / "Scripts"
        for name in candidates:
            p = python_scripts / name
            if p.exists():
                return str(p)
        return None

    # ------------------------------------------------------------------ #
    #  Start                                                               #
    # ------------------------------------------------------------------ #

    def start(self, device_ips: List[str], port: int = 161) -> bool:
        """
        Start SNMPSim listening on *device_ip*:*port* for every IP in
        *device_ips*.  The caller is responsible for having already bound
        those IPs to the network interface via netsh.
        """
        if self.is_running():
            self._log("SNMPSim is already running.")
            return True

        if not device_ips:
            self._log("ERROR: No device IPs supplied — nothing to simulate.")
            self._set_status("Error: no IPs")
            return False

        snmpsim_path = self._find_snmpsim()
        if not snmpsim_path:
            self._log("ERROR: snmpsim not found. Install with:  pip install snmpsim-lextudio")
            self._set_status("Error: snmpsim not found")
            return False

        if not os.path.isdir(self.datasets_dir):
            self._log(f"ERROR: Datasets directory not found: {self.datasets_dir}")
            self._set_status("Error: no datasets directory")
            return False

        cmd = self._build_command(snmpsim_path, device_ips, port)
        self._log(f"Starting SNMPSim with {len(device_ips)} device(s) on port {port}")
        self._log(f"  Executable: {snmpsim_path}")
        self._log(f"  Data dir:   {self.datasets_dir}")
        self._log(f"  Listening:  0.0.0.0:{port}  (community string routes each request to its device)")
        self._log(f"  Devices:    {device_ips[0]} … {device_ips[-1]}  ({len(device_ips)} total)")

        try:
            # PYTHONUNBUFFERED=1 forces SNMPSim (a Python process) to flush
            # stdout after every line.  Without it, subprocess pipe buffering
            # holds output until the buffer fills (~8 KB) — on fast hardware
            # with pre-built indexes the buffer never fills during a scan so
            # per-request SNMP logs appear only in a burst at the end.
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._running = True
            self._ready = False
            self._active_endpoints = [f"{ip}:{port}" for ip in device_ips]
            self._set_status("Starting (building indexes)…")
            self._start_monitor()
            return True
        except Exception as e:
            self._log(f"ERROR starting SNMPSim: {e}")
            self._set_status(f"Error: {e}")
            return False

    def _build_command(self, snmpsim_path: str, device_ips: List[str], port: int) -> List[str]:
        # Use a single wildcard endpoint instead of one flag per device IP.
        # Passing hundreds of --agent-udpv4-endpoint flags would exceed the
        # Windows 32 KB command-line limit (WinError 206).
        # Routing still works because snmpsim matches requests to .snmprec
        # files via the SNMP community string, which is set to the device IP
        # in every generated dataset (e.g. community "10.1.1.1" →
        # datasets/10.1.1.1.snmprec).  The netsh-bound virtual IPs ensure
        # that traffic for each device IP reaches this process.
        base_cmd = (
            [sys.executable, snmpsim_path]
            if snmpsim_path.endswith(".py")
            else [snmpsim_path]
        )

        return base_cmd + [
            f"--data-dir={self.datasets_dir}",
            "--log-level=info",
            f"--agent-udpv4-endpoint=0.0.0.0:{port}",
        ]

    # ------------------------------------------------------------------ #
    #  Stop                                                                #
    # ------------------------------------------------------------------ #

    def stop(self):
        if self._process and self._process.poll() is None:
            self._log("Stopping SNMPSim...")
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            except Exception as e:
                self._log(f"Error stopping SNMPSim: {e}")
        self._running = False
        self._ready = False
        self._process = None
        self._active_endpoints = []
        self._set_status("Stopped")
        self._log("SNMPSim stopped.")

    # ------------------------------------------------------------------ #
    #  Log monitor thread                                                  #
    # ------------------------------------------------------------------ #

    def _start_monitor(self):
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        if not self._process:
            return
        try:
            for line in self._process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self._log(f"[snmpsim] {line}")
                # Detect when snmpsim finishes indexing and is actually listening
                if not self._ready and "Listening at UDP/IPv4 endpoint" in line:
                    self._ready = True
                    self._set_status("Running")
                    if self._ready_callback:
                        self._ready_callback()
            self._process.wait()
            if self._running:
                self._set_status("Stopped unexpectedly")
                self._log("SNMPSim process ended unexpectedly.")
            self._running = False
            self._ready = False
        except Exception as e:
            self._log(f"Monitor error: {e}")

    # ------------------------------------------------------------------ #
    #  Index pre-building                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _snmpsim_cache_dir() -> Optional[str]:
        """Return the snmpsim index cache directory, or None if unavailable."""
        try:
            from snmpsim import confdir
            return confdir.cache
        except ImportError:
            return None

    def clear_index_cache(self) -> int:
        """
        Delete all cached .dbm index files from the snmpsim temp directory.
        Returns the number of files removed.
        Stale indexes accumulate when topologies change; wiping before a fresh
        pre-build keeps the cache lean.
        """
        cache = self._snmpsim_cache_dir()
        if not cache or not os.path.isdir(cache):
            return 0

        removed = 0
        for entry in os.scandir(cache):
            if entry.is_file() and entry.name.endswith((".dat", ".dir", ".bak")):
                try:
                    os.remove(entry.path)
                    removed += 1
                except OSError:
                    pass
        return removed

    def preindex_datasets(self,
                          data_dir: str,
                          progress_cb: Optional[Callable[[int, int], None]] = None,
                          workers: int = 8) -> int:
        """
        Wipe stale index cache, then pre-build snmpsim .dbm indexes for every
        .snmprec file in *data_dir* in parallel so snmpsim starts instantly.

        Uses a fast single-pass writer that produces dbm.dumb-compatible files
        without the O(n²) per-write overhead of dbm.dumb itself.

        Returns the number of files indexed.
        """
        try:
            from snmpsim import confdir as _confdir
        except ImportError:
            return 0  # snmpsim not installed

        # Wipe ALL stale indexes first so the cache never accumulates garbage
        # from previous topology configurations.
        self.clear_index_cache()

        snmprec_files = [
            str(p) for p in Path(data_dir).glob("*.snmprec")
        ]
        if not snmprec_files:
            return 0

        cache_dir = _confdir.cache
        os.makedirs(cache_dir, exist_ok=True)

        total = len(snmprec_files)
        completed = 0
        lock = threading.Lock()

        def _db_path(snmprec_path: str) -> str:
            """Replicate snmpsim's index path computation."""
            p = snmprec_path[: snmprec_path.rindex(".")]  # strip extension
            p += ".dbm"
            p = os.path.splitdrive(p)[1].replace(os.sep, "_")
            return os.path.join(cache_dir, p)

        def _index_one(snmprec_path: str) -> None:
            """
            Parse one .snmprec file and write dbm.dumb-compatible index files
            (.dat, .dir) in a single pass — O(n) instead of O(n²).

            dbm.dumb format (matches CPython's dbm/dumb.py exactly):
              .dat  – raw UTF-8 value bytes, concatenated (no alignment padding)
              .dir  – text lines: "%r, %r\\n" % (key_str, (pos, siz))
                      where key_str is the OID as a Latin-1 string (no b'' prefix)
                      First char must be ' or " for whichdb() to recognise format.
            """
            db_base = _db_path(snmprec_path)
            dat_path = db_base + ".dat"
            dir_path = db_base + ".dir"

            # Parse the .snmprec file to build the index in one pass.
            # Format: OID|TYPE|VALUE\n
            entries: list = []     # [(key_bytes, value_bytes)]
            offset = 0
            prev_offset = -1

            try:
                with open(snmprec_path, "rb") as fh:
                    for raw_line in fh:
                        line = raw_line.decode("utf-8", errors="replace")

                        stripped = line.rstrip("\r\n")
                        if not stripped or stripped.startswith("#"):
                            offset += len(raw_line)
                            continue

                        parts = stripped.split("|", 2)
                        if len(parts) < 3:
                            offset += len(raw_line)
                            continue

                        oid, tag, _val = parts
                        is_subtree = 1 if tag.startswith(":") else 0
                        val_str = "%d,%d,%d" % (offset, is_subtree, prev_offset)

                        # Store raw UTF-8 bytes — NOT marshal-encoded.
                        # dbm.dumb.__setitem__ encodes str→utf-8 before writing.
                        key_b = oid.encode("utf-8")
                        val_b = val_str.encode("utf-8")
                        entries.append((key_b, val_b))

                        if is_subtree:
                            prev_offset = offset
                        else:
                            prev_offset = -1

                        offset += len(raw_line)

                # "last" sentinel (matches snmpsim's db["last"] = "offset,0,prev")
                last_val = "%d,%d,%d" % (offset, 0, prev_offset)
                entries.append((b"last", last_val.encode("utf-8")))

            except Exception:
                return  # skip unreadable files

            # Write .dat — raw value bytes concatenated (no block alignment needed
            # for reads; dbm.dumb seeks to pos+reads siz bytes directly).
            # Write .dir — key as Latin-1 string repr so first char is ' or "
            # (required by whichdb() to recognise the file as dbm.dumb).
            dat_io = bytearray()
            dir_lines: list = []

            for key_b, val_b in entries:
                pos = len(dat_io)
                siz = len(val_b)
                dat_io += val_b
                # key decoded to Latin-1 str → repr starts with ' or "
                # tuple repr matches dbm.dumb _addkey() format exactly
                key_str = key_b.decode("latin-1")
                dir_lines.append("%r, %r\n" % (key_str, (pos, siz)))

            try:
                with open(dat_path, "wb") as f:
                    f.write(dat_io)
                with open(dir_path, "w", encoding="latin-1") as f:
                    f.write("".join(dir_lines))
                # snmpsim uses os.stat()[8] (integer seconds) for freshness.
                # If both files land in the same second the index looks stale.
                # Force .dat mtime to snmprec_mtime + 1 so the check always passes.
                snmprec_sec = int(os.stat(snmprec_path)[8])
                future = snmprec_sec + 1
                os.utime(dat_path, (future, future))
                os.utime(dir_path, (future, future))
            except Exception:
                pass  # skip files we can't write

        # Emit at most ~100 progress callbacks regardless of file count.
        # One callback per file (e.g. 1344 files) floods the main thread's
        # event queue and causes backing-store paint collisions on Windows.
        step = max(1, total // 100)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_index_one, f): f for f in snmprec_files}
            for _ in as_completed(futures):
                with lock:
                    completed += 1
                    if progress_cb and (completed % step == 0 or completed == total):
                        progress_cb(completed, total)

        return completed

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    def get_snmp_walk_command(self, device_ip: str = "127.0.0.1",
                              port: int = 161,
                              community: str = "public") -> str:
        return f"snmpwalk -v2c -c {community} {device_ip}:{port} 1.3.6.1.2.1.1"

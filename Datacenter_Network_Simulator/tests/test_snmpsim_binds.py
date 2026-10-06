"""snmpsim says "Listening" for endpoints it never bound.

pysnmp schedules each bind as an asyncio future, so an EADDRINUSE goes
unread: the process logs "Listening at UDP/IPv4 endpoint ..." for every
endpoint, keeps running, and holds nothing. On 2026-10-06 that left 178 SNMPv3
agents dark while the status said ready with 894 endpoints. The controller now
checks each process's sockets against what it was told to serve, the way
`ss -lnup` would.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from simulator.snmpsim_controller import (SNMPSimController, _endpoints_in_cmd,
                                          _udp_endpoints_by_pid)

linux = pytest.mark.skipif(not sys.platform.startswith("linux"),
                           reason="per-process sockets are read from /proc")


def _holder(port: int) -> subprocess.Popen:
    """A process holding 127.0.0.1:port, as an orphaned shard would."""
    p = subprocess.Popen([sys.executable, "-c",
                          "import socket,time,sys;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                          f"s.bind(('127.0.0.1',{port}));print('up',flush=True);time.sleep(60)"],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "up"
    return p


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _controller(procs) -> SNMPSimController:
    c = SNMPSimController.__new__(SNMPSimController)
    c._procs = procs
    c._report_cache = (0.0, None)
    return c


def test_endpoints_come_from_the_command_and_its_args_file(tmp_path: Path):
    f = tmp_path / "args.txt"
    f.write_text("--v3-engine-id=auto\n--agent-udpv4-endpoint=10.0.0.2:161\n"
                 "--agent-udpv4-endpoint=10.0.0.3:161\n")
    eps = _endpoints_in_cmd(["snmpsim", f"--args-from-file={f}",
                             "--agent-udpv4-endpoint=0.0.0.0:161"])
    assert eps == {"10.0.0.2:161", "10.0.0.3:161", "0.0.0.0:161"}


@linux
def test_a_process_is_credited_only_with_the_sockets_it_holds():
    port = _free_port()
    p = _holder(port)
    try:
        held = _udp_endpoints_by_pid([p.pid])
        assert held is not None and f"127.0.0.1:{port}" in held[p.pid]
        rep = _controller([("v3-0", p, {f"127.0.0.1:{port}", "127.0.0.1:1"})]).binding_report()
        assert rep["verifiable"]
        assert rep["expected"] == 2 and rep["bound"] == 1 and rep["unbound"] == 1
        assert rep["unbound_sample"] == ["127.0.0.1:1"]
    finally:
        p.kill()
        p.wait()


@linux
def test_a_dead_process_is_down_and_serves_nothing():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    rep = _controller([("v2c-1", p, {"127.0.0.1:161"})]).binding_report()
    assert rep["processes_down"] == ["v2c-1"] and rep["unbound"] == 1


@linux
def test_snmpsim_says_listening_while_holding_nothing(tmp_path: Path):
    """The failure itself, with the real snmpsim: an address already held
    elsewhere. snmpsim keeps running and logs "Listening"; only the socket
    check tells the truth."""
    exe = shutil.which("snmpsim-command-responder") or str(
        Path(sys.executable).with_name("snmpsim-command-responder"))
    if not Path(exe).exists():
        pytest.skip("snmpsim not installed here")
    port = _free_port()
    holder = _holder(port)
    data = tmp_path / "data"
    data.mkdir()
    (data / "public.snmprec").write_text("1.3.6.1.2.1.1.1.0|4|test\n")
    cmd = [exe, f"--data-dir={data}", f"--cache-dir={tmp_path / 'cache'}",
           f"--agent-udpv4-endpoint=127.0.0.1:{port}",
           "--log-level=info"]
    if hasattr(__import__("os"), "getuid") and __import__("os").getuid() == 0:
        cmd += ["--process-user=root", "--process-group=root"]
    sim = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        said = ""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and "Listening at UDP/IPv4" not in said:
            line = sim.stdout.readline()
            if not line:
                break
            said += line
        time.sleep(1)
        assert "Listening at UDP/IPv4" in said, said[-500:]
        assert sim.poll() is None, "snmpsim exited - the failure is no longer silent upstream"
        rep = _controller([("primary", sim, {f"127.0.0.1:{port}"})]).binding_report()
        assert rep["unbound"] == 1 and rep["unbound_sample"] == [f"127.0.0.1:{port}"]
    finally:
        sim.kill()
        sim.wait()
        holder.kill()
        holder.wait()

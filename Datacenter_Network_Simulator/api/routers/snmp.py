"""SNMP Simulator REST endpoints."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.routers._bind_guard import require_bound
from api.models.schemas import (
    TrapReceiverRequest,
    SnmpStartRequest,
    SnmpStatusResponse,
    JobResponse,
    JobStatusResponse,
    OkResponse,
)

router = APIRouter(prefix="/snmp", tags=["SNMP Simulator"])

_active_gen_job: Optional[str] = None
_active_start_job: Optional[str] = None


def _state() -> AppState:
    return AppState.get()


@router.post("/datasets/generate", response_model=JobResponse)
def generate_datasets():
    """
    Generate SNMP .snmprec datasets for all devices in the topology.
    Returns job_id — poll /snmp/jobs/{job_id} for progress.
    """
    global _active_gen_job
    s = _state()
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.snmpsim and s.snmpsim.is_running():
        raise HTTPException(status_code=409, detail="SNMP simulator is running — stop it first")

    devices = s.topology.get_all_devices()
    if not devices:
        raise HTTPException(status_code=400, detail="No devices in topology")

    job_id = s.create_job("generate_snmp_datasets")
    _active_gen_job = job_id
    total = len(devices)
    s.notify_ui("snmp_gen_started", total)

    def _run():
        try:
            from core.snmprec_generator import SNMPRecGenerator
            gen = SNMPRecGenerator(s.snmp_datasets_dir)
            files = []
            # Emit one log per 10 devices (not every device) to avoid flooding SSE
            log_step = max(10, total // 50)
            step = max(1, total // 100)
            s.notify_ui("status", "Generating…")
            s.notify_ui("log", f"Generating {total} SNMP datasets…", "info")
            for i, device in enumerate(devices):
                fp = gen.generate_device(device, s.topology)
                # None = this type has no SNMP agent (chiller/pump/valve/... are
                # BACnet/Modbus). Keeping it would inflate the count and index a
                # file that does not exist.
                if fp:
                    files.append(fp)
                if (i + 1) % log_step == 0 or i == total - 1:
                    s.notify_ui(
                        "log",
                        f"[SNMP] Generated {i+1}/{total}  ({device.device_type.value})",
                        "info",
                    )
                if (i + 1) % step == 0 or i == total - 1:
                    s.update_job(job_id, progress_done=i + 1, progress_total=total,
                                 message=f"Generated {i+1}/{total} datasets")
                    s.notify_ui("snmp_progress", i + 1, total)

            s.notify_ui("log", f"[SNMP] {len(files)} .snmprec files generated", "success")

            # Drop datasets from retired topologies BEFORE indexing: snmpsim serves
            # any file in this directory (community -> <ip>.snmprec), so an orphan
            # is a live agent answering for a device that no longer exists.
            reaped = gen.reap_orphans(s.topology)
            if reaped:
                s.notify_ui("log",
                            f"[SNMP] Removed {len(reaped)} orphaned dataset(s) from "
                            f"a previous topology", "info")

            # Stamp WHICH topology these datasets came from, after the reap so the
            # fingerprint describes the directory as it finally stands. Without it
            # a later reconcile can only see that the filenames are all present —
            # never that their contents match the topology now loaded.
            fp = gen.write_fingerprint(s.topology)
            s.notify_ui("log", f"[SNMP] Datasets fingerprinted for this topology ({fp})", "info")

            # Pre-build .dbm indexes
            if s.snmpsim:
                s.update_job(job_id, message="Building indexes...")
                s.notify_ui("status", "Building indexes…")
                s.notify_ui("log", "Pre-building SNMP indexes…", "info")
                s.notify_ui("snmp_progress", 0, len(files))

                def _idx_progress(c, t):
                    s.update_job(job_id, message=f"Indexing {c}/{t}")
                    s.notify_ui("snmp_progress", c, t)

                count = s.snmpsim.preindex_datasets(
                    s.snmp_datasets_dir,
                    progress_cb=_idx_progress,
                )
                s.notify_ui("log", f"Indexes built for {count} datasets — simulator will start instantly.", "success")

            s.generated_snmp_files = files
            s.notify_ui("status", "Datasets ready")
            s.notify_ui("sync_snmp")
            s.update_job(
                job_id,
                status="completed",
                progress_done=total,
                progress_total=total,
                message=f"Generated {len(files)} datasets",
                result={"file_count": len(files)},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.notify_ui("status", "Error")
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="generate_snmp_datasets", status="running")


@router.post("/start", response_model=JobResponse)
def start_snmp_simulator(req: SnmpStartRequest = None):
    """
    Start the SNMP simulator on already-bound agent IPs.

    Does NOT bind addresses — that is the Binding panel's job (see _bind_guard).
    Returns job_id for progress tracking.
    """
    global _active_start_job
    s = _state()

    if s.snmpsim and s.snmpsim.is_running():
        raise HTTPException(status_code=409, detail="SNMP simulator already running")
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if not s.generated_snmp_files:
        raise HTTPException(status_code=400, detail="No datasets generated — call POST /snmp/datasets/generate first")

    # One endpoint per agent: NOS/OS agent IP plus, for servers, the BMC SNMP
    # agent on the mgmt IP (same address Redfish binds). snmp_bind_ips() is the
    # authority — it returns [] for types that have no SNMP agent at all.
    from core.snmprec_generator import SNMPRecGenerator as _Gen
    seen: set = set()
    device_ips = []
    for d in s.topology.get_all_devices():
        for ip in _Gen.snmp_bind_ips(d):
            if ip not in seen:
                seen.add(ip)
                device_ips.append(ip)
    if not device_ips:
        raise HTTPException(status_code=400, detail="No SNMP-capable devices in topology")

    require_bound(s, device_ips, "SNMP agent IP(s)")

    snmp_port = (req.port if req else None) or 161
    # Unlike the other three, SNMP's default port IS privileged — snmpsim cannot
    # open 161 without root. Gate on the port actually requested, not on the fact
    # that this is SNMP: 1611 is a normal choice here and needs no elevation.
    if snmp_port < 1024:
        from core.ip_binder import is_admin
        if not is_admin():
            raise HTTPException(
                status_code=403,
                detail=f"Port {snmp_port} is privileged — Administrator/root required "
                       f"to bind it, or choose a port above 1023 (e.g. 1611).")

    job_id = s.create_job("start_snmp_simulator")
    _active_start_job = job_id

    def _run():
        try:
            s.update_job(job_id, message="Starting SNMP simulator...")
            ok = s.snmpsim.start(device_ips, port=snmp_port)
            if not ok:
                s.update_job(job_id, status="failed", error="snmpsim.start() returned False",
                             finished_at=datetime.utcnow().isoformat())
                return

            if s.state_store:
                s.state_store.start()
                s.state_store.enable_snmp_sync(s.snmpsim)

            # Start SNMP SET management agent
            mgmt_port = (req.mgmt_port if req else None) or 1161
            if s.snmp_set_agent is None or not s.snmp_set_agent.is_running():
                from core.snmp_set_agent import SnmpSetAgent

                def _lookup(ip):
                    if s.device_manager is None:
                        return None
                    for d in s.device_manager.get_all_devices():
                        if d.ip_address == ip or getattr(d, "mgmt_ip", "") == ip:
                            return d
                    return None

                def _on_updated(device):
                    try:
                        from core.snmprec_generator import SNMPRecGenerator
                        if s.topology:
                            SNMPRecGenerator(s.snmp_datasets_dir).generate_device(device, s.topology)
                    except Exception:
                        pass

                agent = SnmpSetAgent(
                    s.rule_engine,
                    port=mgmt_port,
                    device_lookup=_lookup,
                    on_device_updated=_on_updated,
                )
                agent.start()
                s.snmp_set_agent = agent

            s.notify_ui("sync_snmp")
            s.notify_ui("sync_binding")
            s.update_job(
                job_id,
                status="completed",
                message="SNMP simulator started",
                result={"endpoints": s.snmpsim.get_active_endpoints()},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.notify_ui("sync_snmp")
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="start_snmp_simulator", status="running")


@router.post("/reload", response_model=JobResponse)
def reload_snmp_simulator():
    """Hot-reload the SNMP simulator so newly-added devices (e.g. from the Fleet
    Lifecycle churn) become pollable.

    snmpsim indexes its data-dir at process start, so a device that appeared after
    SNMP started isn't served until the process restarts. This binds any not-yet-
    bound device IPs, then bounces the snmpsim subprocess with the *current* full
    device-IP list — it re-reads the data-dir and serves every .snmprec, including
    the new ones. Existing agents are briefly unavailable during the bounce."""
    s = _state()
    if not (s.snmpsim and s.snmpsim.is_running()):
        raise HTTPException(status_code=409, detail="SNMP simulator is not running")
    job_id = s.create_job("reload_snmp_simulator")

    def _run():
        try:
            ok = s.reload_snmp(log_cb=lambda m: s.update_job(job_id, message=m))
            if not ok:
                s.update_job(job_id, status="failed",
                             error="SNMP reload skipped or snmpsim.start() failed",
                             finished_at=datetime.utcnow().isoformat())
                return
            s.update_job(
                job_id, status="completed",
                message="SNMP reloaded",
                result={"endpoints": s.snmpsim.get_active_endpoints()},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.notify_ui("sync_snmp")
            s.update_job(job_id, status="failed", error=str(e),
                         finished_at=datetime.utcnow().isoformat())

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="reload_snmp_simulator", status="running")


@router.post("/stop", response_model=OkResponse)
def stop_snmp_simulator():
    """Stop the SNMP simulator."""
    s = _state()
    if s.snmpsim is None:
        raise HTTPException(status_code=503, detail="SNMP simulator not initialized")
    if not s.snmpsim.is_running():
        return OkResponse(message="SNMP simulator was not running")
    if s.state_store:
        s.state_store.disable_snmp_sync()
    s.snmpsim.stop()
    if s.snmp_set_agent and s.snmp_set_agent.is_running():
        s.snmp_set_agent.stop()
        s.snmp_set_agent = None
    # Rule engine stays enabled (always on); stopping SNMP no longer disables it.
    s.stop_ticker_if_idle()
    s.notify_ui("sync_snmp")
    s.notify_ui("sync_binding")
    s.notify_ui("sync_rules")
    return OkResponse(message="SNMP simulator stopped")


@router.post("/clear", response_model=JobResponse)
def clear_snmp_simulation():
    """Stop simulator and delete all SNMP dataset files."""
    s = _state()
    if s.snmpsim and s.snmpsim.is_running():
        if s.state_store:
            s.state_store.disable_snmp_sync()
        s.snmpsim.stop()
        s.stop_ticker_if_idle()

    job_id = s.create_job("clear_snmp_datasets")

    def _run():
        try:
            from core.snmprec_generator import SNMPRecGenerator
            ds_path = Path(s.snmp_datasets_dir)
            if ds_path.exists():
                for child in ds_path.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    # The fingerprint sidecar is a DOTFILE, so Path.suffix is ""
                    # and the extension list below never matched it — clearing the
                    # datasets left behind a stamp claiming an empty directory was
                    # built from some topology. Harmless only because the
                    # completeness check runs first; delete it with what it describes.
                    elif (child.name == SNMPRecGenerator.FINGERPRINT_FILE
                            or child.suffix in (".snmprec", ".dbm", ".dat", ".dir")):
                        child.unlink(missing_ok=True)
            s.generated_snmp_files = []
            if s.rule_engine:
                s.rule_engine.reset_fired_counts()
            s.notify_ui("sync_snmp")
            s.notify_ui("sync_rules")
            s.update_job(
                job_id,
                status="completed",
                message="SNMP datasets cleared",
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="clear_snmp_datasets", status="running")


@router.get("/status", response_model=SnmpStatusResponse)
async def get_snmp_status():
    """Get SNMP simulator status including running state, endpoints, and active job."""
    s = _state()
    snmpsim = s.snmpsim

    active_job = None
    for job_id in [_active_gen_job, _active_start_job]:
        if job_id:
            j = s.get_job(job_id)
            if j and j.status == "running":
                active_job = job_id
                break

    return SnmpStatusResponse(
        running=snmpsim.is_running() if snmpsim else False,
        ready=snmpsim.is_ready() if snmpsim else False,
        pid=snmpsim.get_pid() if snmpsim else None,
        active_endpoints=snmpsim.get_active_endpoints() if snmpsim else [],
        datasets_generated=bool(s.generated_snmp_files),
        dataset_count=len(s.generated_snmp_files),
        trap_receiver_ip=s.trap_receiver_ip,
        trap_receiver_port=s.trap_receiver_port,
        rule_engine_enabled=s.rule_engine_enabled,
        autonomous_faults=getattr(s.state_store, "autonomous_faults", False) if s.state_store else False,
        active_job_id=active_job,
    )


@router.post("/trap-receiver", response_model=OkResponse)
def set_trap_receiver(req: TrapReceiverRequest):
    """Configure the SNMP trap receiver IP and port."""
    s = _state()
    s.trap_receiver_ip = req.ip
    s.trap_receiver_port = req.port
    if s.trap_engine:
        s.trap_engine.configure(req.ip, req.port)
    s.notify_ui("sync_rules")
    return OkResponse(message=f"Trap receiver set to {req.ip}:{req.port}")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Poll progress of a SNMP async operation."""
    s = _state()
    job = s.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.job_id,
        operation=job.operation,
        status=job.status,
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        message=job.message,
        error=job.error,
        result=job.result,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
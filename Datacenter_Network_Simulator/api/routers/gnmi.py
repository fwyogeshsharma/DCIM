"""gNMI Simulator REST endpoints."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    GnmiStartRequest,
    GnmiStatusResponse,
    JobResponse,
    JobStatusResponse,
    OkResponse,
)

router = APIRouter(prefix="/gnmi", tags=["gNMI Simulator"])

_active_gen_job: Optional[str] = None
_active_start_job: Optional[str] = None


def _state() -> AppState:
    return AppState.get()


@router.post("/datasets/generate", response_model=JobResponse)
def generate_gnmi_datasets():
    """
    Generate gNMI OpenConfig JSON datasets for all switch/router devices.
    Returns job_id — poll /gnmi/jobs/{job_id} for progress.
    """
    global _active_gen_job
    s = _state()
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.gnmi and s.gnmi.is_running():
        raise HTTPException(status_code=409, detail="gNMI simulator is running — stop it first")

    from core.device_manager import DeviceType
    devices = [
        d for d in s.topology.get_all_devices()
        if d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)
    ]
    if not devices:
        raise HTTPException(status_code=400, detail="No switch/router devices in topology")

    job_id = s.create_job("generate_gnmi_datasets")
    _active_gen_job = job_id
    total = len(devices)

    def _run():
        try:
            from core.gnmi_data_generator import GNMIDataGenerator
            gen = GNMIDataGenerator(s.gnmi_datasets_dir)
            files = []
            step = max(1, total // 100)
            s.notify_ui("gnmi_gen_started", total)
            s.notify_ui("console_log", f"[gNMI] Generating datasets for {total} devices…", "info")
            for i, device in enumerate(devices):
                fp = gen.generate_device(device, s.topology)
                if fp:
                    files.append(fp)
                if (i + 1) % step == 0 or i == total - 1:
                    s.update_job(job_id, progress_done=i + 1, progress_total=total,
                                 message=f"Generated {i+1}/{total} gNMI datasets")
                    s.notify_ui("gnmi_gen_progress", i + 1, total)

            s.generated_gnmi_files = files
            # Stamp WHICH topology these came from, so a restart can adopt them
            # instead of rebuilding — and can tell "complete" from "current".
            gfp = gen.write_fingerprint(s.topology)
            s.notify_ui("console_log",
                        f"[gNMI] {len(files)} datasets generated, fingerprinted ({gfp}).",
                        "success")
            s.notify_ui("sync_gnmi")
            s.update_job(
                job_id,
                status="completed",
                progress_done=total,
                progress_total=total,
                message=f"Generated {len(files)} gNMI datasets",
                result={"file_count": len(files)},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.notify_ui("console_log", f"[gNMI] Generation failed: {e}", "error")
            s.notify_ui("sync_gnmi")
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.executor.submit(_run)
    return JobResponse(job_id=job_id, operation="generate_gnmi_datasets", status="running")


@router.post("/start", response_model=JobResponse)
def start_gnmi_simulator(req: GnmiStartRequest = None):
    """
    Bind gNMI device IPs and start the gNMI gRPC server.
    Returns job_id for progress tracking.
    """
    global _active_start_job
    s = _state()

    if s.gnmi and s.gnmi.is_running():
        raise HTTPException(status_code=409, detail="gNMI simulator already running")
    if not s.generated_gnmi_files:
        raise HTTPException(status_code=400, detail="No gNMI datasets — call POST /gnmi/datasets/generate first")
    if not s.selected_adapter:
        raise HTTPException(status_code=400, detail="No adapter selected — call POST /binding/adapter first")

    from core.ip_binder import is_admin
    if not is_admin():
        raise HTTPException(status_code=403, detail="Administrator/root privileges required")

    job_id = s.create_job("start_gnmi_simulator")
    _active_start_job = job_id

    def _run():
        try:
            # Bind gNMI IPs (same set as SNMP if not already bound separately)
            if not s.gnmi_bound_ips:
                from core.ip_binder import add_ips_fast
                ips = s.get_all_bind_ips()
                s.update_job(job_id, message=f"Binding {len(ips)} gNMI IPs...")
                bound, contexts = add_ips_fast(
                    s.selected_adapter, ips, s.subnet_mask,
                    log_cb=lambda msg, lvl: s.update_job(job_id, message=msg),
                    progress_cb=lambda c, t: s.update_job(
                        job_id, progress_done=c, progress_total=t),
                )
                s.gnmi_bound_ips = bound
                s.gnmi_nte_contexts = contexts

            from core.device_manager import DeviceType
            devices = [
                d for d in s.topology.get_all_devices()
                if d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)
            ]
            device_ips = [d.ip_address for d in devices]
            # Dataset files keyed by mgmt_ip (or ip_address when no mgmt_ip);
            # build bound_ip_ports with the same key so load_device() finds them.
            # Use the requested port for all per-device servers so the API port
            # parameter is the single source of truth (same as the desktop UI).
            gnmi_port = (req.port if req else None) or 50051
            bound_ip_ports = {
                (d.mgmt_ip or d.ip_address): gnmi_port
                for d in devices
                if (d.mgmt_ip or d.ip_address) in s.gnmi_bound_ips
            }

            s.update_job(job_id, message="Starting gNMI server...")
            ok = s.gnmi.start(device_ips, port=gnmi_port,
                              bound_ip_ports=bound_ip_ports if bound_ip_ports else None)
            if not ok:
                s.update_job(job_id, status="failed", error="gnmi.start() returned False",
                             finished_at=datetime.utcnow().isoformat())
                return

            s.start_ticker_if_needed()
            s.notify_ui("sync_gnmi")
            s.notify_ui("sync_binding")
            s.update_job(
                job_id,
                status="completed",
                message="gNMI simulator started",
                result={"targets": s.gnmi.get_active_targets()},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.notify_ui("sync_gnmi")
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.executor.submit(_run)
    return JobResponse(job_id=job_id, operation="start_gnmi_simulator", status="running")


@router.post("/stop", response_model=OkResponse)
def stop_gnmi_simulator():
    """Stop the gNMI simulator and proxy."""
    s = _state()
    if s.gnmi is None:
        raise HTTPException(status_code=503, detail="gNMI not initialized")
    if not s.gnmi.is_running():
        return OkResponse(message="gNMI simulator was not running")
    s.gnmi.stop()
    s.stop_ticker_if_idle()
    s.notify_ui("sync_gnmi")
    s.notify_ui("sync_binding")
    return OkResponse(message="gNMI simulator stopped")


@router.post("/clear", response_model=JobResponse)
def clear_gnmi_simulation():
    """Stop gNMI simulator and delete all gNMI dataset files."""
    s = _state()
    if s.gnmi and s.gnmi.is_running():
        s.gnmi.stop()

    job_id = s.create_job("clear_gnmi_datasets")

    def _run():
        try:
            from core.gnmi_data_generator import GNMIDataGenerator
            ds_path = Path(s.gnmi_datasets_dir)
            if ds_path.exists():
                for child in ds_path.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    # The fingerprint sidecar is a dotfile, so Path.suffix is ""
                    # and the extension list never matches it — take it with the
                    # datasets it describes, or an emptied directory keeps a stamp.
                    elif (child.name == GNMIDataGenerator.FINGERPRINT_FILE
                            or child.suffix in (".json", ".gnmi")):
                        child.unlink(missing_ok=True)
            s.generated_gnmi_files = []
            s.notify_ui("sync_gnmi")
            s.update_job(
                job_id,
                status="completed",
                message="gNMI datasets cleared",
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.executor.submit(_run)
    return JobResponse(job_id=job_id, operation="clear_gnmi_datasets", status="running")


@router.get("/status", response_model=GnmiStatusResponse)
async def get_gnmi_status():
    """Get gNMI simulator status."""
    s = _state()
    gnmi = s.gnmi

    active_job = None
    for jid in [_active_gen_job, _active_start_job]:
        if jid:
            j = s.get_job(jid)
            if j and j.status == "running":
                active_job = jid
                break

    clients: list = []
    if gnmi and gnmi.is_running():
        try:
            clients = gnmi.get_clients() or []
        except Exception:
            clients = []

    return GnmiStatusResponse(
        running=gnmi.is_running() if gnmi else False,
        ready=gnmi.is_ready() if gnmi else False,
        proxy_running=gnmi.is_proxy_running() if gnmi else False,
        port=gnmi.get_port() if gnmi else None,
        active_targets=gnmi.get_active_targets() if gnmi else [],
        datasets_generated=bool(s.generated_gnmi_files),
        dataset_count=len(s.generated_gnmi_files),
        active_job_id=active_job,
        clients=clients,
    )


@router.post("/proxy/start", response_model=OkResponse)
def start_gnmi_proxy(req: GnmiStartRequest = None):
    """Start the gNMI aggregating proxy server."""
    s = _state()
    if s.gnmi is None:
        raise HTTPException(status_code=503, detail="gNMI not initialized")
    if s.gnmi.is_proxy_running():
        return OkResponse(message="Proxy already running")
    proxy_port = (req.port if req else None) or 50051
    ok = s.gnmi.start_proxy(port=proxy_port)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to start gNMI proxy")
    s.notify_ui("sync_gnmi")
    return OkResponse(message=f"gNMI proxy started on port {proxy_port}")


@router.post("/proxy/stop", response_model=OkResponse)
def stop_gnmi_proxy():
    """Stop the gNMI aggregating proxy server."""
    s = _state()
    if s.gnmi is None:
        raise HTTPException(status_code=503, detail="gNMI not initialized")
    s.gnmi.stop_proxy()
    s.notify_ui("sync_gnmi")
    return OkResponse(message="gNMI proxy stopped")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Poll progress of a gNMI async operation."""
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
        started_at=job.started_at,
        finished_at=job.finished_at,
    )

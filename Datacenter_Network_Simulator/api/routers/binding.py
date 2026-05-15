"""IP Binding REST endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    AdapterSelectRequest,
    SubnetMaskRequest,
    BindingStatusResponse,
    AdaptersResponse,
    JobResponse,
    JobStatusResponse,
    OkResponse,
)

router = APIRouter(prefix="/binding", tags=["IP Binding"])

# Track active binding/unbinding job IDs
_active_bind_job: Optional[str] = None
_active_unbind_job: Optional[str] = None


def _state() -> AppState:
    return AppState.get()


@router.get("/adapters", response_model=AdaptersResponse)
def list_adapters():
    """List available network adapters. 'adapters' contains adapter names for use with
    POST /binding/adapter. 'adapter_labels' pairs each name with its display label."""
    try:
        from core.ip_binder import get_interfaces
        ifaces = get_interfaces()
        adapters = [name for name, _label in ifaces]
        labels = {name: label for name, label in ifaces}
        return AdaptersResponse(adapters=adapters, adapter_labels=labels)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapter", response_model=OkResponse)
def select_adapter(req: AdapterSelectRequest):
    """Select the network adapter to use for IP binding."""
    s = _state()
    s.selected_adapter = req.adapter
    s.notify_ui("sync_binding")
    return OkResponse(message=f"Adapter set to '{req.adapter}'")


@router.post("/subnet-mask", response_model=OkResponse)
def set_subnet_mask(req: SubnetMaskRequest):
    """Set the subnet mask used when binding IPs."""
    s = _state()
    s.subnet_mask = req.mask
    s.notify_ui("sync_binding")
    return OkResponse(message=f"Subnet mask set to {req.mask}")


@router.post("/bind", response_model=JobResponse)
def bind_ips():
    """
    Bind all device IPs to the selected adapter.
    Returns a job_id — poll /binding/jobs/{job_id} for progress.
    """
    global _active_bind_job
    s = _state()

    if not s.selected_adapter:
        raise HTTPException(status_code=400, detail="No adapter selected — call POST /binding/adapter first")
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")

    from core.ip_binder import is_admin
    if not is_admin():
        raise HTTPException(status_code=403, detail="Administrator/root privileges required for IP binding")

    ips = s.get_all_bind_ips()
    if not ips:
        raise HTTPException(status_code=400, detail="No device IPs to bind — load a topology first")

    job_id = s.create_job("bind_ips")
    _active_bind_job = job_id
    s.notify_ui("binding_started")

    def _run():
        try:
            from core.ip_binder import add_ips_fast
            s.update_job(job_id, progress_total=len(ips), message="Binding IPs...")

            def _progress(done, total):
                s.update_job(job_id, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            def _log(msg, level):
                s.update_job(job_id, message=msg)
                s.notify_ui("log", msg, level)

            bound, contexts = add_ips_fast(
                s.selected_adapter, ips, s.subnet_mask,
                log_cb=_log,
                progress_cb=_progress,
            )
            s.bound_ips = bound
            s.nte_contexts = contexts
            s.notify_ui("sync_binding")
            s.update_job(
                job_id,
                status="completed",
                progress_done=len(bound),
                progress_total=len(ips),
                message=f"Bound {len(bound)}/{len(ips)} IPs",
                result={"bound": len(bound), "total": len(ips)},
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.executor.submit(_run)
    return JobResponse(job_id=job_id, operation="bind_ips", status="running")


@router.post("/unbind", response_model=JobResponse)
def unbind_ips():
    """Remove all bound IPs from the adapter."""
    global _active_unbind_job
    s = _state()

    if not s.selected_adapter:
        raise HTTPException(status_code=400, detail="No adapter selected")
    if not s.bound_ips and not s.gnmi_bound_ips:
        return JobResponse(job_id="none", operation="unbind_ips", status="completed")

    job_id = s.create_job("unbind_ips")
    _active_unbind_job = job_id
    s.notify_ui("binding_started")

    all_ips = list(set(s.bound_ips + s.gnmi_bound_ips))
    all_contexts = {**s.nte_contexts, **s.gnmi_nte_contexts}

    def _run():
        try:
            from core.ip_binder import remove_ips_fast
            s.update_job(job_id, progress_total=len(all_ips), message="Removing bindings...")

            def _progress(done, total):
                s.update_job(job_id, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            remove_ips_fast(
                s.selected_adapter, all_ips, all_contexts,
                log_cb=lambda msg, lvl: s.update_job(job_id, message=msg),
                progress_cb=_progress,
            )
            s.bound_ips = []
            s.nte_contexts = {}
            s.gnmi_bound_ips = []
            s.gnmi_nte_contexts = {}
            s.notify_ui("sync_binding")
            s.update_job(
                job_id,
                status="completed",
                progress_done=len(all_ips),
                message=f"Removed {len(all_ips)} bindings",
                finished_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            s.update_job(job_id, status="failed", error=str(e), finished_at=datetime.utcnow().isoformat())

    s.executor.submit(_run)
    return JobResponse(job_id=job_id, operation="unbind_ips", status="running")


@router.get("/count")
def get_bound_count():
    """Get total number of currently bound IPs."""
    s = _state()
    return {
        "snmp_bound": len(s.bound_ips),
        "gnmi_bound": len(s.gnmi_bound_ips),
        "total": len(set(s.bound_ips + s.gnmi_bound_ips)),
    }


@router.get("/status", response_model=BindingStatusResponse)
def get_binding_status():
    """Full binding status — adapter, mask, bound IPs, active job."""
    s = _state()
    active = None
    if _active_bind_job:
        j = s.get_job(_active_bind_job)
        if j and j.status == "running":
            active = _active_bind_job
    return BindingStatusResponse(
        selected_adapter=s.selected_adapter,
        subnet_mask=s.subnet_mask,
        bound_count=len(s.bound_ips),
        bound_ips=s.bound_ips,
        gnmi_bound_count=len(s.gnmi_bound_ips),
        gnmi_bound_ips=s.gnmi_bound_ips,
        active_job_id=active,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Poll job status for async binding/unbinding operations."""
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
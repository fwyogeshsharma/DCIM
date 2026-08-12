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
            total_ips = len(ips)
            s.update_job(job_id, progress_total=total_ips, message="Binding IPs...")

            def _progress(done, total):
                s.update_job(job_id, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            def _log(msg, lvl):
                s.notify_ui("log", msg, lvl)

            s.notify_ui("log", f"Binding {total_ips} IPs to {s.selected_adapter}...", "info")
            bound, contexts = add_ips_fast(
                s.selected_adapter, ips, s.subnet_mask,
                log_cb=_log,
                progress_cb=_progress,
                workers=8,
            )
            s.notify_ui("log", f"Bound {len(bound)}/{total_ips} IPs", "success")
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

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="bind_ips", status="running")


@router.post("/unbind", response_model=JobResponse)
def unbind_ips():
    """Remove all bound IPs from the adapter."""
    global _active_unbind_job
    s = _state()

    if not s.selected_adapter:
        raise HTTPException(status_code=400, detail="No adapter selected")
    if not s.bound_ips:
        return JobResponse(job_id="none", operation="unbind_ips", status="completed")

    job_id = s.create_job("unbind_ips")
    _active_unbind_job = job_id
    s.notify_ui("binding_started")

    all_ips = list(s.bound_ips)
    all_contexts = dict(s.nte_contexts)

    def _run():
        try:
            from core.ip_binder import remove_ips_fast
            total_ips = len(all_ips)
            s.update_job(job_id, progress_total=total_ips, message="Removing bindings...")

            def _progress(done, total):
                s.update_job(job_id, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            def _log(msg, lvl):
                s.notify_ui("log", msg, lvl)

            s.notify_ui("log", f"Removing {total_ips} bindings...", "info")
            remove_ips_fast(
                s.selected_adapter, all_ips, all_contexts,
                log_cb=_log,
                progress_cb=_progress,
            )
            s.notify_ui("log", f"Removed {total_ips} bindings", "info")
            s.bound_ips = []
            s.nte_contexts = {}
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

    s.submit_job(job_id, _run)
    return JobResponse(job_id=job_id, operation="unbind_ips", status="running")


@router.get("/count")
async def get_bound_count():
    """Get total number of currently bound IPs."""
    s = _state()
    return {"total": len(set(s.bound_ips))}


# Address space this app assigns. An alias outside these prefixes was not put on
# the adapter by us — the host's own LAN address, WSL's loopback helper — and the
# orphan reaper must never consider it, whatever adapter it sits on.
MANAGED_PREFIXES = ("10.50.", "10.51.", "10.52.")


def _orphan_ips(s) -> "dict[str, list[str]]":
    """{adapter: [managed addresses no device claims]}, across EVERY adapter.

    The mirror of snmprec_generator.reap_orphans, and it exists for the same
    reason: snmpsim serves any file in its directory, and the host answers on any
    address on any adapter, so a leftover from a previous topology is a live
    endpoint for a device that no longer exists. It survives an unbind because
    /binding/unbind only removes s.bound_ips — what THIS session bound — and a
    device deleted from the topology falls out of that list while its address
    stays on the NIC. That gap is how a retired address keeps answering.

    Deliberately NOT scoped to the selected adapter. Bindings from earlier runs
    outlive the adapter selection that created them: a run that bound the prod
    plane to eth1 leaves those addresses there for good once the operator moves
    on to a dedicated dcim0, and scoping the sweep to today's choice would leave
    them answering forever with nothing able to see them.

    Two guards do the real work, and neither is the adapter list:
      * the address must be inside MANAGED_PREFIXES — this app's own space, so
        the host's LAN address, WSL's loopback helper and any third-party
        addressing are structurally out of reach;
      * no device in the loaded topology may claim it.
    Loopback is skipped outright: get_interfaces() already excludes it, and a
    simulator address has no business being there in the first place.
    """
    from core.ip_binder import get_interfaces, get_interface_ips

    if s.device_manager is None:
        return {}
    claimed = set()
    for d in s.device_manager.get_all_devices():
        for ip in (getattr(d, "ip_address", ""), getattr(d, "mgmt_ip", "")):
            if ip:
                claimed.add(ip)

    out: "dict[str, list[str]]" = {}
    # apply_filter=False: an adapter DCIM_ADAPTER_FILTER hides is still an
    # adapter whose stranded addresses answer.
    for name, _label in get_interfaces(apply_filter=False):
        if name == "lo":
            continue
        managed = {ip for ip in get_interface_ips(name)
                   if ip.startswith(MANAGED_PREFIXES)}
        stranded = sorted(managed - claimed,
                          key=lambda x: [int(o) for o in x.split(".")])
        if stranded:
            out[name] = stranded
    return out


@router.get("/orphans")
def list_orphans():
    """Managed addresses the host still answers on that no device claims."""
    s = _state()
    by_adapter = _orphan_ips(s)
    return {"count": sum(len(v) for v in by_adapter.values()),
            "adapters": by_adapter,
            "selected_adapter": s.selected_adapter}


@router.post("/reap-orphans", response_model=OkResponse)
def reap_orphans(adapter: Optional[str] = None):
    """Remove stranded managed addresses, on every adapter that carries them.

    Pass ?adapter=<name> to confine the sweep to one. Removal is per-adapter
    because that is how the OS deletes an address — an alias belongs to a NIC,
    not to the host.

    Safety is the managed-prefix check plus the topology claim check, not the
    adapter list: nothing outside 10.50/10.51/10.52 is reachable from here, so
    the host's own addressing cannot be touched however wide the sweep goes.
    """
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")

    by_adapter = _orphan_ips(s)
    if adapter:
        by_adapter = {k: v for k, v in by_adapter.items() if k == adapter}
        if not by_adapter:
            return OkResponse(message=f"No orphaned addresses on {adapter}")
    if not by_adapter:
        return OkResponse(message="No orphaned addresses on any adapter")

    from core.ip_binder import remove_ips_fast
    total_removed = total_failed = 0
    parts = []
    for name, ips in by_adapter.items():
        removed, failed = remove_ips_fast(
            name, ips, dict(s.nte_contexts),
            log_cb=lambda msg, lvl="info": s.notify_ui("log", msg, lvl),
        )
        total_removed += removed
        total_failed += failed
        parts.append(f"{removed} from {name}"
                     + (f" ({failed} failed)" if failed else ""))
        s.notify_ui("log", f"Reaped {removed} orphaned address(es) from {name}",
                    "success")

    # Drop them from the tracked list too, in case one was still recorded there.
    gone = {ip for ips in by_adapter.values() for ip in ips}
    s.bound_ips = [ip for ip in s.bound_ips if ip not in gone]
    s.nte_contexts = {k: v for k, v in s.nte_contexts.items() if k not in gone}

    s.notify_ui("sync_binding")
    return OkResponse(message="Removed " + ", ".join(parts))


@router.get("/status", response_model=BindingStatusResponse)
async def get_binding_status():
    """Full binding status — adapter, mask, bound IPs, active job."""
    s = _state()
    active = None
    for jid in (_active_bind_job, _active_unbind_job):
        if jid:
            j = s.get_job(jid)
            if j and j.status == "running":
                active = jid
                break
    return BindingStatusResponse(
        selected_adapter=s.selected_adapter,
        subnet_mask=s.subnet_mask,
        bound_count=len(s.bound_ips),
        bound_ips=s.bound_ips,
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
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
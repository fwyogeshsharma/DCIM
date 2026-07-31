"""General job status endpoint — works across all routers."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.schemas import JobStatusResponse

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    """Poll status of any async job (generate, bind, start, etc.)."""
    from api.state import AppState
    job = AppState.get().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
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

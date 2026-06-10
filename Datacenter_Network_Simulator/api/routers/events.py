"""Server-Sent Events — real-time log/status streaming to web UI."""
from __future__ import annotations

import asyncio
import json
import queue as _queue

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.auth import require_auth_query

router = APIRouter(prefix="/events", tags=["Events"])


@router.get("", summary="SSE stream: log, progress, sync events",
            dependencies=[Depends(require_auth_query)])
async def event_stream():
    """
    Connect as SSE client.  Each message is newline-terminated JSON:
      {type:"log",    tab:"snmp"|"gnmi"|"sflow", msg:str, level:str}
      {type:"progress", operation:str, done:int, total:int}
      {type:"sync",   target:"snmp"|"gnmi"|"binding"|"rules"|"devices"|"topology"}
      {type:"status", msg:str}
      {type:"connected"}
    """
    from api.state import AppState
    s = AppState.get()
    q: _queue.Queue = _queue.Queue(maxsize=5000)
    s.add_sse_client(q)

    async def generate():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            idle_loops = 0
            while True:
                batch = []
                try:
                    while len(batch) < 200:
                        batch.append(q.get_nowait())
                except _queue.Empty:
                    pass
                if batch:
                    idle_loops = 0
                    for evt in batch:
                        yield f"data: {json.dumps(evt)}\n\n"
                    await asyncio.sleep(0.02)
                else:
                    idle_loops += 1
                    if idle_loops >= 25:
                        yield ": ping\n\n"
                        idle_loops = 0
                    await asyncio.sleep(0.1)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            s.remove_sse_client(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

"""Overview, party switching, deployment audit and the background-task pages (incl. SSE)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from depa.workflows import audit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from depa_console.forms import text
from depa_console.state import Console
from depa_console.tasks import Task, TaskContext
from depa_console.web import Form, Me, act, console, flash, redirect, render, start_task, try_call

router = APIRouter()
TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SSE_POLL_S = 0.25
SSE_HEARTBEAT_S = 15.0


def _service_status(c: Console) -> list[dict[str, Any]]:
    clients, error = try_call(c.clients)
    if clients is None:
        return [{"name": "KMS and Contract Service", "error": error}]
    out = []
    for name, client in zip(("KMS", "Contract Service"), clients, strict=True):
        info, error = try_call(client.info)
        if info is None:
            out.append({"name": name, "url": client.base_url, "error": error})
            continue
        out.append(
            {
                "name": name,
                "url": client.base_url,
                "version": info.version,
                "bundle_digest": info.bundle_digest,
                "bundle_approved": info.raw.get("bundle_digest_approved") is True,
                "bootstrapped": info.bootstrapped,
                "keys": [str(k.get("kid")) for k in info.jwks],
            }
        )
    return out


@router.get("/")
def overview(request: Request, me: Me) -> Response:
    c = console(request)
    return render(
        request,
        "overview.html",
        environment=c.environment(),
        services=_service_status(c),
        audit=c.last_audit,
        tasks=c.tasks.recent()[:5],
    )


@router.post("/audit")
def run_audit(request: Request, me: Me) -> Response:
    c = console(request)

    def job(ctx: TaskContext) -> dict[str, Any]:
        kms, cs = c.clients()
        ctx.log("checking deployed bundles, peer pins and the OIDC gateway")
        report = audit(kms, cs)
        lines = [*(f"OK    {i}" for i in report["info"]), *(f"FAIL  {e}" for e in report["errors"])]
        for line in lines:
            ctx.log(line)
        c.record_check(None, not report["errors"], lines)
        return report

    return start_task(request, "Deployment audit", job, back="/")


@router.post("/session/party")
def switch_party(request: Request, me: Me, form: Form) -> Response:
    target = text(form, "party_id", max_len=63)
    return act(request, "/", lambda: f"now acting as {console(request).switch(target).party_id}")


# ---- tasks ---------------------------------------------------------------------------------


def _task(request: Request, task_id: str) -> Task:
    task = console(request).tasks.get(task_id) if TASK_ID_RE.fullmatch(task_id) else None
    if task is None:
        raise HTTPException(404, "no such task (finished tasks are kept for a while only)")
    return task


@router.get("/tasks")
def task_list(request: Request, me: Me) -> Response:
    return render(request, "tasks.html", tasks=console(request).tasks.recent())


@router.get("/tasks/{task_id}")
def task_page(request: Request, task_id: str, me: Me) -> Response:
    task = _task(request, task_id)
    lines = task.all_lines()
    result = json.dumps(task.result, indent=2, sort_keys=True, default=str) if task.result is not None else None
    return render(
        request,
        "task.html",
        task=task,
        lines=lines,
        next_index=lines[-1][0] + 1 if lines else 0,
        result=result,
    )


@router.get("/tasks/{task_id}/status")
def task_status(request: Request, task_id: str, me: Me) -> JSONResponse:
    task = _task(request, task_id)
    items, state = task.lines_since(0)
    return JSONResponse({"id": task.id, "state": state, "error": task.error, "lines": len(items)})


@router.post("/tasks/{task_id}/cancel")
def task_cancel(request: Request, task_id: str, me: Me) -> Response:
    task = _task(request, task_id)
    if not console(request).tasks.cancel(task.id):
        flash(request, "error", "the task has finished or cannot be cancelled")
    return redirect(f"/tasks/{task.id}")


@router.get("/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str) -> StreamingResponse:
    """Server-sent events: ``line`` (id = absolute line index) until ``end``; resumes from Last-Event-ID."""
    task = _task(request, task_id)
    resume = request.headers.get("last-event-id") or request.query_params.get("after") or "-1"
    start = int(resume) + 1 if resume.lstrip("-").isdigit() else 0

    async def stream() -> AsyncIterator[str]:
        index, idle = max(start, 0), 0.0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            items, state = task.lines_since(index)
            for i, line in items:
                yield f"id: {i}\nevent: line\ndata: {json.dumps(line)}\n\n"
                index = i + 1
            if state in ("succeeded", "failed", "cancelled"):
                yield f"event: end\ndata: {json.dumps({'state': state})}\n\n"
                return
            idle = 0.0 if items else idle + SSE_POLL_S
            if idle >= SSE_HEARTBEAT_S:
                idle = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(SSE_POLL_S)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"x-accel-buffering": "no"})

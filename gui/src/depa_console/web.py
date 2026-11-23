"""Shared view plumbing: templates, role gates, flash messages and the post-redirect-get helper."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from depa.errors import DepaError
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from starlette.responses import Response

from depa_console.errors import explain
from depa_console.security import Auth
from depa_console.state import Console, Identity
from depa_console.tasks import Busy, TaskFn

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["roles_label"] = lambda roles: ", ".join(roles) or "no roles"


def _ts(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


templates.env.filters["ts"] = _ts

# (path, label, roles that may see it; empty = everyone)
NAV: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("/", "Overview", ()),
    ("/governance", "Governance", ("dc-member",)),
    ("/datasets", "Datasets", ("tdp",)),
    ("/contracts", "Contracts", ("tdc", "tdp")),
    ("/runs", "Runs", ("tdc", "tdp")),
    ("/scenarios", "Scenarios", ()),
    ("/tasks", "Activity", ()),
)


class NotAllowed(Exception):
    def __init__(self, identity: Identity, what: str) -> None:
        super().__init__(f"{what} is not available to {identity.party_id} (roles: {', '.join(identity.roles)})")


def console(request: Request) -> Console:
    result: Console = request.app.state.console
    return result


def auth(request: Request) -> Auth:
    result: Auth = request.app.state.auth
    return result


def require(*roles: str) -> Any:
    """Dependency: the active identity, if it has one of ``roles``."""

    def dependency(request: Request) -> Identity:
        identity = console(request).identity()
        if not identity.has(*roles):
            raise NotAllowed(identity, request.url.path)
        return identity

    return Depends(dependency)


def current_identity(request: Request) -> Identity:
    return console(request).identity()


Me = Annotated[Identity, Depends(current_identity)]


async def form_data(request: Request) -> FormData:
    return await request.form()  # already parsed (and bounded) by the CSRF guard


Form = Annotated[FormData, Depends(form_data)]


def flash(request: Request, level: str, message: str) -> None:
    auth(request).flash(request.state.sid, level, message)


def render(request: Request, template: str, status: int = 200, **context: Any) -> Response:
    c = console(request)
    identity = c.identity()
    sid = request.state.sid
    path = request.url.path
    nav = [
        {"href": href, "label": label, "current": path == href or (href != "/" and path.startswith(href + "/"))}
        for href, label, roles in NAV
        if not roles or identity.has(*roles)
    ]
    base = {
        "me": identity,
        "held": c.held(),
        "nav": nav,
        "flashes": auth(request).pop_flashes(sid),
        "csrf": auth(request).csrf_token(sid),
        "now": time.time(),
    }
    return templates.TemplateResponse(request, template, {**base, **context}, status_code=status)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def act(request: Request, back: str, fn: Callable[[], str]) -> RedirectResponse:
    """Run a quick signed action; flash its outcome (or the explained SDK error) and redirect."""
    try:
        flash(request, "ok", fn())
    except DepaError as exc:
        flash(request, "error", explain(exc).text)
    return redirect(back)


def start_task(request: Request, title: str, fn: TaskFn, *, back: str, cancellable: bool = True) -> RedirectResponse:
    c = console(request)
    try:
        task = c.tasks.submit(title, fn, party=c.identity().party_id, cancellable=cancellable)
    except Busy as exc:
        flash(request, "error", str(exc))
        return redirect(back)
    return redirect(f"/tasks/{task.id}")


def try_call(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    """``(value, None)`` or ``(None, explained error)``, for optional sections of a page."""
    try:
        return fn(), None
    except DepaError as exc:
        return None, explain(exc).text

"""Access control for a console that can sign as a party.

* **Launch token**: the CLI prints ``/login?token=…`` once; only a browser that presented it gets
  a session cookie (``HttpOnly``, ``SameSite=Strict``). Other local users, other sites and DNS
  rebinding pages therefore cannot drive the console even though it listens on a TCP port.
* **CSRF**: every non-GET request needs ``csrf_token`` (form field or ``X-CSRF-Token``) equal to
  HMAC(secret, session id), a same-origin ``Origin`` (when sent) and a bounded body.
* **Host allowlist** and strict response headers (CSP without inline script or style, no framing,
  no referrer, no caching) for every response, static files included.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "depa_console_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_BODY = 1 << 20
MAX_SESSIONS = 16
MAX_FLASHES = 20

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "cache-control": "no-store",
}


class NotAuthenticated(Exception):
    pass


class CsrfRejected(Exception):
    pass


class Auth:
    """Launch token, in-memory sessions, CSRF tokens and flash messages."""

    def __init__(self, token: str | None = None, *, secure_cookie: bool = False) -> None:
        self.token = token or secrets.token_urlsafe(32)
        self.secure_cookie = secure_cookie
        self._secret = secrets.token_bytes(32)
        self._sessions: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
        self._lock = threading.Lock()

    def login(self, presented: str | None) -> str | None:
        """A new session id if ``presented`` is the launch token."""
        if not presented or not hmac.compare_digest(presented.encode(), self.token.encode()):
            return None
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = []
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        return sid

    def session_id(self, request: Request) -> str | None:
        sid = request.cookies.get(SESSION_COOKIE)
        with self._lock:
            return sid if sid and sid in self._sessions else None

    def csrf_token(self, sid: str) -> str:
        return hmac.new(self._secret, sid.encode(), hashlib.sha256).hexdigest()

    def csrf_ok(self, sid: str, presented: object) -> bool:
        return isinstance(presented, str) and hmac.compare_digest(presented, self.csrf_token(sid))

    def flash(self, sid: str, level: str, message: str) -> None:
        with self._lock:
            queue = self._sessions.get(sid)
            if queue is not None:
                queue.append((level, message))
                del queue[:-MAX_FLASHES]

    def pop_flashes(self, sid: str) -> list[tuple[str, str]]:
        with self._lock:
            queue = self._sessions.get(sid)
            if not queue:
                return []
            out = list(queue)
            queue.clear()
            return out


async def guard(request: Request) -> None:
    """Router dependency: authenticated session for everything; CSRF + origin + size for writes."""
    auth: Auth = request.app.state.auth
    sid = auth.session_id(request)
    if sid is None:
        raise NotAuthenticated
    request.state.sid = sid
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
        raise CsrfRejected("cross-origin request")
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        raise CsrfRejected("cross-site request")
    length = request.headers.get("content-length")
    if length is None or not length.isdigit() or int(length) > MAX_BODY:
        raise CsrfRejected("request body missing a length or too large")
    form = await request.form(max_files=1, max_fields=200)  # one contract upload at most
    if not auth.csrf_ok(sid, form.get("csrf_token") or request.headers.get("x-csrf-token")):
        raise CsrfRejected("missing or invalid CSRF token")


def host_allowed(host_header: str, allowed: Iterable[str]) -> bool:
    if host_header.startswith("["):  # [v6]:port
        host = host_header[1 : host_header.find("]")] if "]" in host_header else ""
    else:
        host = host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header
    hosts = set(allowed)
    return "*" in hosts or host.lower() in hosts


class SecurityMiddleware:
    """Pure ASGI (keeps SSE streaming intact): Host allowlist + security headers on every response."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        extra = [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        if not host_allowed(headers.get("host", ""), self.allowed_hosts):
            body = b"invalid Host header"
            start: dict[str, Any] = {
                "type": "http.response.start",
                "status": 400,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode()), *extra],
            }
            await send(start)
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {k.decode("latin-1").lower() for k, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []), *(h for h in extra if h[0].decode() not in names)]
            await send(message)

        await self.app(scope, receive, send_with_headers)

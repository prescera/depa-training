"""Application factory: routers behind the session/CSRF guard, error pages, static files, security headers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from depa.errors import DepaError
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response

from depa_console import __version__
from depa_console.errors import explain
from depa_console.security import SESSION_COOKIE, Auth, CsrfRejected, NotAuthenticated, SecurityMiddleware, guard
from depa_console.state import Console
from depa_console.views import contracts, core, datasets, governance, runs, scenarios
from depa_console.web import NotAllowed, redirect, render, templates

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def create_app(console: Console, auth: Auth, *, allowed_hosts: Sequence[str] = LOOPBACK_HOSTS) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        console.close()

    app = FastAPI(
        title="DEPA Training console",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.console = console
    app.state.auth = auth
    app.add_middleware(SecurityMiddleware, allowed_hosts=allowed_hosts)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    for module in (core, governance, datasets, contracts, runs, scenarios):
        app.include_router(module.router, dependencies=[Depends(guard)])

    @app.get("/login")
    def login(request: Request) -> Response:
        sid = auth.login(request.query_params.get("token"))
        if sid is None:
            return _bare(request, 401, "Sign-in required", "The link is invalid. Open the URL the console printed.")
        response = redirect("/")
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", secure=auth.secure_cookie, path="/")
        return response

    @app.exception_handler(NotAuthenticated)
    def not_authenticated(request: Request, _: Exception) -> Response:
        return _bare(
            request, 401, "Sign-in required", "Open the sign-in URL printed in the terminal that started the console."
        )

    @app.exception_handler(CsrfRejected)
    def csrf_rejected(request: Request, exc: Exception) -> Response:
        return _bare(request, 403, "Request refused", f"{exc}. Reload the page and try again.")

    @app.exception_handler(NotAllowed)
    def not_allowed(request: Request, exc: Exception) -> Response:
        return render(request, "error.html", 403, title="Not available", detail=str(exc))

    @app.exception_handler(DepaError)
    def depa_error(request: Request, exc: DepaError) -> Response:
        problem = explain(exc)
        return render(request, "error.html", problem.status, title=problem.title, detail=problem.detail)

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException) -> Response:
        if not hasattr(request.state, "sid"):
            return _bare(request, exc.status_code, "Error", str(exc.detail))
        return render(request, "error.html", exc.status_code, title="Error", detail=str(exc.detail))

    @app.exception_handler(RequestValidationError)
    def bad_request(request: Request, _: Exception) -> Response:
        return _bare(request, 400, "Bad request", "The request was malformed.")

    return app


def _bare(request: Request, status: int, title: str, detail: str) -> HTMLResponse:
    """An error page that needs no session (sign-in and CSRF failures)."""
    return templates.TemplateResponse(request, "bare.html", {"title": title, "detail": detail}, status_code=status)

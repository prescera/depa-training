"""Sign-in, CSRF, Host allowlist, security headers and the remote-bind guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN, Stack, csrf, make_app, post
from depa_console.cli import main
from depa_console.security import CSP


def test_sign_in_required(stack: Stack) -> None:
    app, _ = make_app(stack, "tdc")
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/contracts").status_code == 401
        assert client.get("/login?token=wrong").status_code == 401
        assert client.post("/audit", data={}).status_code == 401
        response = client.get(f"/login?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/").status_code == 200


def test_security_headers_everywhere(client_for) -> None:
    client = client_for("tdc")
    for path in ("/", "/static/console.css", "/login?token=x"):
        headers = client.get(path).headers
        assert headers["content-security-policy"] == CSP
        assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
    assert "unsafe-inline" not in CSP and "script-src 'self'" in CSP


def test_host_allowlist(client_for) -> None:
    client = client_for("tdc")
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


def test_csrf_enforced_on_every_write(client_for) -> None:
    client = client_for("dc-member-1")
    token = csrf(client)
    assert client.post("/audit", data={}, follow_redirects=False).status_code == 403
    assert post(client, "/audit", {}, token="0" * 64).status_code == 403
    cross = client.post(
        "/audit", data={"csrf_token": token}, headers={"origin": "https://evil.example"}, follow_redirects=False
    )
    assert cross.status_code == 403
    site = client.post("/audit", data={"csrf_token": token}, headers={"sec-fetch-site": "cross-site"})
    assert site.status_code == 403
    assert client.post("/audit", headers={"x-csrf-token": token}, follow_redirects=False).status_code == 303
    response = post(client, "/audit", {}, token=token)
    assert response.status_code == 303 and response.headers["location"].startswith("/tasks/")


def test_no_key_material_in_pages(stack: Stack, client_for) -> None:
    client = client_for("tdc")
    secret = (stack.tmp / "keys" / "tdc" / "signing.jwk").read_text()
    d = __import__("json").loads(secret)["d"]
    for path in ("/", "/contracts", f"/contracts/{stack.contract_id}", f"/runs/{stack.run_id}"):
        assert d not in client.get(path).text


def test_remote_bind_needs_opt_in() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0"])
    assert "--allow-remote" in str(exc.value)

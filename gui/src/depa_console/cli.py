"""``depa-console``: start the web console for one party (loopback only unless told otherwise)."""

from __future__ import annotations

import argparse
import ipaddress
import logging
import sys
from pathlib import Path

import uvicorn
from depa.errors import DepaError
from depa.profile import depa_home

from depa_console.app import LOOPBACK_HOSTS, create_app
from depa_console.security import Auth
from depa_console.state import Console, Settings


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="depa-console", description="DEPA Training web console (acts as one party).")
    p.add_argument("--home", type=Path, help="Profile directory (default $DEPA_HOME or ~/.depa)")
    p.add_argument("--env", type=Path, dest="env_path", help="Environment file (depa-env.yaml)")
    p.add_argument("--party", help="Party to act as initially (default: profile party or first held)")
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8400, help="Port (default 8400)")
    p.add_argument("--allow-remote", action="store_true", help="Permit a non-loopback --host")
    p.add_argument("--tls-cert", type=Path, help="Serve HTTPS with this certificate (PEM)")
    p.add_argument("--tls-key", type=Path, help="Private key for --tls-cert")
    p.add_argument("--scenarios-root", type=Path, default=Path("scenarios"), help="Directory of scenarios")
    p.add_argument("--work-dir", type=Path, help="Working directory (default <home>/console)")
    p.add_argument("--storage-token-env", help="Env var holding a bearer token for your dataset storage")
    p.add_argument("--output-token-env", help="Env var holding a bearer token for the TDC output storage")
    p.add_argument("--dev-trainer-cmd", help="Local scenario runs only: trainer command (development)")
    p.add_argument("--workers", type=int, default=2, choices=range(1, 9), metavar="1-8", help="Background workers")
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if not is_loopback(args.host) and not args.allow_remote:
        sys.exit(f"refusing to listen on {args.host}: pass --allow-remote to serve beyond this machine")
    if bool(args.tls_cert) != bool(args.tls_key):
        sys.exit("--tls-cert and --tls-key go together")
    settings = Settings(
        home=(args.home or depa_home()).expanduser().resolve(),
        env_path=args.env_path.resolve() if args.env_path else None,
        party=args.party,
        scenarios_root=args.scenarios_root.resolve(),
        work_root=args.work_dir.resolve() if args.work_dir else None,
        storage_token_env=args.storage_token_env,
        output_token_env=args.output_token_env,
        dev_trainer_cmd=args.dev_trainer_cmd,
        workers=args.workers,
    )
    try:
        console = Console(settings)
    except DepaError as exc:
        sys.exit(f"error: {exc}")
    remote = not is_loopback(args.host)
    if remote:
        sys.stderr.write(
            "WARNING: serving beyond loopback. Anyone holding the sign-in link can sign as "
            f"{console.identity().party_id}"
            + ("" if args.tls_cert else ", and without --tls-cert it travels in clear")
            + ".\n"
        )
    auth = Auth(secure_cookie=bool(args.tls_cert))
    app = create_app(console, auth, allowed_hosts=("*",) if remote else LOOPBACK_HOSTS)
    scheme = "https" if args.tls_cert else "http"
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host  # noqa: S104 - display only
    shown = f"[{shown}]" if ":" in shown else shown
    sys.stderr.write(
        f"DEPA console acting as {console.identity().party_id}.\n"
        f"Open: {scheme}://{shown}:{args.port}/login?token={auth.token}\n"
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,  # request lines would carry the sign-in token
        log_level="warning",
        ssl_certfile=str(args.tls_cert) if args.tls_cert else None,
        ssl_keyfile=str(args.tls_key) if args.tls_key else None,
    )

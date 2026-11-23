"""``depa-ccr run``: the container entrypoint (command ``["depa-ccr", "run"]``)."""

from __future__ import annotations

import dataclasses
import os
import signal
import sys
from pathlib import Path

from depa.ccr.agent import Agent
from depa.ccr.config import CcrConfig
from depa.ccr.log import StatusLog
from depa.ccr.trainer import harden_agent_process, resolve_trainer_command
from depa.errors import DepaError


class AgentTerminated(DepaError):
    """SIGTERM (job deletion, deadline, cancellation): unwind so the trainer is killed and /depa wiped."""

    code = "terminated"


def _on_sigterm(signum: int, frame: object) -> None:
    raise AgentTerminated("terminated by signal")


USAGE = "usage: depa-ccr run   (configured through DEPA_* environment variables)\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["--help"], ["-h"], ["run", "--help"], ["run", "-h"]):
        sys.stdout.write(USAGE)
        return 0
    if args != ["run"]:
        sys.stderr.write(USAGE)
        return 2
    harden_agent_process()
    signal.signal(signal.SIGTERM, _on_sigterm)
    level = os.environ.get("DEPA_LOG_LEVEL", "info")
    platform = os.environ.get("DEPA_PLATFORM", "")
    log = StatusLog(
        level if level in ("error", "warn", "info", "debug") else "info", allow_detail=platform == "virtual"
    )
    try:
        config = CcrConfig.from_env()
        if config.dev and os.environ.get("DEPA_WORK_DIR"):
            # Development only: the virtual platform may run unprivileged outside /depa.
            config = dataclasses.replace(config, work_dir=Path(os.environ["DEPA_WORK_DIR"]))
        dev_trainer = os.environ.get("DEPA_TRAINER_CMD") if config.dev else None
        command = resolve_trainer_command(dev_trainer, os.environ.get("DEPA_CCR_TRAINER"))
        agent = Agent(config, log=log, trainer_command=command)
    except DepaError as exc:
        log.emit("agent.failed", "error", stage="config", code=exc.code)
        log.debug_detail("config", str(exc))
        return 2
    return agent.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

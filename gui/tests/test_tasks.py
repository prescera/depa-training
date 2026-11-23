"""Background task registry: lifecycle, error mapping, redaction, cancellation, limits."""

from __future__ import annotations

import threading
import time

import pytest
from depa.errors import ServiceError

from depa_console.tasks import Busy, Task, TaskContext, TaskRegistry


def _wait(task: Task, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not task.finished:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_success_failure_and_redaction() -> None:
    registry = TaskRegistry(workers=2)
    ok = registry.submit("ok", lambda ctx: (ctx.log("step one"), {"answer": 42})[1], party="p")
    _wait(ok)
    assert ok.state == "succeeded" and ok.result == {"answer": 42}
    assert [line for _, line in ok.all_lines()] == ["step one", "done"]

    def fail(ctx: TaskContext) -> None:
        ctx.log("GET https://acct.blob.core.windows.net/c/x?sv=1&sig=SECRETSIG Bearer abc.def.ghi")
        raise ServiceError("upstream down", service="depa-kms", status=503, remote_code="busy")

    bad = registry.submit("bad", fail, party="p")
    _wait(bad)
    assert bad.state == "failed" and bad.error == "KMS error (HTTP 503, busy): upstream down"
    text = "\n".join(line for _, line in bad.all_lines())
    assert "SECRETSIG" not in text and "abc.def.ghi" not in text and "sig=[redacted]" in text

    def crash(ctx: TaskContext) -> None:
        raise RuntimeError("internal detail")

    boom = registry.submit("boom", crash, party="p")
    _wait(boom)
    assert boom.state == "failed" and "internal detail" not in (boom.error or "")
    registry.shutdown()


def test_cancellation_at_log_points() -> None:
    registry = TaskRegistry(workers=1)
    started = threading.Event()

    def loop(ctx: TaskContext) -> None:
        started.set()
        while True:
            ctx.log("working")
            time.sleep(0.01)

    task = registry.submit("loop", loop, party="p")
    assert started.wait(5)
    assert registry.cancel(task.id)
    _wait(task)
    assert task.state == "cancelled"
    assert not registry.cancel(task.id)  # already finished
    registry.shutdown()


def test_queued_task_cancelled_before_start_and_limits() -> None:
    registry = TaskRegistry(workers=1, max_active=2)
    gate = threading.Event()
    first = registry.submit("block", lambda ctx: gate.wait(5) and None, party="p")
    second = registry.submit("queued", lambda ctx: {"ran": True}, party="p")
    with pytest.raises(Busy):
        registry.submit("third", lambda ctx: None, party="p")
    assert registry.cancel(second.id)
    gate.set()
    _wait(first)
    _wait(second)
    assert second.state == "cancelled" and second.result is None
    registry.shutdown()


def test_bounded_lines_and_history() -> None:
    registry = TaskRegistry(workers=1, keep=3, max_lines=10)

    def chatty(ctx: TaskContext) -> None:
        for i in range(25):
            ctx.log(f"line {i}")

    tasks = [registry.submit(f"t{i}", chatty, party="p") for i in range(5)]
    for task in tasks:
        _wait(task)
    lines = tasks[-1].all_lines()
    assert len(lines) == 10 and lines[-1] == (25, "done") and lines[0][0] == 16
    items, state = tasks[-1].lines_since(24)
    assert [i for i, _ in items] == [24, 25] and state == "succeeded"
    registry.submit("one more", lambda ctx: None, party="p")
    assert len(registry.recent()) <= 4
    registry.shutdown()

"""Background task registry: a bounded worker pool, per-task line logs, cooperative cancellation.

Long operations (encrypt + upload + publish, scenario deploy, model fetch + decrypt, ledger
checks, audits) never run in a request. A task function receives a ``TaskContext``; every
``ctx.log`` call is a cancellation point, and SDK operations that accept a cancel event get
``ctx.cancel_event`` so they stop promptly (``DeployCancelled``). Log lines are redacted and
capped, and finished tasks beyond ``keep`` are forgotten, so memory stays bounded.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from depa.errors import DepaError, DeployCancelled

from depa_console.errors import explain, redact

log = logging.getLogger("depa_console")

QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED = "queued", "running", "succeeded", "failed", "cancelled"
FINISHED = frozenset({SUCCEEDED, FAILED, CANCELLED})
MAX_LINE = 2000


class TaskCancelled(Exception):
    """Raised at a cancellation point once cancellation was requested."""


class Busy(Exception):
    """Too many tasks are queued or running."""


@dataclass
class Task:
    id: str
    title: str
    party: str
    cancellable: bool
    max_lines: int
    created_at: float = field(default_factory=time.time)
    state: str = QUEUED
    error: str | None = None
    result: dict[str, Any] | None = None
    finished_at: float | None = None
    _lines: list[str] = field(default_factory=list)
    _first: int = 0  # absolute index of _lines[0] (older lines are dropped beyond max_lines)
    _cancel: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def finished(self) -> bool:
        return self.state in FINISHED

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def emit(self, message: str) -> None:
        line = redact(str(message).rstrip())[:MAX_LINE]
        with self._lock:
            self._lines.append(line)
            overflow = len(self._lines) - self.max_lines
            if overflow > 0:
                del self._lines[:overflow]
                self._first += overflow

    def lines_since(self, index: int) -> tuple[list[tuple[int, str]], str]:
        """Lines with absolute index >= ``index`` and the current state (read atomically)."""
        with self._lock:
            start = max(index, self._first)
            items = [(i, self._lines[i - self._first]) for i in range(start, self._first + len(self._lines))]
            return items, self.state

    def all_lines(self) -> list[tuple[int, str]]:
        return self.lines_since(0)[0]

    def _finish(self, state: str, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._lock:
            self.result, self.error, self.finished_at = result, error, time.time()
            self.state = state


class TaskContext:
    """What a task function sees: ``log`` (a cancellation point), ``checkpoint`` and the cancel event
    (handed to SDK calls that stop on their own, e.g. ``DeployOptions.cancel``)."""

    def __init__(self, task: Task) -> None:
        self._task = task

    @property
    def cancel_event(self) -> threading.Event:
        return self._task._cancel

    def checkpoint(self) -> None:
        if self._task.cancel_requested:
            raise TaskCancelled

    def log(self, message: str) -> None:
        self.checkpoint()
        self._task.emit(message)


TaskFn = Callable[[TaskContext], dict[str, Any] | None]


class TaskRegistry:
    def __init__(self, *, workers: int = 2, max_active: int = 8, keep: int = 100, max_lines: int = 5000) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="depa-console-task")
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._max_active, self._keep, self._max_lines = max_active, keep, max_lines

    def submit(self, title: str, fn: TaskFn, *, party: str, cancellable: bool = True) -> Task:
        task = Task(uuid.uuid4().hex, title, party, cancellable, self._max_lines)
        with self._lock:
            if sum(not t.finished for t in self._tasks.values()) >= self._max_active:
                raise Busy(f"{self._max_active} tasks are already queued or running; wait for one to finish")
            self._tasks[task.id] = task
            self._prune()
        self._pool.submit(self._run, task, fn)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def recent(self) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.finished or not task.cancellable:
            return False
        task._cancel.set()
        task.emit("cancellation requested; stopping at the next safe point")
        return True

    def shutdown(self) -> None:
        for task in self.recent():
            if not task.finished:
                task._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _prune(self) -> None:
        done = sorted((t for t in self._tasks.values() if t.finished), key=lambda t: t.created_at)
        for task in done[: max(0, len(self._tasks) - self._keep)]:
            del self._tasks[task.id]

    @staticmethod
    def _run(task: Task, fn: TaskFn) -> None:
        if task.cancel_requested:
            task._finish(CANCELLED)
            return
        with task._lock:
            task.state = RUNNING
        try:
            result = fn(TaskContext(task))
        except (TaskCancelled, DeployCancelled):
            task.emit("cancelled")
            task._finish(CANCELLED)
        except DepaError as exc:
            problem = explain(exc)
            task.emit(f"failed: {problem.text}")
            task._finish(FAILED, error=problem.text)
        except Exception:
            log.exception("task %s (%s) failed unexpectedly", task.id, task.title)
            task.emit("failed: unexpected error (details in the console's terminal)")
            task._finish(FAILED, error="Unexpected error; details are in the console's terminal output")
        else:
            task.emit("done")
            task._finish(SUCCEEDED, result=result)

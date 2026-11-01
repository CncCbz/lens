from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from ...models import ModelVerificationRequest, ModelVerificationResult

logger = logging.getLogger(__name__)

# A run takes tens of seconds, far longer than a dev proxy will hold a request
# open, so it is started in the background and polled for.
_RUN_TTL_SECONDS = 3600.0
_MAX_FINISHED_RUNS = 50


@dataclass
class _Run:
    run_id: str
    status: str = "running"
    result: ModelVerificationResult | None = None
    error: str = ""
    task: asyncio.Task[Any] | None = field(default=None, repr=False)


class VerificationRuns:
    """In-memory registry of verification runs.

    Runs are transient: the verdict itself is persisted by the store, so
    losing the registry on restart only costs the in-flight run.
    """

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._lock = asyncio.Lock()

    async def start(self, payload: ModelVerificationRequest) -> str:
        run = _Run(run_id=uuid.uuid4().hex)
        async with self._lock:
            self._runs[run.run_id] = run
            self._prune()
        run.task = asyncio.create_task(self._execute(run, payload))
        return run.run_id

    async def _execute(self, run: _Run, payload: ModelVerificationRequest) -> None:
        from .model_verification import run_verification

        try:
            run.result = await run_verification(payload)
            run.status = "succeeded"
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:
            logger.warning("Verification run %s failed: %s", run.run_id, exc)
            run.error = str(exc) or type(exc).__name__
            run.status = "failed"

    async def get(self, run_id: str) -> _Run | None:
        async with self._lock:
            return self._runs.get(run_id)

    def _prune(self) -> None:
        finished = [
            run_id for run_id, run in self._runs.items() if run.status != "running"
        ]
        if len(finished) <= _MAX_FINISHED_RUNS:
            return
        # Oldest first: dict insertion order tracks start order.
        for run_id in finished[: len(finished) - _MAX_FINISHED_RUNS]:
            self._runs.pop(run_id, None)

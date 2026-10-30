import asyncio
import logging
import random
import time

from .checkers import run_check
from .incidents import IncidentTracker
from .models import Check
from .store import Store

log = logging.getLogger("openedx_status_api")


class Runner:
    def __init__(self, checks: list[Check], store: Store, tracker: IncidentTracker, retention: int = 90, jitter: float = 5.0):
        self.checks, self.store, self.tracker = checks, store, tracker
        self.retention, self.jitter = retention, jitter
        self._tasks: list[asyncio.Task] = []

    def start(self):
        if self.checks:  # an empty list means the file is missing or broken, not that every check was removed
            n = self.store.resolve_orphaned_incidents([c.id for c in self.checks], int(time.time()))
            if n:
                log.info("resolved %d incident(s) for checks no longer configured", n)
        self._tasks = [asyncio.create_task(self._loop(c), name=f"check-{c.id}") for c in self.checks]
        self._tasks.append(asyncio.create_task(self._prune_loop(), name="prune"))

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def run_once(self, check: Check):
        result = await run_check(check)
        await asyncio.to_thread(self._persist, check, result)

    def _persist(self, check: Check, result):
        self.store.record(check.id, result)
        self.tracker.observe(check, result)

    async def _loop(self, check: Check):
        await asyncio.sleep(random.uniform(0, min(self.jitter, check.interval)))
        while True:
            started = time.monotonic()
            try:
                await self.run_once(check)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("check %s iteration failed", check.id)
            await asyncio.sleep(max(1.0, check.interval - (time.monotonic() - started)))

    async def _prune_loop(self):
        while True:
            try:
                await asyncio.to_thread(self.store.prune, self.retention)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")
            await asyncio.sleep(3600)

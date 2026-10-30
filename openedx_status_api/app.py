import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, load_checks
from .incidents import IncidentTracker
from .models import Check, Result
from .runner import Runner
from .store import Store

log = logging.getLogger("openedx_status_api")

CACHE_TTL = 5.0  # seconds
ORDER = ["operational", "degraded", "major_outage"]
OVERALL_TEXT = {
    "operational": "All Systems Operational",
    "degraded": "Partial Service Disruption",
    "major_outage": "Major Service Outage",
    "unknown": "Waiting for first check results",
}


def component_status(check: Check, r: Result | None, now: int) -> str:
    if r is None or now - r.ts > max(180, 3 * check.interval):
        return "unknown"
    if r.ok:
        return "operational"
    return "major_outage" if check.critical else "degraded"


def worst(statuses: list[str]) -> str:
    """Worst status; a stale/unknown component counts as degraded unless everything is unknown."""
    known = [s for s in statuses if s != "unknown"]
    if not known:
        return "unknown"
    if len(known) < len(statuses):
        known.append("degraded")
    return max(known, key=ORDER.index)


def uptime(days: list[dict]) -> float | None:
    total = sum(d["total"] for d in days)
    return round(100 * sum(d["ok"] for d in days) / total, 2) if total else None


def build_status(settings: Settings, checks: list[Check], store: Store, now: int | None = None) -> dict:
    now = now or int(time.time())
    names = {c.id: c.name for c in checks}
    groups: dict[str, list[dict]] = {}
    fresh: dict[str, list[str]] = {}  # statuses that count towards the group; never-reported checks wait
    for c in checks:
        r = store.latest(c.id)
        st = component_status(c, r, now)
        if st in ("degraded", "major_outage") and not store.is_failing(c.id, settings.fail_threshold):
            st = "operational"  # a single failed probe must not flip the banner
        # A never-reported check ("unknown" with no result) waits; a stale one still counts (as unknown).
        fresh.setdefault(c.group, [])
        if r is not None:
            fresh[c.group].append(st)
        days = store.days(c.id, settings.retention_days, now)
        groups.setdefault(c.group, []).append({
            "id": c.id, "name": c.name, "status": st,
            "detail": r.detail if r and st != "unknown" else "",
            "latency_ms": r.latency_ms if r and st != "unknown" else None,
            "uptime_90d": uptime(days), "days": days,
        })
    out = [{"name": g, "status": worst(fresh[g]), "components": comps} for g, comps in groups.items()]
    overall = worst([g["status"] for g in out])
    incidents = [
        {"id": i["id"], "title": i["title"], "component": names.get(i["check_id"], i["check_id"]),
         "started": i["started"], "resolved": i["resolved"]}
        for i in store.incidents(now - settings.retention_days * 86400, 20)
    ]
    return {
        "title": settings.title, "generated_at": now,
        "overall": {"status": overall, "text": OVERALL_TEXT[overall]},
        "groups": out, "incidents": incidents,
    }


def create_app(settings: Settings | None = None, run_checks: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs(settings.data_dir, exist_ok=True)
        store = Store(os.path.join(settings.data_dir, "status.db"))
        checks = load_checks(settings.checks_file)
        runner = Runner(checks, store, IncidentTracker(store, settings.fail_threshold), settings.retention_days)
        app.state.store, app.state.checks, app.state.runner = store, checks, runner
        if run_checks:
            runner.start()
        try:
            yield
        finally:
            await runner.stop()
            store.close()

    app = FastAPI(title="openedx-status-api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(Exception)
    async def boom(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": "internal error"}, status_code=500, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    cache: dict = {"at": 0.0, "data": None}
    cache_lock = threading.Lock()

    def cached_status() -> dict:
        with cache_lock:
            if cache["data"] is None or time.monotonic() - cache["at"] >= CACHE_TTL:
                cache["data"] = build_status(settings, app.state.checks, app.state.store)
                cache["at"] = time.monotonic()
            return cache["data"]

    @app.get("/api/status")
    async def status():
        return await asyncio.to_thread(cached_status)

    return app

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from openedx_status_api.app import create_app


def make(settings, yml=None):
    if yml is not None:
        Path(settings.checks_file).write_text(yml)
    return TestClient(create_app(settings, run_checks=False))


def test_healthz_and_404(settings):
    with make(settings) as c:
        assert c.get("/healthz").json() == {"ok": True}
        r = c.get("/nope")
        assert r.status_code == 404 and "Traceback" not in r.text


def test_missing_file_serves_unknown(settings):
    assert not os.path.exists(settings.checks_file)
    with make(settings) as c:
        s = c.get("/api/status").json()
    assert s["overall"] == {"status": "unknown", "text": "Waiting for first check results"}
    assert s["groups"] == [] and s["incidents"] == []


def test_bad_yaml_survives(settings):
    with make(settings, "checks: [oops\n - {") as c:
        r = c.get("/api/status")
    assert r.status_code == 200 and r.json()["groups"] == []


def test_contract_shape_and_no_leaks(settings, monkeypatch):
    monkeypatch.setenv("MYPW", "hunter2-secret")
    yml = """
checks:
  - {id: lms, name: LMS, group: Open edX, kind: http, target: "http://internal-host.example:8000/heartbeat"}
  - {id: db, name: MySQL, group: Databases, kind: mysql, target: "dbhost.internal:3306", params: {user: u, password_env: MYPW}}
"""
    with make(settings, yml) as c:
        from openedx_status_api.models import Result
        import time
        c.app.state.store.record("lms", Result(int(time.time()), True, "HTTP 200", 12))
        r = c.get("/api/status")
        assert r.headers["cache-control"] == "no-store"
        assert c.get("/healthz").headers["cache-control"] == "no-store"
    s = r.json()
    assert set(s) == {"title", "generated_at", "overall", "groups", "incidents"}
    assert s["title"] == "Open edX status"
    assert s["overall"] == {"status": "degraded", "text": "Partial Service Disruption"}
    assert [g["name"] for g in s["groups"]] == ["Open edX", "Databases"]
    comp = s["groups"][0]["components"][0]
    assert set(comp) == {"id", "name", "status", "detail", "latency_ms", "uptime_90d", "days"}
    assert comp["detail"] == "HTTP 200" and comp["latency_ms"] == 12 and comp["uptime_90d"] == 100.0
    assert len(comp["days"]) == 90 and set(comp["days"][0]) == {"day", "ok", "total"}
    db = s["groups"][1]["components"][0]
    assert db["status"] == "unknown" and db["uptime_90d"] is None and db["latency_ms"] is None
    body = json.dumps(s)
    for leak in ("internal-host", "dbhost", "hunter2", "3306", "heartbeat"):
        assert leak not in body

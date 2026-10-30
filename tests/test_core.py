import time

import logging

from openedx_status_api.app import build_status, component_status, uptime, worst
from openedx_status_api.config import Settings, load_checks
from openedx_status_api.incidents import IncidentTracker
from openedx_status_api.models import Check, Result
from openedx_status_api.store import day_of

NOW = int(time.time())


def chk(id="a", **kw):
    return Check(id=id, name=id.upper(), group=kw.pop("group", "G"), kind="tcp", target="x:1", **kw)


def test_component_status_rules():
    c = chk()
    assert component_status(c, None, NOW) == "unknown"
    assert component_status(c, Result(NOW - 181, True, ""), NOW) == "unknown"
    assert component_status(c, Result(NOW - 100, True, ""), NOW) == "operational"
    assert component_status(c, Result(NOW, False, ""), NOW) == "major_outage"
    assert component_status(chk(critical=False), Result(NOW, False, ""), NOW) == "degraded"
    slow = chk(interval=300)
    assert component_status(slow, Result(NOW - 800, True, ""), NOW) == "operational"
    assert component_status(slow, Result(NOW - 901, True, ""), NOW) == "unknown"


def test_worst():
    assert worst(["unknown", "unknown"]) == "unknown"
    assert worst(["unknown", "operational"]) == "degraded"
    assert worst(["unknown", "major_outage"]) == "major_outage"
    assert worst([]) == "unknown"
    assert worst(["operational", "degraded", "major_outage"]) == "major_outage"


def test_rollup_zero_filled_90(store):
    store.record("a", Result(NOW, True, "ok", 5))
    store.record("a", Result(NOW, False, "bad", 5))
    store.record("a", Result(NOW - 86400 * 2, True, "ok", 5))
    d = store.days("a", 90, NOW)
    assert len(d) == 90 and d[-1]["day"] == day_of(NOW)
    assert d[-1] == {"day": day_of(NOW), "ok": 1, "total": 2}
    assert d[0]["total"] == 0
    assert uptime(d) == 66.67
    assert uptime(store.days("none", 90, NOW)) is None


def test_incident_threshold_and_resolve(store):
    t = IncidentTracker(store, 3)
    c = chk()
    for i in range(2):
        t.observe(c, Result(NOW + i, False, ""))
    assert store.open_incident_for("a") is None
    t.observe(c, Result(NOW + 2, False, ""))
    iid = store.open_incident_for("a")
    assert iid
    t.observe(c, Result(NOW + 3, False, ""))
    assert len(store.incidents(0)) == 1
    t.observe(c, Result(NOW + 4, True, ""))
    assert store.open_incident_for("a") is None
    assert store.incidents(0)[0]["resolved"] == NOW + 4
    # success resets the counter
    t.observe(c, Result(NOW + 5, False, ""))
    t.observe(c, Result(NOW + 6, True, ""))
    t.observe(c, Result(NOW + 7, False, ""))
    assert store.open_incident_for("a") is None


def test_incident_started_at_first_failure(store):
    t = IncidentTracker(store, 3)
    c = chk()
    for i in range(3):
        t.observe(c, Result(NOW + i * 10, False, ""))
    assert store.incidents(0)[0]["started"] == NOW


def test_single_failure_does_not_flip_status(store, settings):
    cs = [chk("a")]
    store.record("a", Result(NOW - 2, True, "ok"))
    store.record("a", Result(NOW, False, "bad"))
    assert build_status(settings, cs, store, NOW)["overall"]["status"] == "operational"
    store.record("a", Result(NOW + 1, False, "bad"))
    store.record("a", Result(NOW + 2, False, "bad"))
    assert build_status(settings, cs, store, NOW + 2)["overall"]["status"] == "major_outage"


def test_config_edge_cases(tmp_path, monkeypatch, caplog):
    f = tmp_path / "c.yml"
    f.write_text(
        "checks:\n"
        " - {id: a, kind: tcp, target: 'h:1', critical: 'false'}\n"
        " - {id: b, kind: tcp, target: 'h:1', critical: 'No'}\n"
        " - {id: c, kind: tcp, target: 'no-port'}\n"
        " - {id: d, kind: tcp, target: 'h:1', params: {1: x, 2_env: '', 3: null}}\n"
        " - {id: e, kind: tcp, target: '[::1]:6379'}\n"
    )
    out = {c.id: c for c in load_checks(str(f))}
    assert set(out) == {"a", "b", "d", "e"}
    assert not out["a"].critical and not out["b"].critical and out["d"].critical


def test_settings_bad_numbers_fall_back(monkeypatch, caplog):
    monkeypatch.setenv("STATUS_FAIL_THRESHOLD", "abc")
    monkeypatch.setenv("STATUS_RETENTION_DAYS", "9x")
    with caplog.at_level(logging.WARNING):
        s = Settings.from_env()
    assert (s.fail_threshold, s.retention_days) == (3, 90)
    assert "STATUS_FAIL_THRESHOLD" in caplog.text


def test_prune(store):
    store.record("a", Result(NOW - 10 * 86400, True, ""))
    store.record("a", Result(NOW, True, ""))
    store.prune(90, NOW)
    assert store.latest("a").ts == NOW
    assert sum(d["total"] for d in store.days("a", 90, NOW)) == 2


def test_resolve_orphaned_incidents(store):
    store.open_incident("a", "A down", NOW)
    store.open_incident("gone", "Gone down", NOW)
    assert store.resolve_orphaned_incidents(["a"], NOW + 5) == 1
    assert store.open_incident_for("a") and store.open_incident_for("gone") is None
    assert store.resolve_orphaned_incidents([], NOW + 6) == 1


def test_yaml_loading(tmp_path):
    assert load_checks(str(tmp_path / "missing.yml")) == []
    bad = tmp_path / "bad.yml"
    bad.write_text("checks: [unclosed\n  - {")
    assert load_checks(str(bad)) == []
    odd = tmp_path / "odd.yml"
    odd.write_text("checks:\n - {id: a, kind: tcp, target: 'h:1'}\n - {id: b, kind: nope, target: x}\n - {kind: tcp}\n - {id: a, kind: tcp, target: 'h:2'}\n")
    out = load_checks(str(odd))
    assert [c.id for c in out] == ["a"] and out[0].interval == 60 and out[0].critical and out[0].group == "Services"


def test_status_group_order(store, settings):
    cs = [chk("a", group="Open edX"), chk("b", group="Databases"), chk("c", group="Open edX", critical=False)]
    store.record("a", Result(NOW, True, "ok"))
    for i in range(3):
        store.record("c", Result(NOW - i, False, "bad"))
    s = build_status(settings, cs, store, NOW)
    assert [g["name"] for g in s["groups"]] == ["Open edX", "Databases"]
    assert s["groups"][0]["status"] == "degraded" and s["groups"][1]["status"] == "unknown"
    assert s["overall"]["text"] == "Partial Service Disruption"


def test_never_reported_check_waits_but_stale_one_degrades(store, settings):
    cs = [chk("a"), chk("b")]
    store.record("a", Result(NOW, True, "ok"))
    assert build_status(settings, cs, store, NOW)["groups"][0]["status"] == "operational"  # b has not reported yet
    store.record("b", Result(NOW - 1000, True, "ok"))
    assert build_status(settings, cs, store, NOW)["groups"][0]["status"] == "degraded"  # b went stale

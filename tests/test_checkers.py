import asyncio

import httpx
import pytest

from openedx_status_api.checkers import run_check
from openedx_status_api.models import Check


def mk(kind, target, **kw):
    return Check(id="x", name="X", group="G", kind=kind, target=target, timeout=2, **kw)


async def free_port():
    srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    srv.close()
    await srv.wait_closed()
    return port


async def test_http_ok_and_fail():
    def handler(req):
        return httpx.Response(200 if req.url.path == "/ok" else 503)

    t = httpx.MockTransport(handler)
    r = await run_check(mk("http", "http://h/ok"), t)
    assert r.ok and r.detail == "HTTP 200"
    r = await run_check(mk("http", "http://h/bad"), t)
    assert not r.ok and r.detail == "HTTP 503"
    r = await run_check(mk("http", "http://h/ok", params={"expected_status": 204}), t)
    assert not r.ok


async def test_http_params_from_env(monkeypatch):
    monkeypatch.setenv("EXP", "204")
    t = httpx.MockTransport(lambda req: httpx.Response(204))
    assert (await run_check(mk("http", "http://h/", params={"expected_status_env": "EXP"}), t)).ok
    monkeypatch.setenv("EXP", "200")
    assert not (await run_check(mk("http", "http://h/", params={"expected_status_env": "EXP"}), t)).ok


async def test_http_unreachable():
    r = await run_check(mk("http", f"http://127.0.0.1:{await free_port()}/"))
    assert not r.ok and r.detail == "Connection failed"


async def test_tcp():
    srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        assert (await run_check(mk("tcp", f"127.0.0.1:{port}"))).ok
    assert not (await run_check(mk("tcp", f"127.0.0.1:{port}"))).ok


async def test_redis(monkeypatch):
    monkeypatch.setenv("RPW", "s3cret")
    seen = []

    async def handle(r, w):
        while data := await r.read(1024):
            seen.append(data)
            w.write(b"+OK\r\n" if b"AUTH" in data else b"+PONG\r\n")
            await w.drain()
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        r = await run_check(mk("redis", f"127.0.0.1:{port}", params={"password_env": "RPW"}))
    assert r.ok and r.detail == "PONG" and b"s3cret" in seen[0]
    assert "s3cret" not in r.detail


@pytest.mark.parametrize("kind", ["mysql", "mongo", "redis", "ssl"])
async def test_unreachable_failure_paths(kind):
    r = await run_check(mk(kind, f"127.0.0.1:{await free_port()}"))
    assert not r.ok and r.detail in {"Connection failed", "Check failed", "Timed out"}
    assert r.latency_ms is not None


@pytest.mark.parametrize("target,default,expected", [
    ("db.example:3307", 3306, ("db.example", 3307)),
    ("db.example", 3306, ("db.example", 3306)),
    ("mysql://user:pw@db.example:3307/mydb?x=1", 3306, ("db.example", 3307)),
    ("https://example.org/", 443, ("example.org", 443)),
    ("https://example.org:8443/path/", 443, ("example.org", 8443)),
    ("[::1]:6379", 1, ("::1", 6379)),
    ("redis://[::1]/0", 6379, ("::1", 6379)),
    ("::1", 1, ("::1", 1)),
    ("tcp-host", None, ("tcp-host", None)),
])
def test_hostport(target, default, expected):
    from openedx_status_api.checkers import _hostport

    assert _hostport(target, default) == expected


def test_ssl_expired_cert(monkeypatch):
    import ssl

    from openedx_status_api import checkers

    def boom(*a, **k):
        err = ssl.SSLCertVerificationError(1, "certificate has expired")
        err.verify_code, err.verify_message = 10, "certificate has expired"
        raise err

    monkeypatch.setattr(checkers.socket, "create_connection", boom)
    r = asyncio.run(checkers.run_check(Check(id="s", name="s", group="g", kind="ssl", target="x.example", timeout=2)))
    assert (r.ok, r.detail) == (False, "Certificate expired")

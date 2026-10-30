"""Probes. Each returns Result-like (ok, detail, latency_ms); detail never contains secrets."""
import asyncio
import logging
import os
import socket
import ssl
import time

import httpx

from .models import Check, Result

log = logging.getLogger("openedx_status_api")


def _param(check: Check, key: str, default=None):
    """Read params[key], or the env var named by params[key + '_env']."""
    env = check.params.get(f"{key}_env")
    if env:
        return os.environ.get(str(env), default)
    return check.params.get(key, default)


def _flag(v) -> bool:
    """Bool param; env-sourced values arrive as strings."""
    return v if isinstance(v, bool) else str(v).strip().lower() not in {"0", "false", "no", "off"}


def _hostport(target: str, default_port: int | None) -> tuple[str, int | None]:
    """Split a URL or host[:port] (bracketed IPv6 allowed); the port is default_port when absent."""
    t = target.split("://", 1)[-1]
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1]
    if t.startswith("["):
        host, _, rest = t[1:].partition("]")
        return host, int(rest[1:]) if rest[1:].isdigit() else default_port
    host, sep, port = t.rpartition(":")
    if sep and ":" not in host and port.isdigit():
        return host, int(port)
    return (t.rstrip(":") if t.count(":") == 1 else t), default_port


async def check_http(check: Check, transport=None) -> tuple[bool, str]:
    verify = _flag(_param(check, "verify_tls", True))
    expect = _param(check, "expected_status")
    async with httpx.AsyncClient(timeout=check.timeout, verify=verify, follow_redirects=True, transport=transport) as c:
        r = await c.get(check.target)
    good = r.status_code == int(expect) if expect else r.status_code < 400
    return good, f"HTTP {r.status_code}"


async def check_tcp(check: Check) -> tuple[bool, str]:
    host, port = _hostport(check.target, None)
    if port is None:
        raise ValueError("tcp target needs a port")
    _, w = await asyncio.open_connection(host, port)
    w.close()
    return True, "Port open"


async def check_redis(check: Check) -> tuple[bool, str]:
    host, port = _hostport(check.target, 6379)
    r, w = await asyncio.open_connection(host, port)
    try:
        pw = _param(check, "password")
        if pw:
            w.write(f"*2\r\n$4\r\nAUTH\r\n${len(pw.encode())}\r\n{pw}\r\n".encode())
            await w.drain()
            if not (await r.readline()).startswith(b"+OK"):
                return False, "Auth failed"
        w.write(b"*1\r\n$4\r\nPING\r\n")
        await w.drain()
        line = await r.readline()
        return line.startswith(b"+PONG"), "PONG" if line.startswith(b"+PONG") else "Unexpected reply"
    finally:
        w.close()


def _mysql_sync(check: Check) -> tuple[bool, str]:
    import pymysql

    host, port = _hostport(check.target, 3306)
    conn = pymysql.connect(
        host=host, port=port, user=_param(check, "user", "root"), password=_param(check, "password", "") or "",
        database=_param(check, "database"), connect_timeout=check.timeout,
        read_timeout=check.timeout, write_timeout=check.timeout,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()
    return True, "Query OK"


def _mongo_sync(check: Check) -> tuple[bool, str]:
    from pymongo import MongoClient

    host, port = _hostport(check.target, 27017)
    ms = check.timeout * 1000
    client = MongoClient(
        host=host, port=port, username=_param(check, "username") or None, password=_param(check, "password") or None,
        serverSelectionTimeoutMS=ms, connectTimeoutMS=ms, socketTimeoutMS=ms,
    )
    try:
        client.admin.command("ping")
    finally:
        client.close()
    return True, "Ping OK"


def _ssl_sync(check: Check) -> tuple[bool, str]:
    host, port = _hostport(check.target, 443)
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=check.timeout) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        if exc.verify_code == 10 or "expired" in str(exc.verify_message).lower():  # 10 = CERT_HAS_EXPIRED
            return False, "Certificate expired"
        raise
    days = int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400)
    warn = int(_param(check, "warn_days", 7))
    return days >= warn, f"expires in {days}d" if days >= 0 else "Certificate expired"


def _describe(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return "Timed out"
    if isinstance(exc, ssl.SSLError):
        return "TLS error"
    if isinstance(exc, (ConnectionError, OSError, httpx.TransportError)):
        return "Connection failed"
    if type(exc).__module__.split(".")[0] in {"pymysql", "pymongo"}:
        return "Connection failed"
    return "Check failed"


async def run_check(check: Check, transport=None) -> Result:
    """Run one probe; any exception becomes a failed Result (never raises)."""
    start = time.monotonic()
    try:
        async with asyncio.timeout(check.timeout + 2):
            if check.kind == "http":
                ok, detail = await check_http(check, transport)
            elif check.kind == "tcp":
                ok, detail = await check_tcp(check)
            elif check.kind == "redis":
                ok, detail = await check_redis(check)
            else:
                fn = {"mysql": _mysql_sync, "mongo": _mongo_sync, "ssl": _ssl_sync}[check.kind]
                ok, detail = await asyncio.to_thread(fn, check)
    except Exception as exc:  # noqa: BLE001 - probes must never crash the runner
        log.warning("check %s failed: %s", check.id, type(exc).__name__)
        ok, detail = False, _describe(exc)
    ms = int((time.monotonic() - start) * 1000)
    return Result(ts=int(time.time()), ok=ok, detail=detail, latency_ms=ms)

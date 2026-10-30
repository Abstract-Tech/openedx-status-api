from dataclasses import dataclass, field

KINDS = {"http", "tcp", "mysql", "mongo", "redis", "ssl"}


@dataclass
class Check:
    id: str
    name: str
    group: str
    kind: str
    target: str
    interval: int = 60
    timeout: int = 10
    critical: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class Result:
    ts: int
    ok: bool
    detail: str
    latency_ms: int | None = None

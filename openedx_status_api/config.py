import logging
import os
from dataclasses import dataclass

import yaml

from .checkers import _flag, _hostport
from .models import KINDS, Check

log = logging.getLogger("openedx_status_api")


@dataclass
class Settings:
    title: str = "Open edX status"
    checks_file: str = "/config/checks.yml"
    data_dir: str = "/data"
    fail_threshold: int = 3
    retention_days: int = 90

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get

        def num(name: str, default: int) -> int:
            try:
                return max(1, int(e(name, str(default))))
            except ValueError:
                log.warning("%s is not a number; using %d", name, default)
                return default

        return cls(
            title=e("STATUS_TITLE", "Open edX status"),
            checks_file=e("STATUS_CHECKS_FILE", "/config/checks.yml"),
            data_dir=e("STATUS_DATA_DIR", "/data"),
            fail_threshold=num("STATUS_FAIL_THRESHOLD", 3),
            retention_days=num("STATUS_RETENTION_DAYS", 90),
        )


def load_checks(path: str) -> list[Check]:
    """Parse checks.yml. Never raises: bad file or entries are logged and skipped."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        log.error("checks file %s not found; serving an empty component list", path)
        return []
    except (OSError, yaml.YAMLError) as exc:
        log.error("checks file %s unreadable/invalid (%s); serving an empty list", path, type(exc).__name__)
        return []
    items = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        log.error("checks file %s has no 'checks' list; serving an empty list", path)
        return []
    checks, seen = [], set()
    for raw in items:
        try:
            c = Check(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                group=str(raw.get("group") or "Services"),
                kind=str(raw["kind"]).lower(),
                target=str(raw["target"]),
                interval=max(5, int(raw.get("interval", 60))),
                timeout=max(1, int(raw.get("timeout", 10))),
                critical=_flag(raw.get("critical", True)),
                params=raw.get("params") or {},
            )
            if c.kind not in KINDS or c.id in seen or not isinstance(c.params, dict):
                raise ValueError("bad kind, duplicate id or params")
            if c.kind == "tcp" and _hostport(c.target, None)[1] is None:
                raise ValueError("tcp target needs a port")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.error("skipping invalid check entry %r: %s", str(raw)[:80], type(exc).__name__)
            continue
        for k, v in c.params.items():
            k = str(k)
            if k.endswith("_env") and v and str(v) not in os.environ:
                log.warning("check %s: env var %s (param %s) is not set", c.id, v, k)
        seen.add(c.id)
        checks.append(c)
    log.info("loaded %d checks from %s", len(checks), path)
    return checks

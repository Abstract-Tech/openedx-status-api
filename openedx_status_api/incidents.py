from .models import Check, Result
from .store import Store


class IncidentTracker:
    """Opens an incident after N consecutive failures; closes it on the first success."""

    def __init__(self, store: Store, threshold: int):
        self.store, self.threshold = store, threshold
        self._fails: dict[str, int] = {}
        self._first_fail: dict[str, int] = {}  # ts of the first failure in the current streak

    def restore(self, check: Check):
        n, first = self.store.failure_streak(check.id, self.threshold)
        if n:
            self._fails[check.id], self._first_fail[check.id] = n, first

    def observe(self, check: Check, r: Result):
        open_id = self.store.open_incident_for(check.id)
        if r.ok:
            self._fails[check.id] = 0
            if open_id is not None:
                self.store.resolve_incident(open_id, r.ts)
            return
        n = self._fails[check.id] = self._fails.get(check.id, 0) + 1
        if n == 1:
            self._first_fail[check.id] = r.ts
        if n >= self.threshold and open_id is None:
            self.store.open_incident(check.id, f"{check.name} is unavailable", self._first_fail[check.id])

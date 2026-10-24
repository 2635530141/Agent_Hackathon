from ..intervals import overlaps
from ..schemas import BusyBlock, Horizon, PrivateEvent, ProviderSnapshot


class FakeCalendarProvider:
    """Synthetic provider; has no network or write capability and loads no credentials."""
    def __init__(self, events: dict[str, list[PrivateEvent]]):
        self.events = events
        self.calls = 0
        self.failure_mode: str | None = None

    def read_snapshot(self, owner_id: str, horizon: Horizon) -> ProviderSnapshot:
        self.calls += 1
        if self.failure_mode == "timeout":
            raise TimeoutError("synthetic calendar timeout")
        events = [e for e in self.events.get(owner_id, []) if overlaps(e, horizon)]
        if self.failure_mode == "incomplete":
            return ProviderSnapshot(coverage=horizon, busy=[], complete=False)
        return ProviderSnapshot(coverage=horizon,
            busy=[BusyBlock(id=e.id, start=e.start, end=e.end) for e in events], private_events=events)

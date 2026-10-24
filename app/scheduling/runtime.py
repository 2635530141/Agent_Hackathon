"""Local persistent runtime. Replace repository/auth/session adapters at integration."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from .adapters import TeamSessionGateway
from .core.service import ScheduleService
from .core.storage import SQLiteScheduleRepository
from .demo import seed_repository
from .time import display_zone


@dataclass
class CalendarRuntime:
    repository: SQLiteScheduleRepository
    service: ScheduleService


def build_runtime(database_url=None):
    if os.getenv("STUDYMATE_CALENDAR_MODE", "synthetic") != "synthetic":
        raise RuntimeError("Team authentication is not wired; this host permits synthetic calendar data only")
    default = (Path(__file__).resolve().parents[2] / "data" / "calendar-synthetic.db").as_posix()
    repository = SQLiteScheduleRepository(database_url or os.getenv("STUDYMATE_CALENDAR_DB", f"sqlite+pysqlite:///{default}"))
    repository.migrate()
    _, provider = seed_repository(repository)
    service = ScheduleService(repository, provider, TeamSessionGateway(), timezone_name=display_zone())
    return CalendarRuntime(repository, service)


@lru_cache(maxsize=1)
def _synthetic_runtime():
    return build_runtime()


def get_runtime():
    from .context import live_context
    context = live_context()
    return context.runtime if context else _synthetic_runtime()


get_runtime.cache_clear = _synthetic_runtime.cache_clear
get_runtime.cache_info = _synthetic_runtime.cache_info

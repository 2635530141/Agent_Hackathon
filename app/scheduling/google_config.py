"""M2 config has no import-time credential reads and no write scopes."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from urllib.parse import urlsplit

READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALLBACK = "http://127.0.0.1:8011/calendar/google/callback"


@dataclass(frozen=True)
class GoogleActor:
    """Construct ONLY from the team's verified login session, never browser fields."""
    subject_id: str
    timezone: str
    session_binding: str = field(repr=False)
    csrf_token: str = field(repr=False)

    def __post_init__(self):
        from .core.schemas import Actor
        from zoneinfo import ZoneInfo
        Actor(subject_id=self.subject_id)
        ZoneInfo(self.timezone)
        if not self.session_binding or not self.csrf_token:
            raise ValueError("verified session binding and CSRF token are required")


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = CALLBACK

    def __post_init__(self):
        if not self.client_id or not self.client_secret:
            raise ValueError("Web OAuth client configuration is incomplete")
        url = urlsplit(self.redirect_uri)
        if (url.path != "/calendar/google/callback" or url.query or url.fragment or url.username
            or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1","localhost","::1"}))):
            raise ValueError("exact HTTPS or loopback callback required")

    @classmethod
    def from_file(cls, path, redirect_uri=CALLBACK):
        config = json.loads(Path(path).read_text(encoding="utf-8"))["web"]
        if redirect_uri not in config["redirect_uris"]:
            raise ValueError("callback must be registered exactly in Web OAuth configuration")
        return cls(config["client_id"], config["client_secret"], redirect_uri)

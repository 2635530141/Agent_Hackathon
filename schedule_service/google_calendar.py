"""Small, privacy-preserving Google Calendar adapter.

The application must enforce the consent / accepted-session gate before calling
``create_event``. This adapter never adds attendees, sends invitations, updates
existing events, or writes to a calendar other than the caller's primary one.
BusyBlock titles are owner-only data: public availability responses must redact
them. Credentials are supplied by a server-side token provider, never by a browser.

API references (Google):
https://developers.google.com/workspace/calendar/api/v3/reference/events/list
https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
https://developers.google.com/identity/protocols/oauth2/web-server
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .domain import BusyBlock, Interval


READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
API_BASE = "https://www.googleapis.com/calendar/v3"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_UTC = timezone.utc
_MAX_WINDOW = timedelta(days=14)
_MAX_PAGES = 20


class CalendarError(Exception):
    """Sanitized error: no provider response, credentials, or event text."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "calendar_error",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CalendarWriteUncertain(CalendarError):
    """A write may have happened; preserve the session ID and reconcile later."""

    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__(
            "Calendar write could not be verified. Check its status before trying again.",
            code="write_uncertain",
            status_code=status_code,
        )


@dataclass(frozen=True)
class CalendarReceipt:
    event_id: str
    status: str
    html_link: str | None = None


def calendar_event_id(user_id: str, session_id: str) -> str:
    """Stable Google-compatible ID for exactly one owner's study-session copy."""
    if not isinstance(user_id, str) or not user_id:
        raise CalendarError("An authenticated calendar owner is required.", code="owner_required")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 1024:
        raise CalendarError("A valid study session ID is required.", code="invalid_session")
    identity = json.dumps([user_id, session_id], ensure_ascii=True, separators=(",", ":"))
    return "sm" + hashlib.sha256(identity.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError("Calendar times must include a timezone.", code="invalid_interval")
    return value.astimezone(_UTC)


def _bounds(interval: Interval, *, limit_window: bool = False) -> tuple[datetime, datetime]:
    try:
        start, end = _utc(interval.start), _utc(interval.end)
    except AttributeError:
        raise CalendarError("A valid calendar interval is required.", code="invalid_interval") from None
    if end <= start:
        raise CalendarError("Calendar end must be after start.", code="invalid_interval")
    if limit_window and end - start > _MAX_WINDOW:
        raise CalendarError("Calendar queries are limited to two weeks.", code="window_too_large")
    return start, end


def _rfc3339(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except (ValueError, UnicodeError):
        raise CalendarError("Calendar returned an invalid response.", code="invalid_response") from None
    if not isinstance(value, dict):
        raise CalendarError("Calendar returned an invalid response.", code="invalid_response")
    return value


def _http_error(status: int) -> CalendarError:
    if status in (401, 403):
        return CalendarError("Calendar authorization is required.", code="authorization_required", status_code=status)
    if status == 429:
        return CalendarError("Calendar is rate limited. Try again later.", code="rate_limited", status_code=status)
    return CalendarError("Calendar request failed.", code="provider_error", status_code=status)


def _tz(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not name:
        raise CalendarError("Calendar timezone is missing.", code="invalid_response")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarError("Calendar timezone is invalid.", code="invalid_response") from None


def _event_time(part: Any, calendar_timezone: str | None) -> datetime:
    if not isinstance(part, dict):
        raise CalendarError("Calendar event time is missing.", code="invalid_response")
    if "dateTime" in part:
        raw = part["dateTime"]
        if not isinstance(raw, str) or "T" not in raw or "date" in part:
            raise CalendarError("Calendar event time is invalid.", code="invalid_response")
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise CalendarError("Calendar event time is invalid.", code="invalid_response") from None
        if value.tzinfo is None:
            zone = _tz(part.get("timeZone") or calendar_timezone)
            # A timezone name alone cannot disambiguate a fall-back fold. Both
            # missing instants (spring gap) and two instants (fold) fail closed.
            instants: set[datetime] = set()
            for fold in (0, 1):
                instant = value.replace(tzinfo=zone, fold=fold).astimezone(_UTC)
                if instant.astimezone(zone).replace(tzinfo=None) == value:
                    instants.add(instant)
            if len(instants) != 1:
                raise CalendarError("Calendar event time is ambiguous or invalid.", code="invalid_response")
            value = instants.pop()
        return _utc(value)
    if "date" in part:
        try:
            day = date.fromisoformat(part["date"])
        except (TypeError, ValueError):
            raise CalendarError("Calendar event date is invalid.", code="invalid_response") from None
        return datetime.combine(day, time.min, _tz(part.get("timeZone") or calendar_timezone)).astimezone(_UTC)
    raise CalendarError("Calendar event time is missing.", code="invalid_response")


class GoogleCalendarConnector:
    """Synchronous adapter; inject MockTransport in tests and encrypted tokens in production."""

    def __init__(
        self,
        client: httpx.Client,
        token_provider: Callable[[str, bool], str],
        calendar_id: str = "primary",
    ) -> None:
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ValueError("A calendar ID is required.")
        self.client = client
        self.token_provider = token_provider
        self.calendar_id = calendar_id

    def _headers(self, user_id: str, write: bool) -> dict[str, str]:
        if not isinstance(user_id, str) or not user_id:
            raise CalendarError("An authenticated calendar owner is required.", code="owner_required")
        try:
            token = self.token_provider(user_id, write)
        except Exception:
            raise CalendarError("Calendar credentials are unavailable.", code="credentials_unavailable") from None
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            raise CalendarError("Calendar credentials are unavailable.", code="credentials_unavailable")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    @property
    def _events_url(self) -> str:
        return f"{API_BASE}/calendars/{quote(self.calendar_id, safe='')}/events"

    def fetch_busy(self, user_id: str, window: Interval | None = None) -> list[BusyBlock]:
        """Read owner events, defaulting to the next 14 days; never return partial success."""
        if window is None:
            now = datetime.now(_UTC)
            window = Interval(start=now, end=now + _MAX_WINDOW)
        window_start, window_end = _bounds(window, limit_window=True)
        headers = self._headers(user_id, False)
        params: dict[str, Any] = {
            "timeMin": _rfc3339(window_start),
            "timeMax": _rfc3339(window_end),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 2500,
            "maxAttendees": 1,
            "showDeleted": "false",
            "fields": "timeZone,nextPageToken,items(id,status,summary,transparency,start,end,attendees(self,responseStatus))",
        }
        result: list[BusyBlock] = []
        seen_ids: set[str] = set()
        seen_pages: set[str] = set()
        calendar_timezone: str | None = None
        for _ in range(_MAX_PAGES):
            try:
                response = self.client.get(self._events_url, params=params, headers=headers, follow_redirects=False)
            except httpx.HTTPError:
                raise CalendarError("Calendar could not be read. Existing availability is unchanged.", code="read_failed") from None
            if response.status_code != 200:
                raise _http_error(response.status_code)
            page = _object(response)
            if "timeZone" in page:
                _tz(page["timeZone"])
                calendar_timezone = page["timeZone"]
            items = page.get("items", [])
            if not isinstance(items, list):
                raise CalendarError("Calendar returned invalid events.", code="invalid_response")
            for event in items:
                if not isinstance(event, dict):
                    raise CalendarError("Calendar returned an invalid event.", code="invalid_response")
                if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                    continue
                attendees = event.get("attendees", [])
                if not isinstance(attendees, list) or any(not isinstance(a, dict) for a in attendees):
                    raise CalendarError("Calendar returned invalid attendees.", code="invalid_response")
                if any(a.get("self") is True and a.get("responseStatus") == "declined" for a in attendees):
                    continue
                event_id = event.get("id")
                if not isinstance(event_id, str) or not event_id:
                    raise CalendarError("Calendar event ID is missing.", code="invalid_response")
                start = _event_time(event.get("start"), calendar_timezone)
                end = _event_time(event.get("end"), calendar_timezone)
                if end <= start:
                    raise CalendarError("Calendar event interval is invalid.", code="invalid_response")
                start, end = max(start, window_start), min(end, window_end)
                if end <= start or event_id in seen_ids:
                    continue
                title = event.get("summary", "")
                if not isinstance(title, str):
                    raise CalendarError("Calendar event summary is invalid.", code="invalid_response")
                result.append(BusyBlock(id=event_id, interval=Interval(start=start, end=end), source="google_calendar", title=title))
                seen_ids.add(event_id)
            page_token = page.get("nextPageToken")
            if page_token is None or page_token == "":
                return result
            if not isinstance(page_token, str) or page_token in seen_pages:
                raise CalendarError("Calendar pagination was incomplete.", code="pagination_incomplete")
            seen_pages.add(page_token)
            params["pageToken"] = page_token
        raise CalendarError("Calendar pagination exceeded its limit.", code="pagination_incomplete")

    def create_event(
        self,
        user_id: str,
        session_id: str,
        subject: str,
        interval: Interval,
        place: str,
        payload_version: int = 1,
    ) -> CalendarReceipt:
        """Create the owner's private copy after the application's explicit consent gate.

        Reusing a session ID with changed content is a conflict, not an update.
        Unknown outcomes never generate a replacement event ID or an automatic POST retry.
        """
        if self.calendar_id != "primary":
            raise CalendarError("Only the owner's primary calendar can be written.", code="write_target_forbidden")
        start, end = _bounds(interval)
        if not isinstance(session_id, str) or not session_id or len(session_id) > 1024:
            raise CalendarError("A valid study session ID is required.", code="invalid_session")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 1024:
            raise CalendarError("A valid study subject is required.", code="invalid_subject")
        if not isinstance(place, str) or len(place) > 1024:
            raise CalendarError("A valid study location is required.", code="invalid_place")
        if type(payload_version) is not int or payload_version < 1:
            raise CalendarError("A valid payload version is required.", code="invalid_payload_version")
        headers = self._headers(user_id, True)
        event_id = calendar_event_id(user_id, session_id)
        canonical = {
            "session_id": session_id,
            "subject": subject,
            "start": _rfc3339(start),
            "end": _rfc3339(end),
            "place": place,
            "payload_version": payload_version,
        }
        payload_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        body = {
            "id": event_id,
            "summary": subject,
            "location": place,
            "start": {"dateTime": canonical["start"]},
            "end": {"dateTime": canonical["end"]},
            "visibility": "private",
            "extendedProperties": {"private": {"study_mate_session": session_id, "study_mate_payload_hash": payload_hash, "study_mate_payload_version": str(payload_version)}},
        }
        try:
            response = self.client.post(self._events_url, params={"sendUpdates": "none"}, json=body, headers=headers, follow_redirects=False)
        except httpx.HTTPError:
            # The write might already exist; only read back the stable ID.
            pass
        else:
            if response.status_code not in (200, 201, 409):
                # A provider error can happen after a write was committed.
                if response.status_code < 500:
                    raise _http_error(response.status_code)
        return self._verify_event(event_id, canonical, payload_hash, headers)

    def _verify_event(
        self,
        event_id: str,
        canonical: dict[str, Any],
        payload_hash: str,
        headers: dict[str, str],
    ) -> CalendarReceipt:
        try:
            response = self.client.get(f"{self._events_url}/{event_id}", headers=headers, follow_redirects=False)
        except httpx.HTTPError:
            raise CalendarWriteUncertain() from None
        if response.status_code != 200:
            # Includes 403: inaccessible is not evidence of absence.
            raise CalendarWriteUncertain(status_code=response.status_code)
        try:
            event = _object(response)
            private = event.get("extendedProperties", {}).get("private", {})
            if not isinstance(private, dict):
                raise CalendarError("Calendar event verification failed.", code="payload_conflict")
            if event.get("id") != event_id or private.get("study_mate_session") != canonical["session_id"] or private.get("study_mate_payload_hash") != payload_hash or private.get("study_mate_payload_version") != str(canonical["payload_version"]):
                raise CalendarError("This study session already has different calendar content.", code="payload_conflict")
            actual_start = _event_time(event.get("start"), None)
            actual_end = _event_time(event.get("end"), None)
            if _rfc3339(actual_start) != canonical["start"] or _rfc3339(actual_end) != canonical["end"] or event.get("summary") != canonical["subject"] or event.get("location", "") != canonical["place"]:
                raise CalendarError("This study session already has different calendar content.", code="payload_conflict")
            if event.get("status") != "confirmed":
                raise CalendarError("Calendar event is not confirmed.", code="event_not_confirmed")
            # An attendee list could mean this ID was altered outside our app.
            if event.get("attendees"):
                raise CalendarError("Calendar event contains unexpected guests.", code="payload_conflict")
            link = event.get("htmlLink")
            if link is not None and (not isinstance(link, str) or urlparse(link).scheme != "https" or urlparse(link).hostname not in {"www.google.com", "calendar.google.com"}):
                link = None
            return CalendarReceipt(event_id=event_id, status="confirmed", html_link=link)
        except (AttributeError, TypeError, ValueError):
            raise CalendarWriteUncertain() from None
        except CalendarError as error:
            if error.code == "invalid_response":
                raise CalendarWriteUncertain() from None
            raise


class GoogleOAuthClient:
    """OAuth endpoint client; caller stores tokens encrypted and verifies callback state.

    ``authorization_url`` does not store state or a PKCE verifier. The server must
    bind both to its authenticated session, consume state once, and compare it on
    callback before invoking ``exchange_code``. Never send returned token dicts to
    browsers or application logs.
    """

    def __init__(self, client: httpx.Client, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self.client = client
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @staticmethod
    def _verifier(code_verifier: str) -> str:
        if not isinstance(code_verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
            raise CalendarError("A valid PKCE verifier is required.", code="invalid_pkce")
        return code_verifier

    def authorization_url(self, state: str, write: bool = False, code_verifier: str | None = None) -> str:
        if not isinstance(state, str) or not state:
            raise CalendarError("OAuth state is required.", code="invalid_state")
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": f"{READ_SCOPE} {WRITE_SCOPE}" if write else READ_SCOPE,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "state": state,
        }
        if code_verifier is not None:
            self._verifier(code_verifier)
            params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
            params["code_challenge_method"] = "S256"
        return f"{AUTH_URL}?{urlencode(params)}"

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.client.post(TOKEN_URL, data=data, follow_redirects=False)
        except httpx.HTTPError:
            raise CalendarError("Google authorization could not be completed.", code="oauth_failed") from None
        if response.status_code != 200:
            raise CalendarError("Google authorization failed. Reconnect your calendar.", code="oauth_failed", status_code=response.status_code)
        value = _object(response)
        if not isinstance(value.get("access_token"), str) or not value["access_token"] or str(value.get("token_type", "")).lower() != "bearer":
            raise CalendarError("Google returned invalid credentials.", code="oauth_failed")
        return value

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, Any]:
        if not isinstance(code, str) or not code:
            raise CalendarError("An authorization code is required.", code="oauth_failed")
        data = {"client_id": self.client_id, "client_secret": self.client_secret, "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code": code}
        if code_verifier is not None:
            data["code_verifier"] = self._verifier(code_verifier)
        return self._token_request(data)

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CalendarError("Calendar must be reconnected.", code="oauth_failed")
        return self._token_request({"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "refresh_token", "refresh_token": refresh_token})

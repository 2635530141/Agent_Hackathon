"""Connector tests use MockTransport only; no real Google credentials or requests."""

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from schedule_service.domain import Interval
from schedule_service.google_calendar import (
    CalendarError,
    CalendarWriteUncertain,
    GoogleCalendarConnector,
    GoogleOAuthClient,
    calendar_event_id,
    READ_SCOPE,
    WRITE_SCOPE,
)


UTC = timezone.utc


def interval(start="2026-10-24T10:00:00+00:00", end="2026-10-25T10:00:00+00:00"):
    return Interval(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end))


def event(event_id="one", **extra):
    return {"id": event_id, "status": "confirmed", "summary": "Owner's private lecture", "start": {"dateTime": "2026-10-24T12:00:00Z"}, "end": {"dateTime": "2026-10-24T13:00:00Z"}, **extra}


def connector(handler, token_provider=None, calendar_id="primary"):
    return GoogleCalendarConnector(httpx.Client(transport=httpx.MockTransport(handler)), token_provider or (lambda owner, write: "server-secret"), calendar_id)


def test_reads_all_pages_and_expands_recurring_instances():
    seen = []
    credential_requests = []

    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["orderBy"] == "startTime"
        assert request.url.params["maxResults"] == "2500"
        assert request.url.params["maxAttendees"] == "1"
        assert "attendees(self,responseStatus)" in request.url.params["fields"]
        assert request.headers["authorization"] == "Bearer server-secret"
        assert "server-secret" not in str(request.url)
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"timeZone": "America/Los_Angeles", "items": [event("recurrence_1")], "nextPageToken": "second"})
        assert request.url.params["pageToken"] == "second"
        return httpx.Response(200, json={"items": [event("recurrence_2")]})

    def tokens(owner, write):
        credential_requests.append((owner, write))
        return "server-secret"

    blocks = connector(handler, tokens).fetch_busy("owner", interval())
    assert [block.id for block in blocks] == ["recurrence_1", "recurrence_2"]
    assert blocks[0].title == "Owner's private lecture"
    assert blocks[0].source == "google_calendar"
    assert len(seen) == 2
    assert credential_requests == [("owner", False)]


def test_excludes_cancelled_transparent_and_owner_declined_but_not_other_declines():
    events = [event("cancelled", status="cancelled"), event("free", transparency="transparent"), event("declined", attendees=[{"self": True, "responseStatus": "declined"}]), event("busy", attendees=[{"self": False, "responseStatus": "declined"}])]
    adapter = connector(lambda request: httpx.Response(200, json={"items": events}))
    assert [block.id for block in adapter.fetch_busy("owner", interval())] == ["busy"]


def test_all_day_exclusive_end_uses_calendar_timezone_over_dst_transition():
    day = event("dst", start={"date": "2026-11-01"}, end={"date": "2026-11-02"})
    adapter = connector(lambda request: httpx.Response(200, json={"timeZone": "America/Los_Angeles", "items": [day]}))
    blocks = adapter.fetch_busy("owner", interval("2026-10-31T00:00:00Z", "2026-11-03T00:00:00Z"))
    assert blocks[0].interval.start == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert blocks[0].interval.end == datetime(2026, 11, 2, 8, tzinfo=UTC)
    assert blocks[0].interval.end - blocks[0].interval.start == timedelta(hours=25)


def test_naive_provider_datetime_uses_explicit_timezone_and_dst_gap_is_rejected():
    good = event(start={"dateTime": "2026-10-24T05:00:00", "timeZone": "America/Los_Angeles"})
    adapter = connector(lambda request: httpx.Response(200, json={"items": [good]}))
    assert adapter.fetch_busy("owner", interval())[0].interval.start.hour == 12
    gap = event(start={"dateTime": "2026-03-08T02:30:00", "timeZone": "America/Los_Angeles"}, end={"dateTime": "2026-03-08T04:00:00", "timeZone": "America/Los_Angeles"})
    adapter = connector(lambda request: httpx.Response(200, json={"items": [gap]}))
    with pytest.raises(CalendarError, match="invalid"):
        adapter.fetch_busy("owner", interval("2026-03-08T00:00:00Z", "2026-03-09T00:00:00Z"))


def test_naive_provider_fold_is_rejected_but_explicit_offset_is_accepted():
    fold = event(start={"dateTime": "2026-11-01T01:30:00", "timeZone": "America/Los_Angeles"}, end={"dateTime": "2026-11-01T03:00:00", "timeZone": "America/Los_Angeles"})
    window = interval("2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z")
    adapter = connector(lambda request: httpx.Response(200, json={"items": [fold]}))
    with pytest.raises(CalendarError) as caught:
        adapter.fetch_busy("owner", window)
    assert caught.value.code == "invalid_response"
    fold["start"]["dateTime"] = "2026-11-01T01:30:00-08:00"
    blocks = adapter.fetch_busy("owner", window)
    assert blocks[0].interval.start == datetime(2026, 11, 1, 9, 30, tzinfo=UTC)


def test_busy_intervals_are_clipped_and_nonoverlapping_events_omitted():
    long_event = event("long", start={"dateTime": "2026-10-20T00:00:00Z"}, end={"dateTime": "2026-10-30T00:00:00Z"})
    past_event = event("past", start={"dateTime": "2026-10-20T00:00:00Z"}, end={"dateTime": "2026-10-21T00:00:00Z"})
    adapter = connector(lambda request: httpx.Response(200, json={"items": [long_event, past_event]}))
    window = interval()
    result = adapter.fetch_busy("owner", window)
    assert len(result) == 1
    assert result[0].interval == window


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_read_failures_never_turn_into_empty_calendar(status):
    adapter = connector(lambda request: httpx.Response(status, json={"error": "sensitive provider content"}))
    with pytest.raises(CalendarError) as caught:
        adapter.fetch_busy("owner", interval())
    assert caught.value.status_code == status
    assert "sensitive" not in str(caught.value)


def test_second_page_failure_rejects_partial_result():
    def handler(request):
        if "pageToken" in request.url.params:
            return httpx.Response(503, json={"error": "failure"})
        return httpx.Response(200, json={"items": [event()], "nextPageToken": "next"})

    with pytest.raises(CalendarError):
        connector(handler).fetch_busy("owner", interval())


def test_pagination_cycle_and_page_limit_fail_closed():
    count = 0

    def endless(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"items": [], "nextPageToken": str(count)})

    with pytest.raises(CalendarError) as caught:
        connector(endless).fetch_busy("owner", interval())
    assert caught.value.code == "pagination_incomplete"
    assert count == 20
    with pytest.raises(CalendarError):
        connector(lambda request: httpx.Response(200, json={"items": [], "nextPageToken": "same"})).fetch_busy("owner", interval())


def test_window_bound_before_credentials_or_network():
    def fail(*args):
        pytest.fail("No credentials or HTTP for invalid windows")

    too_long = interval("2026-10-01T00:00:00Z", "2026-10-16T00:00:00Z")
    with pytest.raises(CalendarError) as caught:
        connector(fail, fail).fetch_busy("owner", too_long)
    assert caught.value.code == "window_too_large"
    # Use a simple object to independently test adapter validation, even if the
    # domain correctly refuses to construct a naive Interval in the first place.
    class Naive:
        start = datetime(2026, 10, 24)
        end = datetime(2026, 10, 25)

    with pytest.raises(CalendarError) as caught:
        connector(fail, fail).fetch_busy("owner", Naive())
    assert caught.value.code == "invalid_interval"


def test_default_window_is_fourteen_days():
    def handler(request):
        start = datetime.fromisoformat(request.url.params["timeMin"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["timeMax"].replace("Z", "+00:00"))
        assert end - start == timedelta(days=14)
        return httpx.Response(200, json={"items": []})

    assert connector(handler).fetch_busy("owner") == []


def test_bad_all_day_timezone_or_malformed_event_rejects_complete_sync():
    cases = [event(start={"date": "2026-10-24"}, end={"date": "2026-10-25"}), event(start={"dateTime": "garbage"}), event(end={"dateTime": "2026-10-24T11:00:00Z"})]
    for malformed in cases:
        with pytest.raises(CalendarError):
            connector(lambda request: httpx.Response(200, json={"items": [malformed]})).fetch_busy("owner", interval())


def stored_event(body):
    return {**body, "status": "confirmed", "htmlLink": "https://www.google.com/calendar/event?eid=demo"}


def test_create_requires_write_token_no_attendees_and_verified_receipt():
    requests = []
    tokens = []
    stored = {}

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert request.url.params["sendUpdates"] == "none"
            assert "attendees" not in body
            assert "conferenceData" not in body
            assert re.fullmatch(r"[a-v0-9]{5,1024}", body["id"])
            assert body["visibility"] == "private"
            stored.update(stored_event(body))
            return httpx.Response(201, json=stored)
        assert request.url.path.endswith("/" + stored["id"])
        return httpx.Response(200, json=stored)

    def token_provider(owner, write):
        tokens.append((owner, write))
        return "secret"

    receipt = connector(handler, token_provider).create_event("owner", "accepted-session", "STAT 153", interval(), "Moffitt")
    assert receipt.status == "confirmed"
    assert receipt.event_id == stored["id"]
    assert receipt.event_id == calendar_event_id("owner", "accepted-session")
    assert receipt.html_link.startswith("https://www.google.com/")
    assert [request.method for request in requests] == ["POST", "GET"]
    assert tokens == [("owner", True)]


def test_calendar_event_id_is_stable_and_scoped_to_both_owner_and_session():
    value = calendar_event_id("owner", "session")
    assert value == calendar_event_id("owner", "session")
    assert re.fullmatch(r"[a-v0-9]{5,1024}", value)
    assert value != calendar_event_id("another-owner", "session")
    assert value != calendar_event_id("owner", "another-session")
    assert calendar_event_id("a:b", "c") != calendar_event_id("a", "b:c")
    with pytest.raises(CalendarError):
        calendar_event_id("", "session")
    with pytest.raises(CalendarError):
        calendar_event_id("owner", "")


def test_duplicate_write_uses_same_id_and_returns_existing_receipt():
    stored = {}
    posted_ids = []

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            posted_ids.append(body["id"])
            if stored:
                return httpx.Response(409, json={"error": "duplicate"})
            stored.update(stored_event(body))
            return httpx.Response(201, json=stored)
        return httpx.Response(200, json=stored)

    adapter = connector(handler)
    first = adapter.create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    second = adapter.create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    assert first == second
    assert len(set(posted_ids)) == 1


def test_existing_session_with_changed_payload_is_explicit_conflict():
    stored = {}

    def handler(request):
        if request.method == "POST":
            if stored:
                return httpx.Response(409)
            stored.update(stored_event(json.loads(request.content)))
            return httpx.Response(201, json=stored)
        return httpx.Response(200, json=stored)

    adapter = connector(handler)
    adapter.create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    with pytest.raises(CalendarError) as caught:
        adapter.create_event("owner", "session", "STAT 151A", interval(), "Moffitt")
    assert caught.value.code == "payload_conflict"


def test_post_timeout_reconciles_one_get_without_second_post():
    stored = {}
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            stored.update(stored_event(json.loads(request.content)))
            raise httpx.ReadTimeout("secret must not appear", request=request)
        return httpx.Response(200, json=stored)

    receipt = connector(handler).create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    assert receipt.status == "confirmed"
    assert methods == ["POST", "GET"]


@pytest.mark.parametrize("get_status", [403, 404, 503])
def test_timeout_then_unknown_write_never_claims_success_or_absence(get_status):
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("server-secret private title", request=request)
        return httpx.Response(get_status, json={"error": "server-secret private title"})

    with pytest.raises(CalendarWriteUncertain) as caught:
        connector(handler).create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    assert caught.value.status_code == get_status
    assert "server-secret" not in str(caught.value)
    assert "private title" not in str(caught.value)
    assert methods == ["POST", "GET"]


@pytest.mark.parametrize("change", ["time", "hash", "attendee", "status"])
def test_read_back_verification_checks_content_and_guests(change):
    stored = {}

    def handler(request):
        if request.method == "POST":
            stored.update(stored_event(json.loads(request.content)))
            return httpx.Response(201, json=stored)
        if change == "time":
            stored["end"] = {"dateTime": "2026-10-25T11:00:00Z"}
        elif change == "hash":
            stored["extendedProperties"]["private"]["study_mate_payload_hash"] = "different"
        elif change == "attendee":
            stored["attendees"] = [{"email": "person@example.com"}]
        else:
            stored["status"] = "cancelled"
        return httpx.Response(200, json=stored)

    with pytest.raises(CalendarError):
        connector(handler).create_event("owner", "session", "STAT 153", interval(), "Moffitt")


def test_only_primary_can_be_written():
    def fail(*args):
        pytest.fail("Must not get credentials or send HTTP")

    with pytest.raises(CalendarError) as caught:
        connector(fail, fail, "other@example.com").create_event("owner", "session", "STAT 153", interval(), "Moffitt")
    assert caught.value.code == "write_target_forbidden"


def test_error_strings_do_not_leak_tokens_or_provider_body(caplog):
    def handler(request):
        raise httpx.ConnectError("server-secret private title", request=request)

    with pytest.raises(CalendarError) as caught:
        connector(handler).fetch_busy("owner", interval())
    assert "server-secret" not in str(caught.value)
    assert "private title" not in str(caught.value)
    assert "server-secret" not in caplog.text

    def credential_failure(*args):
        raise ValueError("refresh-token-value")

    with pytest.raises(CalendarError) as caught:
        connector(handler, credential_failure).fetch_busy("owner", interval())
    assert "refresh-token-value" not in str(caught.value)


def oauth(handler=lambda request: httpx.Response(200, json={"access_token": "new-secret", "token_type": "Bearer"})):
    return GoogleOAuthClient(httpx.Client(transport=httpx.MockTransport(handler)), "client-id", "client-secret", "http://localhost:8000/oauth/callback")


def test_oauth_minimum_scopes_incremental_write_and_pkce():
    client = oauth()
    verifier = "v" * 64
    normal = parse_qs(urlparse(client.authorization_url("state", code_verifier=verifier)).query)
    assert normal["scope"] == [READ_SCOPE]
    assert normal["access_type"] == ["offline"]
    assert normal["include_granted_scopes"] == ["true"]
    assert normal["state"] == ["state"]
    assert normal["code_challenge_method"] == ["S256"]
    assert verifier not in client.authorization_url("state", code_verifier=verifier)
    upgraded = parse_qs(urlparse(client.authorization_url("state", write=True)).query)
    assert upgraded["scope"] == [f"{READ_SCOPE} {WRITE_SCOPE}"]
    assert "client_secret" not in normal
    with pytest.raises(CalendarError):
        client.authorization_url("")
    with pytest.raises(CalendarError):
        client.authorization_url("state", code_verifier="short")


def test_oauth_exchange_and_refresh_are_form_encoded_server_calls():
    bodies = []

    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/token"
        assert request.method == "POST"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        bodies.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "new-secret", "refresh_token": "refresh-secret", "token_type": "Bearer", "expires_in": 3600, "scope": READ_SCOPE})

    client = oauth(handler)
    result = client.exchange_code("auth-code", code_verifier="v" * 64)
    assert result["refresh_token"] == "refresh-secret"
    assert bodies[0]["grant_type"] == ["authorization_code"]
    assert bodies[0]["code_verifier"] == ["v" * 64]
    client.refresh_token("old-refresh")
    assert bodies[1]["grant_type"] == ["refresh_token"]
    assert bodies[1]["refresh_token"] == ["old-refresh"]
    assert "code" not in bodies[1]


def test_oauth_failure_is_sanitized_and_redirect_not_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/collect"}, json={"error": "client-secret refresh-secret"})

    with pytest.raises(CalendarError) as caught:
        oauth(handler).exchange_code("private-code")
    assert len(seen) == 1
    assert "client-secret" not in str(caught.value)
    assert "refresh-secret" not in str(caught.value)

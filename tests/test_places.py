"""Real places: parsing Google Maps and OpenStreetMap, hours, matching, and the Omnara picker.

Network calls are faked; conftest keeps everything offline by default.
"""

from datetime import datetime

import pytest

from app import matching_core, place_agent, places, store
from app.contracts import PublicProfile

TUESDAY_7PM = datetime(2026, 10, 27, 19, 0)
TUESDAY_9PM = datetime(2026, 10, 27, 21, 0)


class Reply:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv("STUDYMATE_OFFLINE", raising=False)
    monkeypatch.setattr(places, "NOMINATIM_GAP", 0)
    monkeypatch.setattr(places, "maps_key", lambda: "")
    return monkeypatch


def profile(pid="u_a", lat=37.8678, lng=-122.2600, radius=1500, place_types=("coffee",)):
    return PublicProfile(
        id=pid, name="Alex Kim", study_style="quiet", radius_meters=radius, anchor="home",
        anchor_lat=lat, anchor_lng=lng, group_size=2, place_types=list(place_types),
        major="EECS", year=3, course_ids=["cs61a"],
    )


# Opening hours


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Mo-Fr 06:00-17:00; Sa-Su 08:00-15:00", [(0, "06:00", "17:00"), (5, "08:00", "15:00")]),
        ("24/7", [(1, "00:00", "23:59")]),
        ("Mo-Th 06:00-22:00; Su off", [(3, "06:00", "22:00")]),
        ("Tu 10:00-14:00,15:00-24:00", [(1, "10:00", "14:00"), (1, "15:00", "23:59")]),
        ("Mo-Su 11:00-26:00", [(1, "11:00", "23:59")]),
    ],
)
def test_osm_hours_are_parsed(text, expected):
    periods = places.osm_periods(text)
    for period in expected:
        assert period in periods


@pytest.mark.parametrize("text", ["", '"see website"', "Mo-Fr sunrise-sunset", "PH off"])
def test_unusual_osm_hours_are_unknown(text):
    assert places.osm_periods(text) is None


def test_open_during_and_hours_on():
    cafe = {"periods": [(1, "06:00", "17:00")]}
    assert places.open_during(cafe, TUESDAY_7PM, TUESDAY_9PM) is False
    assert places.hours_on(cafe, TUESDAY_7PM) == "Open 06:00–17:00"
    late = {"periods": [(1, "08:00", "22:00")]}
    assert places.open_during(late, TUESDAY_7PM, TUESDAY_9PM) is True
    unknown = {"periods": None}
    assert places.open_during(unknown, TUESDAY_7PM, TUESDAY_9PM) is None
    assert places.hours_on(unknown, TUESDAY_7PM) == "Hours not listed"
    assert places.hours_on({"periods": [(1, "11:00", "23:59")]}, TUESDAY_7PM) == "Open 11:00–late"
    assert places.hours_on({"periods": [(1, "00:00", "23:59")]}, TUESDAY_7PM) == "Open 24 hours"


def test_google_hours_count_days_from_sunday():
    periods = places._google_periods({"periods": [{"open": {"day": 2, "hour": 8, "minute": 0}, "close": {"day": 2, "hour": 22, "minute": 0}}]})
    assert periods == [(1, "08:00", "22:00")]
    assert places._google_periods({"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]})[0] == (0, "00:00", "23:59")


# Nearby search


def test_openstreetmap_nearby_keeps_only_real_places_of_the_kind(online):
    rows = [
        {"category": "amenity", "type": "library", "name": "Doe Memorial Library", "lat": "37.8723", "lon": "-122.2595",
         "osm_type": "way", "osm_id": 1, "address": {"road": "Memorial Glade", "city": "Berkeley"},
         "extratags": {"opening_hours": "Mo-Su 08:00-22:00"}},
        {"category": "shop", "type": "books", "name": "Library Books Store", "lat": "37.8700", "lon": "-122.2600"},
        {"category": "amenity", "type": "library", "name": "", "lat": "37.8700", "lon": "-122.2600"},
        {"category": "amenity", "type": "library", "name": "Far Library", "lat": "37.9500", "lon": "-122.2600"},
    ]
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(params)
        return Reply(rows)

    online.setattr(places.httpx, "get", fake_get)
    found = places.nearby("library", 37.8678, -122.2600, 1000)
    assert [place["name"] for place in found] == ["Doe Memorial Library"]
    doe = found[0]
    assert doe["source"] == "OpenStreetMap" and doe["address"] == "Memorial Glade, Berkeley"
    assert doe["periods"][0] == (0, "08:00", "22:00") and doe["map_url"].startswith("https://www.google.com/maps/")
    assert seen["q"] == "library" and seen["bounded"] == 1


def test_google_nearby_is_used_with_a_key(online):
    online.setattr(places, "maps_key", lambda: "test-key")
    sent = {}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        return Reply({"places": [
            {"id": "abc", "displayName": {"text": "Yali's Cafe"}, "shortFormattedAddress": "1920 Oxford St, Berkeley",
             "location": {"latitude": 37.8735, "longitude": -122.2662}, "rating": 4.4,
             "googleMapsUri": "https://maps.google.com/?cid=1", "businessStatus": "OPERATIONAL",
             "regularOpeningHours": {"periods": [{"open": {"day": 2, "hour": 7, "minute": 0}, "close": {"day": 2, "hour": 21, "minute": 0}}]}},
            {"id": "gone", "displayName": {"text": "Closed Cafe"}, "location": {"latitude": 37.87, "longitude": -122.26},
             "businessStatus": "CLOSED_PERMANENTLY"},
        ]})

    online.setattr(places.httpx, "post", fake_post)
    found = places.nearby("coffee", 37.8678, -122.2600, 1500)
    assert [place["name"] for place in found] == ["Yali's Cafe"]
    assert found[0]["rating"] == "4.4" and found[0]["source"] == "Google Maps"
    assert sent["url"] == places.NEARBY_URL and sent["headers"]["X-Goog-Api-Key"] == "test-key"
    assert sent["body"]["includedTypes"] == ["cafe", "coffee_shop"]


def test_nearby_is_cached(online):
    calls = []
    online.setattr(places.httpx, "get", lambda *a, **k: calls.append(1) or Reply([]))
    places.nearby("coffee", 37.8678, -122.2600, 1000)
    places.nearby("coffee", 37.8678, -122.2600, 1000)
    assert len(calls) == 1


def test_nearby_failure_returns_nothing_instead_of_inventing(online):
    def down(*args, **kwargs):
        raise places.httpx.ConnectError("down")

    online.setattr(places.httpx, "get", down)
    assert places.nearby("coffee", 37.8678, -122.2600, 1000) == []


def test_nearby_is_off_when_offline(monkeypatch):
    monkeypatch.setattr(places.httpx, "get", lambda *a, **k: pytest.fail("network used offline"))
    assert places.nearby("coffee", 37.8678, -122.2600, 1000) == []


# Matching uses real places


REAL = [
    {"id": "osm:1", "name": "Late Library", "address": "1 Glade", "kind": "library", "lat": 37.8690, "lng": -122.2600,
     "rating": "", "map_url": "https://www.google.com/maps/x", "periods": [(1, "08:00", "22:00")], "source": "OpenStreetMap"},
    {"id": "osm:2", "name": "Early Cafe", "address": "2 Ave", "kind": "coffee", "lat": 37.8680, "lng": -122.2605,
     "rating": "", "map_url": "https://www.google.com/maps/y", "periods": [(1, "06:00", "17:00")], "source": "OpenStreetMap"},
    {"id": "osm:3", "name": "Mystery Cafe", "address": "3 Ave", "kind": "coffee", "lat": 37.8682, "lng": -122.2601,
     "rating": "", "map_url": "https://www.google.com/maps/z", "periods": None, "source": "OpenStreetMap"},
]


def test_suggest_places_uses_real_places_and_ranks_open_first(monkeypatch):
    monkeypatch.setattr(matching_core.live_places, "nearby", lambda kind, lat, lng, radius: [p for p in REAL if p["kind"] == kind])
    people = [profile(), profile("u_b", lat=37.8690)]
    rows = matching_core.suggest_places("coffee", people, TUESDAY_7PM, TUESDAY_9PM, preferred=["library"])
    names = [row["name"] for row in rows]
    assert set(names) == {"Late Library", "Early Cafe", "Mystery Cafe"}
    assert names[0] == "Mystery Cafe"  # The kind asked for comes first, even with unlisted hours.
    assert names[-1] == "Early Cafe"  # Known to be closed then, so last.
    assert not any(row["name"] in {p["name"] for p in store.places()} for row in rows)


def test_suggest_places_falls_back_to_seeded_places_when_offline():
    rows = matching_core.suggest_places("coffee", [profile()], TUESDAY_7PM, TUESDAY_9PM)
    assert {row["name"] for row in rows} <= {p["name"] for p in store.places()}


def test_reason_skips_rating_when_the_source_has_none(monkeypatch):
    monkeypatch.setattr(matching_core.live_places, "nearby", lambda kind, lat, lng, radius: [p for p in REAL if p["kind"] == kind])
    rows = matching_core.suggest_places("library", [profile()], TUESDAY_7PM, TUESDAY_9PM)
    assert rows[0]["name"] == "Late Library" and rows[0]["hours"] == "Open 08:00–22:00"


# Omnara place picker


OPTIONS = [
    {"id": "a", "name": "Late Library", "kind": "library", "hours": "Open 08:00–22:00", "open": True, "hours_known": True,
     "rating": "", "walk_minutes": 6, "inside": True},
    {"id": "b", "name": "Mystery Cafe", "kind": "coffee", "hours": "Hours not listed", "open": True, "hours_known": False,
     "rating": "4.2", "walk_minutes": 3, "inside": True},
]


@pytest.fixture
def picker(monkeypatch):
    monkeypatch.delenv("STUDYMATE_OFFLINE", raising=False)
    monkeypatch.setattr(place_agent, "_picks", {})
    asked = []

    def answer(reply):
        def fake(prompt):
            asked.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(place_agent, "_ask", fake)

    answer.asked = asked
    return answer


def test_picker_returns_a_listed_place_and_a_reason(picker):
    picker({"choice": 2, "reason": "Closest cafe, and you asked for somewhere to talk."})
    assert place_agent.choose(OPTIONS, "coffee", "somewhere I can talk", "Tue 7–9 PM", ["Cafe"], []) == (
        1, "Closest cafe, and you asked for somewhere to talk.")
    prompt = picker.asked[0]
    assert "1. Late Library" in prompt and "2. Mystery Cafe" in prompt and "hours not listed" in prompt


@pytest.mark.parametrize("reply", [{"choice": 7, "reason": "x"}, {"choice": "two", "reason": "x"}, {"choice": 1, "reason": ""}, {}])
def test_picker_rejects_answers_outside_the_list(picker, reply):
    picker(reply)
    assert place_agent.choose(OPTIONS, "coffee", "", "Tue", [], []) is None


def test_picker_failure_keeps_matching_order(picker):
    picker(place_agent.omnara_client.OmnaraError("down"))
    assert place_agent.choose(OPTIONS, "coffee", "", "Tue", [], []) is None


def test_picker_answers_are_reused(picker):
    picker({"choice": 1, "reason": "Open the whole time."})
    place_agent.choose(OPTIONS, "library", "", "Tue", [], [])
    place_agent.choose(OPTIONS, "library", "", "Tue", [], [])
    assert len(picker.asked) == 1


def test_picker_is_off_offline_and_for_one_option(monkeypatch):
    monkeypatch.setattr(place_agent, "_ask", lambda prompt: pytest.fail("Omnara used"))
    assert place_agent.choose(OPTIONS, "coffee", "", "Tue", [], []) is None  # conftest is offline
    monkeypatch.delenv("STUDYMATE_OFFLINE")
    assert place_agent.choose(OPTIONS[:1], "coffee", "", "Tue", [], []) is None


def test_picker_agent_has_no_tools():
    config = place_agent._config({})
    assert "tools" not in config and config["instruction"]


def test_match_puts_the_picked_place_first(monkeypatch):
    seeded = store.places()
    monkeypatch.setattr(matching_core, "suggest_places", lambda *a, **k: [
        {**seeded[0], "inside": True, "open": True, "hours_known": True, "walk_minutes": 5},
        {**seeded[1], "inside": True, "open": True, "hours_known": True, "walk_minutes": 7},
    ])
    monkeypatch.setattr(matching_core.place_agent, "choose", lambda *a: (1, "Quiet and open late."))
    proposal = matching_core.build_proposal("CS 61A", "2026-10-27T19:00", "2026-10-27T21:00", "coffee", "")
    assert proposal.place.name == seeded[1]["name"]
    assert "Why this place: Quiet and open late." in proposal.reason


# Rooms


def test_new_rooms_are_named_after_a_real_nearby_place(monkeypatch):
    from app.routers import session

    monkeypatch.setattr(session.live_places, "nearby", lambda kind, lat, lng, radius: [REAL[0]] if kind == "library" else [])
    assert session._place_name("library", "") == "Late Library"
    assert session._place_name("outdoor", "") == "Faculty Glade"  # Nothing real found: seeded fallback.


# Place quality


def test_undocumented_openstreetmap_entries_are_dropped(online):
    rows = [
        {"category": "leisure", "type": "park", "name": "Dwight Triangle", "lat": "37.8680", "lon": "-122.2600",
         "osm_type": "way", "osm_id": 1, "address": {"road": "Dwight Way"}, "extratags": {"access": "yes"}},
        {"category": "leisure", "type": "park", "name": "Willard Park", "lat": "37.8640", "lon": "-122.2560",
         "osm_type": "way", "osm_id": 2, "address": {"house_number": "2730", "road": "Hillegass Ave"},
         "extratags": {"opening_hours": "Mo-Su 06:00-22:00", "website": "https://example.org"}},
    ]
    online.setattr(places.httpx, "get", lambda *a, **k: Reply(rows))
    found = places.nearby("outdoor", 37.8678, -122.2600, 1500)
    assert [place["name"] for place in found] == ["Willard Park"]
    assert found[0]["quality"] == 4


def test_best_prefers_a_documented_place_over_the_nearest(monkeypatch):
    near = {**REAL[2], "name": "Tiny Stand", "quality": 1, "lat": 37.8678, "lng": -122.2600}
    documented = {**REAL[1], "name": "Real Cafe", "quality": 4, "lat": 37.8700, "lng": -122.2600}
    monkeypatch.setattr(places, "nearby", lambda kind, lat, lng, radius: [near, documented])
    assert places.best("coffee", 37.8678, -122.2600, 1500)["name"] == "Real Cafe"


def test_room_cards_show_address_and_map_link(monkeypatch, online):
    from app.routers import session

    rows = [{"category": "amenity", "type": "cafe", "name": "Sweetheart Cafe", "lat": "37.8670", "lon": "-122.2560",
             "osm_type": "node", "osm_id": 9, "address": {"house_number": "2523", "road": "Durant Avenue", "city": "Berkeley"},
             "extratags": {"opening_hours": "Mo-Su 11:00-26:00"}}]
    online.setattr(places.httpx, "get", lambda *a, **k: Reply(rows))
    assert session._place_name("coffee", "") == "Sweetheart Cafe"
    details = session.place_details("Sweetheart Cafe")
    assert details["address"] == "2523 Durant Avenue, Berkeley"
    assert details["map_url"].startswith("https://www.google.com/maps/search/")
    assert session.place_details("Doe Library")["address"] == "Campus"  # Seeded rooms still get details.


def test_street_number_without_a_street_is_left_out():
    assert places._osm_address({"house_number": "2730", "city": "Berkeley"}) == "Berkeley"
    assert places._osm_address({"house_number": "2523", "road": "Durant Avenue", "city": "Berkeley"}) == "2523 Durant Avenue, Berkeley"


def test_room_settings_offer_real_places_and_accept_them(monkeypatch):
    from app.routers import session

    real = {**REAL[0], "quality": 3}
    monkeypatch.setattr(session.live_places, "nearby", lambda kind, lat, lng, radius: [real] if kind == "library" else [])
    names = [choice["name"] for choice in session._room_choices()["place_choices"]]
    assert names[0] == "Late Library"
    assert {place["name"] for place in store.places()} <= set(names)  # Demo places stay available.

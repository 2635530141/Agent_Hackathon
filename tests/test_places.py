"""suggest_places against the seeded list.

Seed: Yali's Cafe (coffee, 7 AM–9 PM, 4.4), Doe Library (library, 8 AM–10 PM, 4.6),
Faculty Glade (outdoor, 7 AM–6 PM, 4.7). Doe is ~70 m from campus, the Glade ~230 m, Yali's ~700 m.
"""

from app import matching_core as m
from factories import ME, at, inputs, person, request

EVENING = (at("2026-10-27T19:00"), at("2026-10-27T21:00"))
CAMPUS_ONLY = person("u_me", "Maya Chen", radius_meters=200).profile


def names(place_type, people=(ME.profile,), window=EVENING, avoid=None, preferred=None):
    return [p["name"] for p in m.suggest_places(place_type, list(people), *window, avoid, preferred)]


def test_requested_type_comes_first():
    assert names("library")[0] == "Doe Library"
    assert names("coffee")[0] == "Yali's Cafe"


def test_open_beats_type():
    assert names("outdoor")[:2] == ["Doe Library", "Yali's Cafe"]
    assert names("outdoor", window=(at("2026-10-27T15:00"), at("2026-10-27T17:00")))[0] == "Faculty Glade"


def test_preferred_type_beats_rating_among_fallbacks():
    assert names("outdoor", preferred=["coffee"])[0] == "Yali's Cafe"


def test_inside_every_radius_beats_type():
    assert names("coffee", people=(CAMPUS_ONLY,))[0] == "Doe Library"


def test_avoided_type_goes_last_even_when_closest():
    assert names("coffee", people=(CAMPUS_ONLY,), avoid={"library"})[-1] == "Doe Library"


def test_every_person_counts_for_the_radius():
    far = person("u_far", "Far Away", anchor="home", anchor_lat=37.8735, anchor_lng=-122.2700, radius_meters=500).profile
    rows = {p["name"]: p for p in m.suggest_places("library", [ME.profile, far], *EVENING)}
    assert rows["Yali's Cafe"]["inside"] is True
    assert rows["Doe Library"]["inside"] is False
    assert names("library", people=(ME.profile, far))[0] == "Yali's Cafe"


def test_walk_minutes_is_the_longer_trip():
    rows = {p["name"]: p for p in m.suggest_places("coffee", [ME.profile], *EVENING)}
    assert rows["Doe Library"]["walk_minutes"] == 1
    assert rows["Yali's Cafe"]["walk_minutes"] == 9
    east = person("u_e", "E E", anchor_lat=37.8719, anchor_lng=-122.2485).profile
    both = {p["name"]: p for p in m.suggest_places("coffee", [ME.profile, east], *EVENING)}
    assert both["Yali's Cafe"]["walk_minutes"] > rows["Yali's Cafe"]["walk_minutes"]


def test_opening_hours():
    yalis = next(p for p in m.places() if p["name"] == "Yali's Cafe")
    assert m._is_open(yalis, at("2026-10-27T19:00"), at("2026-10-27T21:00"))
    assert not m._is_open(yalis, at("2026-10-27T19:00"), at("2026-10-27T21:30"))
    assert not m._is_open(yalis, at("2026-10-27T06:30"), at("2026-10-27T08:00"))
    assert not m._is_open(yalis, at("2026-10-27T20:00"), at("2026-10-28T01:00"))
    assert m._is_open({"name": "No hours"}, at("2026-10-27T03:00"), at("2026-10-27T04:00"))


def test_swap_place_cycles_and_wraps():
    pair = inputs(ME, person("u_a", "Alex Kim"))
    picks = [m.match(request(), pair, swap_place=i).proposal.place.name for i in range(4)]
    assert picks == ["Doe Library", "Yali's Cafe", "Faculty Glade", "Doe Library"]


def test_card_place_has_no_internal_fields():
    place = m.match(request(), inputs(ME, person("u_a", "Alex Kim"))).proposal.place.model_dump()
    assert set(place) == {"name", "address", "kind", "hours", "rating", "map_url"}

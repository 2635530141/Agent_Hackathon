"""Real locations: addresses to coordinates, and study places near a point.

Google Maps is used when GOOGLE_MAPS_API_KEY is set (in .env or the environment).
Without a key, OpenStreetMap is used instead (Nominatim), which needs no key.
Every place returned here comes from one of those two sources; nothing is made up.

STUDYMATE_OFFLINE=1 turns network lookups off (the tests set it).
"""

import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import httpx

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "StudyMate/0.1 (student hackathon project; https://github.com/2635530141/Agent_Hackathon)"
# Bias address lookups toward the campus area so "2425 Durant Ave" means Berkeley.
BIAS_BOX = (-122.33, 37.80, -122.18, 37.93)  # west, south, east, north
CACHE_SECONDS = 24 * 60 * 60
NOMINATIM_GAP = 1.1  # OpenStreetMap allows at most one request per second.
MAX_RESULTS = 20

GOOGLE_TYPES = {"coffee": ["cafe", "coffee_shop"], "library": ["library"], "outdoor": ["park"]}
OSM_QUERY = {"coffee": "cafe", "library": "library", "outdoor": "park"}
OSM_TYPES = {
    "coffee": {("amenity", "cafe")},
    "library": {("amenity", "library")},
    "outdoor": {("leisure", "park"), ("leisure", "garden")},
}
GOOGLE_FIELDS = ",".join(
    f"places.{name}"
    for name in (
        "id",
        "displayName",
        "formattedAddress",
        "shortFormattedAddress",
        "location",
        "rating",
        "regularOpeningHours",
        "googleMapsUri",
        "businessStatus",
    )
)
DAY_CODES = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}

Period = tuple[int, str, str]  # weekday (Monday 0), opens "HH:MM", closes "HH:MM"


class PlacesError(Exception):
    """A lookup service could not be reached. The message is safe to show."""


_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()
_last_osm_call = 0.0


def offline() -> bool:
    return os.environ.get("STUDYMATE_OFFLINE") == "1"


def maps_key() -> str:
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        return os.environ["GOOGLE_MAPS_API_KEY"]
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GOOGLE_MAPS_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def source() -> str:
    return "Google Maps" if maps_key() else "OpenStreetMap"


def maps_url(lat: float, lng: float, name: str = "", address: str = "") -> str:
    query = f"{name}, {address}" if name else f"{lat},{lng}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def clear_cache() -> None:
    _cache.clear()


def _cached(key: tuple, fetch):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    value = fetch()
    _cache[key] = (time.time() + CACHE_SECONDS, value)
    return value


def _osm_get(params: dict) -> list[dict]:
    global _last_osm_call
    with _lock:
        wait = _last_osm_call + NOMINATIM_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            response = httpx.get(
                NOMINATIM_URL, params={**params, "format": "jsonv2"}, headers={"User-Agent": USER_AGENT}, timeout=10
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlacesError("OpenStreetMap could not be reached. Try again in a moment.") from exc
        finally:
            _last_osm_call = time.monotonic()


# Addresses


def _google_geocode(address: str, key: str) -> tuple[float, float] | None:
    west, south, east, north = BIAS_BOX
    try:
        response = httpx.get(
            GEOCODE_URL,
            params={"address": address, "key": key, "region": "us", "bounds": f"{south},{west}|{north},{east}"},
            timeout=5,
        )
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlacesError("Google Maps could not be reached. Try again in a moment.") from exc
    status = data.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        raise PlacesError(f"Google Maps could not look up the address ({status}).")
    location = data["results"][0]["geometry"]["location"]
    return location["lat"], location["lng"]


def _osm_geocode(address: str) -> tuple[float, float] | None:
    west, south, east, north = BIAS_BOX
    found = _osm_get({"q": address, "limit": 1, "countrycodes": "us", "viewbox": f"{west},{north},{east},{south}"})
    if not found:
        return None
    return float(found[0]["lat"]), float(found[0]["lon"])


def geocode(address: str) -> tuple[float, float] | None:
    """Coordinates for a real address, or None when the address is not found."""
    normalized = " ".join(address.lower().split())
    key = maps_key()
    if key:
        return _cached(("geocode", "google", normalized), lambda: _google_geocode(address, key))
    return _cached(("geocode", "osm", normalized), lambda: _osm_geocode(address))


# Opening hours


def _google_periods(hours: dict | None) -> list[Period] | None:
    if not hours or not hours.get("periods"):
        return None
    periods: list[Period] = []
    for period in hours["periods"]:
        opens, closes = period.get("open"), period.get("close")
        if opens and not closes:  # Open around the clock.
            return [(day, "00:00", "23:59") for day in range(7)]
        weekday = (opens["day"] + 6) % 7  # Google counts from Sunday.
        start = f"{opens.get('hour', 0):02d}:{opens.get('minute', 0):02d}"
        end = "23:59" if closes["day"] != opens["day"] else f"{closes.get('hour', 0):02d}:{closes.get('minute', 0):02d}"
        periods.append((weekday, start, end))
    return periods


def _day_span(text: str) -> list[int] | None:
    days: list[int] = []
    for part in text.split(","):
        part = part.strip().lower()
        if "-" in part:
            first, _, last = part.partition("-")
            if first[:2] not in DAY_CODES or last[:2] not in DAY_CODES:
                return None
            day, end = DAY_CODES[first[:2]], DAY_CODES[last[:2]]
            days.append(day)
            while day != end:
                day = (day + 1) % 7
                days.append(day)
        elif part[:2] in DAY_CODES and len(part) == 2:
            days.append(DAY_CODES[part])
        else:
            return None
    return days


def osm_periods(text: str) -> list[Period] | None:
    """Simple OpenStreetMap opening_hours like "Mo-Fr 08:00-17:00; Sa 10:00-14:00".

    Anything more complicated returns None, meaning the hours are unknown.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text == "24/7":
        return [(day, "00:00", "23:59") for day in range(7)]
    periods: dict[int, list[tuple[str, str]]] = {}
    for rule in text.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        match = re.fullmatch(r"(?:([A-Za-z,\- ]+?)\s+)?((?:\d{1,2}:\d{2}-\d{1,2}:\d{2},?\s*)+|off|closed)", rule)
        if not match:
            return None
        days = _day_span(match.group(1)) if match.group(1) else list(range(7))
        if days is None:
            return None
        for day in days:
            periods[day] = []
            if match.group(2) in ("off", "closed"):
                continue
            for span in re.findall(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", match.group(2)):
                start, end = (value.zfill(5) for value in span)
                # "26:00" means 2 am the next day; count it as open until the end of this day.
                periods[day].append((start, "23:59" if end >= "24:00" or end == "00:00" else end))
    return [(day, start, end) for day, spans in sorted(periods.items()) for start, end in spans]


def open_during(place: dict, start: datetime, end: datetime) -> bool | None:
    """True or False from the listed hours, or None when hours are not listed."""
    periods = place.get("periods")
    if periods is None:
        return None
    if start.date() != end.date():
        return False
    begin, finish = start.strftime("%H:%M"), end.strftime("%H:%M")
    return any(day == start.weekday() and opens <= begin and finish <= closes for day, opens, closes in periods)


def hours_on(place: dict, day: datetime) -> str:
    periods = place.get("periods")
    if periods is None:
        return "Hours not listed"
    spans = [f"{opens}–{'late' if closes == '23:59' else closes}" for weekday, opens, closes in periods if weekday == day.weekday()]
    if not spans:
        return "Closed that day"
    if spans == ["00:00–late"]:
        return "Open 24 hours"
    return "Open " + ", ".join(spans)


# Places


def _google_nearby(kind: str, lat: float, lng: float, radius: int, key: str) -> list[dict]:
    body = {
        "includedTypes": GOOGLE_TYPES[kind],
        "maxResultCount": MAX_RESULTS,
        "rankPreference": "DISTANCE",
        "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(radius)}},
    }
    try:
        response = httpx.post(
            NEARBY_URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS}, timeout=8
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlacesError("Google Places could not be reached.") from exc
    found = []
    for place in data.get("places", []):
        if place.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        name = (place.get("displayName") or {}).get("text", "").strip()
        location = place.get("location") or {}
        if not name or "latitude" not in location:
            continue
        address = place.get("shortFormattedAddress") or place.get("formattedAddress") or ""
        rating = place.get("rating")
        found.append(
            {
                "id": f"google:{place.get('id', name)}",
                "name": name,
                "address": address,
                "kind": kind,
                "lat": location["latitude"],
                "lng": location["longitude"],
                "rating": f"{rating:.1f}" if isinstance(rating, (int, float)) else "",
                "map_url": place.get("googleMapsUri") or maps_url(location["latitude"], location["longitude"], name, address),
                "periods": _google_periods(place.get("regularOpeningHours")),
                "source": "Google Maps",
            }
        )
    return found


def _osm_address(details: dict) -> str:
    street = " ".join(part for part in (details.get("house_number"), details.get("road")) if part)
    city = details.get("city") or details.get("town") or details.get("village") or ""
    return ", ".join(part for part in (street, city) if part)


def _osm_nearby(kind: str, lat: float, lng: float, radius: int) -> list[dict]:
    dlat = radius / 111_000
    dlng = radius / (111_000 * max(0.2, math.cos(math.radians(lat))))
    rows = _osm_get(
        {
            "q": OSM_QUERY[kind],
            "viewbox": f"{lng - dlng},{lat + dlat},{lng + dlng},{lat - dlat}",
            "bounded": 1,
            "limit": 40,
            "extratags": 1,
            "addressdetails": 1,
        }
    )
    found = []
    for row in rows:
        if (row.get("category"), row.get("type")) not in OSM_TYPES[kind]:
            continue
        name = (row.get("name") or "").strip()
        if not name:
            continue
        place_lat, place_lng = float(row["lat"]), float(row["lon"])
        address = _osm_address(row.get("address") or {})
        found.append(
            {
                "id": f"osm:{row.get('osm_type', '')}{row.get('osm_id', name)}",
                "name": name,
                "address": address,
                "kind": kind,
                "lat": place_lat,
                "lng": place_lng,
                "rating": "",
                "map_url": maps_url(place_lat, place_lng, name, address),
                "periods": osm_periods((row.get("extratags") or {}).get("opening_hours", "")),
                "source": "OpenStreetMap",
            }
        )
    return found


def nearby(kind: str, lat: float, lng: float, radius: int) -> list[dict]:
    """Real places of one kind (coffee, library, outdoor) within radius meters, nearest first.

    Returns an empty list when offline or when the lookup fails, so callers can
    fall back without inventing anything.
    """
    if kind not in GOOGLE_TYPES or offline():
        return []
    radius = int(min(max(radius, 200), 50_000))
    key = maps_key()
    cache_key = ("nearby", "google" if key else "osm", kind, round(lat, 3), round(lng, 3), radius)
    try:
        if key:
            found = _cached(cache_key, lambda: _google_nearby(kind, lat, lng, radius, key))
        else:
            found = _cached(cache_key, lambda: _osm_nearby(kind, lat, lng, radius))
    except PlacesError:
        return []
    inside = [place for place in found if meters(lat, lng, place["lat"], place["lng"]) <= radius]
    return sorted(inside, key=lambda place: meters(lat, lng, place["lat"], place["lng"]))

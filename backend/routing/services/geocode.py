"""Place search through Nominatim. One request per second, per their usage policy."""

from __future__ import annotations

import re
import threading
import time

from .http import get_json

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_LOCK = threading.Lock()
_LAST_CALL = 0.0
_CACHE: dict[str, list[dict]] = {}

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodeError(Exception):
    pass


def search_places(query: str, limit: int = 5) -> list[dict]:
    text = query.strip()
    if len(text) < 2:
        return []
    direct = _LATLON.match(text)
    if direct:
        lat = float(direct.group(1))
        lon = float(direct.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return []
        return [{"label": f"{lat:.4f}, {lon:.4f}", "lat": lat, "lon": lon}]

    key = text.casefold()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached[:limit]

    payload = _nominatim(text, limit)
    places = [_place(item) for item in payload if isinstance(item, dict)]
    _CACHE[key] = places
    if len(_CACHE) > 400:
        _CACHE.clear()
        _CACHE[key] = places
    return places


def _nominatim(query: str, limit: int) -> list:
    global _LAST_CALL
    params = {
        "q": query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": limit,
    }
    with _LOCK:
        wait = 1.05 - (time.time() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        payload = get_json(NOMINATIM_URL, params, timeout=15, attempts=2)
        _LAST_CALL = time.time()
    if isinstance(payload, dict):
        raise GeocodeError(str(payload.get("error") or "Geocoding failed"))
    return payload


def _place(item: dict) -> dict:
    return {
        "label": _label(item),
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
    }


def _label(item: dict) -> str:
    address = item.get("address") or {}
    locality = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("municipality")
        or address.get("county")
    )
    name = item.get("name")
    state = address.get("state")
    parts: list[str] = []
    for part in (name, locality, state):
        if part and part not in parts:
            parts.append(part)
    if parts:
        return ", ".join(parts[:3])
    return item.get("display_name") or "Unknown place"

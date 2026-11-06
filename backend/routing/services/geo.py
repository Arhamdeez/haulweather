"""Geographic helpers. Coordinates are (longitude, latitude) unless noted."""

from __future__ import annotations

import math

EARTH_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(a)))


def cumulative_miles(coords: list[tuple[float, float]]) -> list[float]:
    cum = [0.0]
    for index in range(1, len(coords)):
        lon1, lat1 = coords[index - 1]
        lon2, lat2 = coords[index]
        cum.append(cum[-1] + haversine_miles(lon1, lat1, lon2, lat2))
    return cum


def destination(lon: float, lat: float, bearing: float, distance_miles: float) -> tuple[float, float]:
    """Point reached by traveling `distance_miles` on `bearing` (0 = north, 90 = east)."""
    if distance_miles == 0:
        return lon, lat
    delta = distance_miles / EARTH_MILES
    theta = math.radians(bearing)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(delta) + math.cos(lat1) * math.sin(delta) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(lat1),
        math.cos(delta) - math.sin(lat1) * math.sin(lat2),
    )
    lon_deg = (math.degrees(lon2) + 180) % 360 - 180
    return lon_deg, math.degrees(lat2)


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def sample_mileposts(total_miles: float, interval: float) -> list[float]:
    """Mileposts from 0 through total, spaced by `interval`, always including both ends."""
    if total_miles <= 0:
        return [0.0]
    posts = [0.0]
    mile = interval
    while mile < total_miles:
        posts.append(mile)
        mile += interval
    gap = total_miles - posts[-1]
    # Drop a sample that would sit on top of the destination.
    if posts[-1] != total_miles:
        if gap < max(1.0, interval * 0.2) and len(posts) > 1:
            posts[-1] = total_miles
        else:
            posts.append(total_miles)
    return posts


def locate(coords: list[tuple[float, float]], cum: list[float], mile: float) -> tuple[float, float, int, float]:
    """Return lon, lat, segment index, and 0–1 position along that segment."""
    if not coords:
        raise ValueError("empty geometry")
    if mile <= 0 or len(coords) == 1:
        return coords[0][0], coords[0][1], 0, 0.0
    if mile >= cum[-1]:
        last_segment = max(0, len(coords) - 2)
        return coords[-1][0], coords[-1][1], last_segment, 1.0
    lo, hi = 1, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < mile:
            lo = mid + 1
        else:
            hi = mid
    span = cum[lo] - cum[lo - 1]
    t = 0.0 if span <= 0 else (mile - cum[lo - 1]) / span
    lon = coords[lo - 1][0] + t * (coords[lo][0] - coords[lo - 1][0])
    lat = coords[lo - 1][1] + t * (coords[lo][1] - coords[lo - 1][1])
    return lon, lat, lo - 1, t


def path_between(
    coords: list[tuple[float, float]],
    cum: list[float],
    mile_a: float,
    mile_b: float,
) -> list[tuple[float, float]]:
    start_mile = min(mile_a, mile_b)
    end_mile = max(mile_a, mile_b)
    start = locate(coords, cum, start_mile)
    end = locate(coords, cum, end_mile)
    inner: list[tuple[float, float]] = []
    for (lon, lat), mile in zip(coords, cum):
        if start_mile < mile < end_mile:
            inner.append((lon, lat))
    return [(start[0], start[1]), *inner, (end[0], end[1])]


def seconds_at(durations: list[float] | None, segment_index: int, t: float, fallback: float) -> float:
    if not durations:
        return fallback
    if segment_index >= len(durations):
        return float(sum(durations))
    clamped = min(1.0, max(0.0, t))
    return float(sum(durations[:segment_index]) + durations[segment_index] * clamped)


def mean_separation_miles(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> float:
    """Average distance from samples of `source` to the `target` path."""
    if len(source) < 2 or len(target) < 2:
        return 0.0
    cum = cumulative_miles(source)
    total = cum[-1]
    step = max(1, len(target) // 250)
    reduced = target[::step]
    if reduced[-1] != target[-1]:
        reduced.append(target[-1])
    separations: list[float] = []
    for fraction in (0.15, 0.3, 0.42, 0.55, 0.68, 0.82):
        lon, lat, _, _ = locate(source, cum, total * fraction)
        nearest = min(
            _miles_to_segment(lon, lat, reduced[index], reduced[index + 1])
            for index in range(len(reduced) - 1)
        )
        separations.append(nearest)
    return sum(separations) / len(separations)


def _miles_to_segment(
    lon: float,
    lat: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    lat_scale = 69.0
    lon_scale = max(1.0, 69.0 * math.cos(math.radians(lat)))
    ax = (start[0] - lon) * lon_scale
    ay = (start[1] - lat) * lat_scale
    bx = (end[0] - lon) * lon_scale
    by = (end[1] - lat) * lat_scale
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
    return math.hypot(ax + t * dx, ay + t * dy)

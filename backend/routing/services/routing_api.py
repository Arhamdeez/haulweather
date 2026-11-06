"""Three driving routes from OSRM.

The public service often returns a single path even when alternatives are
requested. The other two options are still OSRM routes: each is forced through
a waypoint offset to one side of the direct path, then rejected if it is
almost the same road or an unreasonable detour.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geo import (
    METERS_PER_MILE,
    bearing_deg,
    cumulative_miles,
    destination,
    haversine_miles,
    locate,
    mean_separation_miles,
)
from .http import UpstreamError, get_json

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


@dataclass
class RouteOption:
    coords: list[tuple[float, float]]
    distance_m: float
    duration_s: float
    annotation_distance: list[float] | None = None
    annotation_duration: list[float] | None = None
    name: str = ""
    source: str = "osrm"

    @property
    def distance_mi(self) -> float:
        return self.distance_m / METERS_PER_MILE


class RoutingError(Exception):
    pass


def driving_routes(
    origin: tuple[float, float],
    destination_point: tuple[float, float],
    fetch_impl=None,
) -> list[RouteOption]:
    fetch = fetch_impl or osrm_driving
    try:
        found = fetch([origin, destination_point], True)
    except UpstreamError as exc:
        raise RoutingError(str(exc)) from exc
    routes = _distinct(found, minimum_gap=6.0)
    if not routes:
        raise RoutingError("No driving route was found between those places.")

    direct = routes[0]
    if len(routes) >= 3:
        chosen = routes[:3]
        name_routes(chosen)
        return chosen

    total = max(direct.distance_mi, 1)
    offset = min(70.0, max(12.0, total * 0.16))
    max_ratio = 2.5 if total > 80 else 3.2
    attempts = (
        (0.50, 1, offset),
        (0.50, -1, offset),
        (0.35, 1, offset * 1.25),
        (0.65, -1, offset * 1.25),
        (0.42, -1, offset * 0.7),
        (0.58, 1, offset * 0.7),
        (0.30, 1, min(90.0, offset * 1.6)),
        (0.70, -1, min(90.0, offset * 1.6)),
    )
    for fraction, side, miles in attempts:
        if len(routes) >= 3:
            break
        via = _via_point(direct, fraction, side, miles)
        if via is None:
            continue
        if haversine_miles(*origin, *via) < 5 or haversine_miles(*destination_point, *via) < 5:
            continue
        try:
            extra = fetch([origin, via, destination_point], False)
        except (UpstreamError, RoutingError):
            continue
        if not extra:
            continue
        candidate = extra[0]
        if candidate.distance_mi > direct.distance_mi * max_ratio:
            continue
        if any(mean_separation_miles(candidate.coords, kept.coords) < 6.0 for kept in routes):
            continue
        candidate.source = "osrm-via"
        routes.append(candidate)

    if len(routes) < 3:
        routes = _relax(routes, direct, attempts, fetch, origin, destination_point)

    if len(routes) < 3:
        raise RoutingError(
            f"Only {len(routes)} distinct driving route(s) were found. "
            "Try a longer trip, or places connected by more than one highway."
        )
    chosen = routes[:3]
    name_routes(chosen)
    return chosen


def _relax(routes, direct, attempts, fetch, origin, destination_point):
    """Second pass with a looser detour cap if the corridor has few roads."""
    if len(routes) >= 3:
        return routes
    total = max(direct.distance_mi, 1)
    offset = min(80.0, max(18.0, total * 0.22))
    for fraction, side, _miles in attempts:
        if len(routes) >= 3:
            break
        via = _via_point(direct, fraction, side, offset)
        if via is None:
            continue
        try:
            extra = fetch([origin, via, destination_point], False)
        except (UpstreamError, RoutingError):
            continue
        if not extra:
            continue
        candidate = extra[0]
        if candidate.distance_mi > direct.distance_mi * 3.4:
            continue
        if any(mean_separation_miles(candidate.coords, kept.coords) < 3.0 for kept in routes):
            continue
        candidate.source = "osrm-via"
        routes.append(candidate)
    return routes


def _distinct(routes: list[RouteOption], minimum_gap: float) -> list[RouteOption]:
    kept: list[RouteOption] = []
    for route in routes:
        if len(route.coords) < 2 or route.distance_m <= 0:
            continue
        if any(mean_separation_miles(route.coords, existing.coords) < minimum_gap for existing in kept):
            continue
        kept.append(route)
    return kept


def _via_point(route: RouteOption, fraction: float, side: int, offset_miles: float) -> tuple[float, float] | None:
    cum = cumulative_miles(route.coords)
    total = cum[-1]
    if total <= 1:
        return None
    mile = total * fraction
    lon, lat, _, _ = locate(route.coords, cum, mile)
    before = locate(route.coords, cum, max(0.0, mile - min(3.0, total * 0.05)))
    after = locate(route.coords, cum, min(total, mile + min(3.0, total * 0.05)))
    heading = bearing_deg(before[0], before[1], after[0], after[1])
    return destination(lon, lat, (heading + 90 * side) % 360, offset_miles)


def osrm_driving(points: list[tuple[float, float]], alternatives: bool) -> list[RouteOption]:
    location = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in points)
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        "annotations": "duration,distance",
        "alternatives": "3" if alternatives else "false",
    }
    payload = get_json(f"{OSRM_URL}/{location}", params, timeout=40, attempts=2)
    if not isinstance(payload, dict):
        raise RoutingError("Routing returned an unexpected response.")
    if payload.get("code") != "Ok":
        message = payload.get("message") or payload.get("code") or "Routing failed"
        raise RoutingError(str(message))
    routes = []
    for raw in payload.get("routes") or []:
        parsed = _parse_route(raw)
        if parsed is not None:
            routes.append(parsed)
    return routes


def _parse_route(raw: dict) -> RouteOption | None:
    geometry = (raw.get("geometry") or {}).get("coordinates") or []
    coords = [(float(pair[0]), float(pair[1])) for pair in geometry if len(pair) >= 2]
    if len(coords) < 2:
        return None
    legs = raw.get("legs") or []
    distances: list[float] = []
    durations: list[float] = []
    for leg in legs:
        annotation = leg.get("annotation") or {}
        distances.extend(float(value) for value in annotation.get("distance") or [])
        durations.extend(float(value) for value in annotation.get("duration") or [])
    if distances and len(distances) != len(coords) - 1:
        distances = []
        durations = []
    return RouteOption(
        coords=coords,
        distance_m=float(raw.get("distance") or 0),
        duration_s=float(raw.get("duration") or 0),
        annotation_distance=distances or None,
        annotation_duration=durations or None,
    )


def name_routes(routes: list[RouteOption]) -> None:
    fastest = min(routes, key=lambda route: (route.duration_s, route.distance_m))
    fastest.name = "Fastest"
    rest = [route for route in routes if route is not fastest]
    if len(rest) == 1:
        rest[0].name = _relative_name(rest[0], fastest)
        return
    if len(rest) != 2:
        for index, route in enumerate(rest, start=2):
            route.name = f"Alternate {index}"
        return
    higher, lower = sorted(rest, key=_mid_lat, reverse=True)
    if _mid_lat(higher) - _mid_lat(lower) >= 0.12:
        higher.name = "Northern"
        lower.name = "Southern"
        return
    west, east = sorted(rest, key=_mid_lon)
    west.name = "Western"
    east.name = "Eastern"


def _relative_name(route: RouteOption, baseline: RouteOption) -> str:
    lat_gap = _mid_lat(route) - _mid_lat(baseline)
    lon_gap = _mid_lon(route) - _mid_lon(baseline)
    if lat_gap >= 0.1:
        return "Northern"
    if lat_gap <= -0.1:
        return "Southern"
    if lon_gap >= 0.1:
        return "Eastern"
    if lon_gap <= -0.1:
        return "Western"
    return "Alternate"


def _mid_lat(route: RouteOption) -> float:
    return _middle_average(route, axis=1)


def _mid_lon(route: RouteOption) -> float:
    return _middle_average(route, axis=0)


def _middle_average(route: RouteOption, axis: int) -> float:
    count = len(route.coords)
    window = route.coords[count // 3 : max(count // 3 + 1, (2 * count) // 3)]
    return sum(point[axis] for point in window) / len(window)

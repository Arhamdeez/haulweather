"""Turn a trip request into three weather-scored routes and a corridor forecast."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .geo import (
    cumulative_miles,
    destination,
    bearing_deg,
    haversine_miles,
    locate,
    path_between,
    sample_mileposts,
    seconds_at,
)
from .geocode import search_places
from .risk import assess_conditions, explain_recommendation, ownership_spans, recommendation_sort_key, summarize
from .routing_api import RouteOption, RoutingError, driving_routes
from .weather import fetch_series, sample_at, sample_hours

logger = logging.getLogger(__name__)

SAMPLE_CHOICES = (10, 25, 50)
MAX_CHECKPOINTS = 450
CORRIDOR_HOURS = 49


class PlanError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def plan_trip(body: dict) -> dict:
    started = time.perf_counter()
    request = parse_request(body)
    origin = _resolve(request["origin_query"], request["origin_point"])
    destination = _resolve(request["destination_query"], request["destination_point"])
    if haversine_miles(origin["lon"], origin["lat"], destination["lon"], destination["lat"]) < 1:
        raise PlanError("Origin and destination need to be different places.")

    routes = driving_routes((origin["lon"], origin["lat"]), (destination["lon"], destination["lat"]))
    estimated = sum(len(sample_mileposts(route.distance_mi, request["sample_miles"])) for route in routes)
    if estimated > MAX_CHECKPOINTS:
        raise PlanError(
            f"That interval needs about {estimated} weather samples. Choose 25 or 50 miles for a long haul."
        )

    slots_by_route = [checkpoint_slots(route, request["sample_miles"], request["departure"]) for route in routes]
    corridor = _corridor_points(routes)
    cells = _unique_cells(
        [(slot["lon"], slot["lat"]) for slots in slots_by_route for slot in slots]
        + corridor
    )
    window_start, window_end = _forecast_window(request["departure"], slots_by_route)
    series_list = fetch_series(cells, window_start, window_end)
    series_by_cell = {cell: series for cell, series in zip(cells, series_list)}

    def weather_at(lon: float, lat: float, when: datetime) -> dict:
        return sample_at(series_by_cell[_cell(lon, lat)], when)

    evaluated = []
    for route, slots in zip(routes, slots_by_route):
        evaluated.append(
            _assemble(route, slots, request["load_lb"], weather_at)
        )

    ranked = sorted(evaluated, key=lambda route: recommendation_sort_key(route["summary"], route["duration_s"]))
    for rank, route in enumerate(ranked, start=1):
        route["rank"] = rank
        route["recommended"] = rank == 1
        route["id"] = f"route-{rank}"

    clamped = sum(checkpoint["forecast_clamped"] for route in ranked for checkpoint in route["checkpoints"])
    local_departure = request["departure"].astimezone(request["tz"])
    corridor_payload = _corridor_forecast(
        corridor,
        series_by_cell,
        request["departure"],
        local_departure,
        request["load_lb"],
    )
    logger.info(
        "planned %s -> %s in %.1fs (%s checkpoints, %s weather cells)",
        origin["label"],
        destination["label"],
        time.perf_counter() - started,
        sum(len(route["checkpoints"]) for route in ranked),
        len(cells),
    )
    return {
        "origin": origin,
        "destination": destination,
        "departure": local_departure.isoformat(),
        "timezone": request["timezone_name"],
        "load_lb": int(request["load_lb"]) if request["load_lb"].is_integer() else request["load_lb"],
        "sample_miles": request["sample_miles"],
        "recommendation": {
            "route_id": ranked[0]["id"],
            "text": explain_recommendation(ranked),
            "blocked": ranked[0]["summary"]["miles"]["no_travel"] > 0,
        },
        "forecast_note": (
            f"{clamped} checkpoints fall outside the hourly forecast and use the nearest hour."
            if clamped
            else None
        ),
        "routes": ranked,
        "corridor": corridor_payload,
    }


def parse_request(body: dict) -> dict:
    if not isinstance(body, dict):
        raise PlanError("Expected a JSON object.")
    origin = str(body.get("origin") or "").strip()
    destination = str(body.get("destination") or "").strip()
    if not origin or not destination:
        raise PlanError("Enter both an origin and a destination.")
    try:
        load = float(body.get("load_lb"))
    except (TypeError, ValueError):
        raise PlanError("Enter the load weight in pounds.")
    if not 0 < load <= 120000:
        raise PlanError("Load weight must be between 1 and 120,000 lb.")
    try:
        sample = int(body.get("sample_miles"))
    except (TypeError, ValueError):
        raise PlanError("Choose a sample interval of 10, 25, or 50 miles.")
    if sample not in SAMPLE_CHOICES:
        raise PlanError("Choose a sample interval of 10, 25, or 50 miles.")
    timezone_name = str(body.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:
        raise PlanError("Unknown timezone.") from exc
    raw_departure = str(body.get("departure") or "").strip()
    if not raw_departure:
        raise PlanError("Enter a departure date and time.")
    return {
        "origin_query": origin,
        "destination_query": destination,
        "origin_point": _optional_point(body, "origin"),
        "destination_point": _optional_point(body, "destination"),
        "load_lb": load,
        "sample_miles": sample,
        "timezone_name": timezone_name,
        "tz": tz,
        "departure": _parse_departure(raw_departure, tz),
    }


def checkpoint_slots(route: RouteOption, interval: float, departure: datetime) -> list[dict]:
    """Sample the road at a fixed mile interval and estimate arrival at each point."""
    road_miles = route.distance_mi
    cum = cumulative_miles(route.coords)
    geometry_miles = cum[-1]
    slots = []
    for display_mile in sample_mileposts(road_miles, interval):
        fraction = 0.0 if road_miles <= 0 else display_mile / road_miles
        along_geometry = fraction * geometry_miles
        lon, lat, segment, t = locate(route.coords, cum, along_geometry)
        seconds = seconds_at(route.annotation_duration, segment, t, route.duration_s * fraction)
        slots.append(
            {
                "mile": display_mile,
                "lon": lon,
                "lat": lat,
                "eta": departure + timedelta(seconds=seconds),
                "_geometry_miles": geometry_miles,
                "_road_miles": road_miles,
            }
        )
    return slots


def evaluate_route(route: RouteOption, *, interval: float, departure: datetime, load_lb: float, weather_at) -> dict:
    slots = checkpoint_slots(route, interval, departure)
    return _assemble(route, slots, load_lb, weather_at)


def _assemble(route: RouteOption, slots: list[dict], load_lb: float, weather_at) -> dict:
    checkpoints = []
    for slot in slots:
        sample = weather_at(slot["lon"], slot["lat"], slot["eta"])
        assessed = assess_conditions(sample["wind"], sample["rain"], sample["snow"], load_lb)
        gust = sample.get("gust")
        sustained = sample.get("sustained")
        checkpoints.append(
            {
                "mile": round(slot["mile"], 1),
                "lat": round(slot["lat"], 5),
                "lon": round(slot["lon"], 5),
                "eta": slot["eta"].astimezone(timezone.utc).isoformat(),
                "wind_mph": round(sample["wind"], 1),
                "sustained_mph": None if sustained is None else round(sustained, 1),
                "gust_mph": None if gust is None else round(gust, 1),
                "rain_in_hr": round(sample["rain"], 3),
                "snow_in_hr": round(sample["snow"], 3),
                "wind_level": assessed["wind_level"],
                "rain_level": assessed["rain_level"],
                "snow_level": assessed["snow_level"],
                "risk": assessed["risk"],
                "drivers": assessed["drivers"],
                "load_note": assessed["load_note"],
                "forecast_clamped": bool(sample.get("clamped")),
            }
        )
    # Ownership uses the precise mileposts. The payload above keeps rounded display miles.
    summary = summarize(
        [{"mile": slot["mile"], "risk": checkpoint["risk"]} for slot, checkpoint in zip(slots, checkpoints)]
    )
    return {
        "name": route.name,
        "distance_mi": round(route.distance_mi, 1),
        "duration_s": round(route.duration_s),
        "arrival": checkpoints[-1]["eta"] if checkpoints else None,
        "checkpoints": checkpoints,
        "segments": _segments(route, slots, checkpoints),
        "summary": summary,
    }


def _segments(route: RouteOption, slots: list[dict], checkpoints: list[dict]) -> list[dict]:
    precise = [{"mile": slot["mile"], "risk": checkpoint["risk"]} for slot, checkpoint in zip(slots, checkpoints)]
    cum = cumulative_miles(route.coords)
    geometry_miles = cum[-1]
    road_miles = slots[0]["_road_miles"] if slots else route.distance_mi
    segments = []
    for span in ownership_spans(precise):
        length = span["to_mile"] - span["from_mile"]
        if length <= 0.05:
            continue
        path = path_between(route.coords, cum, _to_geometry(span["from_mile"], road_miles, geometry_miles), _to_geometry(span["to_mile"], road_miles, geometry_miles))
        segments.append(
            {
                "risk": span["risk"],
                "from_mile": round(span["from_mile"], 1),
                "to_mile": round(span["to_mile"], 1),
                "path": [[round(lat, 5), round(lon, 5)] for lon, lat in path],
            }
        )
    return segments


def _to_geometry(display_mile: float, road_miles: float, geometry_miles: float) -> float:
    if road_miles <= 0:
        return 0.0
    return display_mile / road_miles * geometry_miles


def _resolve(query: str, point: tuple[float, float] | None) -> dict:
    if point is not None:
        lon, lat = point
        return {"label": query, "lat": lat, "lon": lon}
    matches = search_places(query, limit=1)
    if not matches:
        raise PlanError(f'Could not find "{query}".')
    return matches[0]


def _optional_point(body: dict, prefix: str) -> tuple[float, float] | None:
    lat = body.get(f"{prefix}_lat")
    lon = body.get(f"{prefix}_lon")
    if lat is None and lon is None:
        return None
    try:
        lat_value = float(lat)
        lon_value = float(lon)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"The {prefix} coordinates are invalid.") from exc
    if not (-90 <= lat_value <= 90 and -180 <= lon_value <= 180):
        raise PlanError(f"The {prefix} coordinates are out of range.")
    return lon_value, lat_value


def _parse_departure(value: str, tz: ZoneInfo) -> datetime:
    try:
        has_offset = value.endswith("Z") or "+" in value[10:] or value.count("-") > 2
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if has_offset else datetime.fromisoformat(value)
    except ValueError as exc:
        raise PlanError("Departure time is not a valid date.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _forecast_window(departure: datetime, slots_by_route: list[list[dict]]) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if departure < now - timedelta(hours=12):
        raise PlanError("Departure is too far in the past for the forecast.")
    if departure > now + timedelta(days=10):
        raise PlanError("Departure is outside the 10-day forecast. Pick an earlier date.")
    latest = departure
    for slots in slots_by_route:
        if slots:
            latest = max(latest, slots[-1]["eta"])
    start = min(departure, now) - timedelta(hours=1)
    end = max(departure + timedelta(hours=48), latest) + timedelta(hours=2)
    horizon = now + timedelta(days=15)
    if end > horizon:
        end = horizon
    if end < start:
        end = start + timedelta(hours=2)
    return start, end


def _cell(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon / 0.05) * 0.05, round(lat / 0.05) * 0.05)


def _unique_cells(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    seen: dict[tuple[float, float], None] = {}
    for lon, lat in points:
        seen.setdefault(_cell(lon, lat), None)
    return list(seen)


def _corridor_points(routes: list[RouteOption]) -> list[tuple[float, float]]:
    longest = max((cumulative_miles(route.coords)[-1] for route in routes), default=0)
    spacing = max(18.0, longest / 16)
    lateral = min(22.0, spacing * 0.55)
    raw: list[tuple[float, float]] = []
    for route in routes:
        cum = cumulative_miles(route.coords)
        total = cum[-1]
        if total <= 0:
            continue
        mile = 0.0
        while True:
            lon, lat, _, _ = locate(route.coords, cum, mile)
            before = locate(route.coords, cum, max(0.0, mile - min(2.0, total * 0.04)))
            after = locate(route.coords, cum, min(total, mile + min(2.0, total * 0.04)))
            heading = bearing_deg(before[0], before[1], after[0], after[1])
            raw.append((lon, lat))
            if lateral > 1:
                raw.append(destination(lon, lat, (heading + 90) % 360, lateral))
                raw.append(destination(lon, lat, (heading - 90) % 360, lateral))
            if mile >= total:
                break
            mile = min(total, mile + spacing)
    kept: list[tuple[float, float]] = []
    gap = max(8.0, spacing * 0.45)
    for point in raw:
        if all(haversine_miles(point[0], point[1], other[0], other[1]) > gap for other in kept):
            kept.append(point)
    if len(kept) > 90:
        stride = len(kept) / 90
        kept = [kept[min(len(kept) - 1, int(index * stride))] for index in range(90)]
    return kept


def _corridor_forecast(points, series_by_cell, departure_utc, departure_local, load_lb: float) -> dict:
    times = [(departure_local + timedelta(hours=hour)).isoformat() for hour in range(CORRIDOR_HOURS)]
    payload = []
    for lon, lat in points:
        hours = sample_hours(series_by_cell[_cell(lon, lat)], departure_utc, CORRIDOR_HOURS)
        risks = []
        wind = []
        rain = []
        snow = []
        for hour in hours:
            assessed = assess_conditions(hour["wind"], hour["rain"], hour["snow"], load_lb)
            risks.append(assessed["risk"])
            wind.append(round(hour["wind"], 1))
            rain.append(round(hour["rain"], 3))
            snow.append(round(hour["snow"], 3))
        payload.append(
            {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "risk": risks,
                "wind": wind,
                "rain": rain,
                "snow": snow,
            }
        )
    return {"times": times, "points": payload}

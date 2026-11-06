"""Hourly forecast from Open-Meteo, cached briefly so a load change does not refetch."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .http import UpstreamError, get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CHUNK_SIZE = 40
CACHE_TTL_S = 600
_CACHE: dict[tuple, tuple[float, "HourSeries"]] = {}


@dataclass
class HourSeries:
    times: list[int]
    wind_mph: list[float]
    sustained_mph: list[float]
    gust_mph: list[float | None]
    rain_in: list[float]
    snow_in: list[float]


def fetch_series(points: list[tuple[float, float]], start: datetime, end: datetime) -> list[HourSeries]:
    if not points:
        return []
    start_date = start.astimezone(timezone.utc).date()
    end_date = end.astimezone(timezone.utc).date()
    results: list[HourSeries | None] = [None] * len(points)
    missing: list[int] = []
    now = time.time()
    for index, point in enumerate(points):
        cached = _CACHE.get(_key(point, start_date, end_date))
        if cached and now - cached[0] < CACHE_TTL_S:
            results[index] = cached[1]
        else:
            missing.append(index)

    if missing:
        fetched = _fetch_missing([points[index] for index in missing], start_date.isoformat(), end_date.isoformat())
        if len(fetched) != len(missing):
            raise UpstreamError("Weather service returned the wrong number of locations.")
        for index, series in zip(missing, fetched):
            _CACHE[_key(points[index], start_date, end_date)] = (time.time(), series)
            results[index] = series
        if len(_CACHE) > 2500:
            _CACHE.clear()

    return [series for series in results if series is not None]


def sample_at(series: HourSeries, when: datetime) -> dict:
    target = int(when.timestamp())
    index, clamped = _hour_index(series.times, target)
    return {
        "wind": series.wind_mph[index],
        "sustained": series.sustained_mph[index],
        "gust": series.gust_mph[index],
        "rain": series.rain_in[index],
        "snow": series.snow_in[index],
        "clamped": clamped,
    }


def _fetch_missing(points: list[tuple[float, float]], start_date: str, end_date: str) -> list[HourSeries]:
    chunks = [points[index : index + CHUNK_SIZE] for index in range(0, len(points), CHUNK_SIZE)]
    if len(chunks) == 1:
        return _fetch_chunk(chunks[0], start_date, end_date)
    ordered: list[HourSeries] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for series_list in pool.map(lambda chunk: _fetch_chunk(chunk, start_date, end_date), chunks):
            ordered.extend(series_list)
    return ordered


def _fetch_chunk(points: list[tuple[float, float]], start_date: str, end_date: str) -> list[HourSeries]:
    params = {
        "latitude": ",".join(f"{lat:.4f}" for _lon, lat in points),
        "longitude": ",".join(f"{lon:.4f}" for lon, _lat in points),
        "hourly": "wind_speed_10m,wind_gusts_10m,rain,snowfall",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "timeformat": "unixtime",
        "start_date": start_date,
        "end_date": end_date,
        "cell_selection": "nearest",
    }
    payload = get_json(FORECAST_URL, params, timeout=40, attempts=2)
    locations = _as_locations(payload, len(points))
    return [_parse_location(location) for location in locations]


def _as_locations(payload, count: int) -> list:
    if isinstance(payload, dict) and payload.get("error"):
        raise UpstreamError(str(payload.get("reason") or "Weather request failed"))
    if count == 1:
        if isinstance(payload, list):
            return payload
        return [payload]
    if not isinstance(payload, list):
        raise UpstreamError("Weather service did not return every location.")
    return payload


def _parse_location(location: dict) -> HourSeries:
    if location.get("error"):
        raise UpstreamError(str(location.get("reason") or "Weather request failed"))
    hourly = location.get("hourly") or {}
    units = location.get("hourly_units") or {}
    times = [int(value) for value in hourly.get("time") or []]
    if not times:
        raise UpstreamError("Weather forecast did not include hourly times.")
    speed_unit = str(units.get("wind_speed_10m") or "mp/h")
    gust_unit = str(units.get("wind_gusts_10m") or speed_unit)
    rain_unit = str(units.get("rain") or "inch")
    snow_unit = str(units.get("snowfall") or "inch")
    sustained = [_number(_to_mph(value, speed_unit)) for value in hourly.get("wind_speed_10m") or []]
    gusts = [_optional_mph(value, gust_unit) for value in hourly.get("wind_gusts_10m") or []]
    rain = [_number(_to_inches(value, rain_unit)) for value in hourly.get("rain") or []]
    snow = [_number(_to_inches(value, snow_unit)) for value in hourly.get("snowfall") or []]
    length = len(times)
    sustained = _fit(sustained, length)
    gusts = _fit_optional(gusts, length)
    rain = _fit(rain, length)
    snow = _fit(snow, length)
    wind = [gust if gust is not None else speed for speed, gust in zip(sustained, gusts)]
    return HourSeries(times, wind, sustained, gusts, rain, snow)


def sample_hours(series: HourSeries, start: datetime, hours: int) -> list[dict]:
    rows = []
    for step in range(hours):
        rows.append(sample_at(series, start + timedelta(hours=step)))
    return rows


def _hour_index(times: list[int], target: int) -> tuple[int, bool]:
    if target <= times[0]:
        return 0, target < times[0]
    last_end = times[-1] + 3600
    if target >= last_end:
        return len(times) - 1, True
    lo, hi = 0, len(times) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if times[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo, False


def _key(point: tuple[float, float], start_date, end_date) -> tuple:
    lon, lat = point
    return (round(lat, 3), round(lon, 3), start_date.isoformat(), end_date.isoformat())


def _to_mph(value, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    unit = unit.lower()
    if unit in {"mp/h", "mph"}:
        return number
    if unit in {"km/h", "kmh"}:
        return number * 0.621371
    if unit in {"m/s", "ms"}:
        return number * 2.23694
    return number


def _optional_mph(value, unit: str) -> float | None:
    return _to_mph(value, unit)


def _to_inches(value, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    unit = unit.lower()
    if unit in {"inch", "inches"}:
        return number
    if unit == "cm":
        return number / 2.54
    if unit == "mm":
        return number / 25.4
    return number


def _number(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _fit(values: list[float], length: int) -> list[float]:
    if len(values) < length:
        values = values + [0.0] * (length - len(values))
    return values[:length]


def _fit_optional(values: list[float | None], length: int) -> list[float | None]:
    if len(values) < length:
        values = values + [None] * (length - len(values))
    return values[:length]

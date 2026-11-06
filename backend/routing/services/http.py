from __future__ import annotations

import time

import requests

HEADERS = {"User-Agent": "HaulWeather/1.0 (weather-aware truck routing demo)"}


class UpstreamError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


def get_json(url: str, params: dict | None = None, timeout: float = 30, attempts: int = 3) -> dict | list:
    last_error = "request failed"
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(0.35 * (attempt + 1))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            time.sleep(0.4 * (attempt + 1))
            continue
        if response.status_code >= 400:
            reason = _error_reason(response)
            raise UpstreamError(reason or f"HTTP {response.status_code}")
        return response.json()
    raise UpstreamError(f"Upstream unavailable ({last_error})")


def _error_reason(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:180]
    if isinstance(body, dict):
        return str(body.get("reason") or body.get("message") or body.get("code") or "")
    return ""

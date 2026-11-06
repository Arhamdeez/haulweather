"""Weather risk for a loaded truck, and the route ranking that uses it.

Wind, rain, and snow are scored separately. A checkpoint takes the worst of
the three. Load weight changes only the wind score:

- 55 mph or more is no-travel for every load
- 45–54 mph is no-travel when the load is over 30,000 lb
- 35–44 mph is severe when the load is over 40,000 lb

Shared range edges use the more cautious bin. The top bin is open: rain over
1.00 in/hr and snow over 3.0 in/hr are no-travel, while 1.00 and 3.0 stay severe.
"""

from __future__ import annotations

LEVELS = ("low", "moderate", "high", "severe", "no_travel")
SCORE = {level: index + 1 for index, level in enumerate(LEVELS)}

# Ranking compares these rounded values so the sentence matches the order.
MILE_DECIMALS = 1
SCORE_DECIMALS = 2


def worse(left: str, right: str) -> str:
    return left if SCORE[left] >= SCORE[right] else right


def classify_wind(mph: float, load_lb: float) -> str:
    if mph >= 55:
        return "no_travel"
    if mph >= 45:
        return "no_travel" if load_lb > 30000 else "severe"
    if mph >= 35:
        return "severe" if load_lb > 40000 else "high"
    if mph >= 25:
        return "moderate"
    return "low"


def classify_rain(inches_per_hour: float) -> str:
    if inches_per_hour > 1.0:
        return "no_travel"
    if inches_per_hour >= 0.50:
        return "severe"
    if inches_per_hour >= 0.25:
        return "high"
    if inches_per_hour >= 0.10:
        return "moderate"
    return "low"


def classify_snow(inches_per_hour: float) -> str:
    if inches_per_hour > 3.0:
        return "no_travel"
    if inches_per_hour >= 2.0:
        return "severe"
    if inches_per_hour >= 1.0:
        return "high"
    if inches_per_hour >= 0.5:
        return "moderate"
    return "low"


def label_for_average(score: float) -> str:
    if score < 1.5:
        return "low"
    if score < 2.5:
        return "moderate"
    if score < 3.5:
        return "high"
    if score < 4.5:
        return "severe"
    return "no_travel"


def assess_conditions(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> dict:
    wind_level = classify_wind(wind_mph, load_lb)
    wind_without_load = classify_wind(wind_mph, 0)
    rain_level = classify_rain(rain_in_hr)
    snow_level = classify_snow(snow_in_hr)
    overall = wind_level
    for level in (rain_level, snow_level):
        overall = worse(overall, level)

    drivers: list[str] = []
    if overall != "low":
        if wind_level == overall:
            drivers.append("wind")
        if rain_level == overall:
            drivers.append("rain")
        if snow_level == overall:
            drivers.append("snow")

    load_note = None
    if wind_level != wind_without_load:
        if wind_without_load == "severe" and wind_level == "no_travel":
            load_note = "Load over 30,000 lb makes 45–54 mph wind no-travel."
        elif wind_without_load == "high" and wind_level == "severe":
            load_note = "Load over 40,000 lb makes 35–44 mph wind severe."

    return {
        "wind_level": wind_level,
        "rain_level": rain_level,
        "snow_level": snow_level,
        "risk": overall,
        "drivers": drivers,
        "load_note": load_note,
    }


def ownership_spans(checkpoints: list[dict]) -> list[dict]:
    """Give each checkpoint the miles halfway toward its neighbors.

    A sample stands for the stretch around it, so one severe reading counts
    about one sample interval, not both adjacent gaps.
    """
    count = len(checkpoints)
    if count == 0:
        return []
    spans = []
    for index, checkpoint in enumerate(checkpoints):
        if index == 0:
            left = checkpoint["mile"]
        else:
            left = (checkpoints[index - 1]["mile"] + checkpoint["mile"]) / 2
        if index == count - 1:
            right = checkpoint["mile"]
        else:
            right = (checkpoint["mile"] + checkpoints[index + 1]["mile"]) / 2
        spans.append(
            {
                "from_mile": left,
                "to_mile": right,
                "risk": checkpoint["risk"],
                "checkpoint_index": index,
            }
        )
    return spans


def summarize(checkpoints: list[dict]) -> dict:
    spans = ownership_spans(checkpoints)
    buckets = {level: 0.0 for level in LEVELS}
    weighted = 0.0
    total = 0.0
    for span in spans:
        distance = max(0.0, span["to_mile"] - span["from_mile"])
        buckets[span["risk"]] += distance
        weighted += distance * SCORE[span["risk"]]
        total += distance
    if total <= 0 and checkpoints:
        score = float(SCORE[checkpoints[0]["risk"]])
    elif total <= 0:
        score = 1.0
    else:
        score = weighted / total
    rounded_score = round(score, SCORE_DECIMALS)
    return {
        "miles": {level: round(buckets[level], MILE_DECIMALS) for level in LEVELS},
        "total_mi": round(total, MILE_DECIMALS),
        "average_score": rounded_score,
        "average_level": label_for_average(rounded_score),
    }


def recommendation_sort_key(summary: dict, duration_s: float) -> tuple:
    """Lower is better.

    No-travel miles sort ahead of severe miles. A route with no-travel weather
    must not beat a route that is only severe, or the ranking would hide the
    worst condition. After that the order is the one in the brief: fewest
    severe miles, fewest high miles, lowest average risk, shortest time.
    """
    miles = summary["miles"]
    return (
        round(miles["no_travel"], MILE_DECIMALS),
        round(miles["severe"], MILE_DECIMALS),
        round(miles["high"], MILE_DECIMALS),
        round(summary["average_score"], SCORE_DECIMALS),
        round(duration_s / 60.0),
    )


def format_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    hours, mins = divmod(max(0, minutes), 60)
    if hours == 0:
        return f"{mins} min"
    return f"{hours} h {mins} min"


def _joined(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def explain_recommendation(ranked: list[dict]) -> str:
    winner = ranked[0]
    others = ranked[1:]
    if not others:
        text = "Only one distinct driving route was found, so it is the only option."
    else:
        text = _deciding_sentence(winner, others)
        fastest = min(ranked, key=lambda route: (route["duration_s"], route["distance_mi"]))
        if fastest["id"] == winner["id"]:
            if "travel time" not in text:
                text += " It is also the shortest trip."
        else:
            delta = winner["duration_s"] - fastest["duration_s"]
            text += f" It adds {format_duration(delta)} compared with {fastest['name']}."

    if winner["summary"]["miles"]["no_travel"] > 0:
        if all(route["summary"]["miles"]["no_travel"] > 0 for route in ranked):
            text += " Every option still crosses no-travel weather, so this is the least exposure, not an all-clear."
        else:
            text += " This route still includes no-travel weather, so delay it if you can."
    return text


def _deciding_sentence(winner: dict, others: list[dict]) -> str:
    criteria = (
        ("no_travel", "no-travel miles", lambda route: route["summary"]["miles"]["no_travel"], lambda value: f"{value:.1f} mi"),
        ("severe", "severe miles", lambda route: route["summary"]["miles"]["severe"], lambda value: f"{value:.1f} mi"),
        ("high", "high miles", lambda route: route["summary"]["miles"]["high"], lambda value: f"{value:.1f} mi"),
        ("average", "average risk", lambda route: route["summary"]["average_score"], lambda value: f"{value:.2f}"),
        ("duration", "travel time", lambda route: round(route["duration_s"] / 60.0), lambda value: format_duration(value * 60)),
    )
    tied: list[str] = []
    for _key, label, read, fmt in criteria:
        winner_value = read(winner)
        other_values = [read(route) for route in others]
        if all(value == winner_value for value in other_values):
            tied.append(label)
            continue
        versus = _joined(
            [f"{route['name']} {fmt(read(route))}" for route in others]
        )
        quality = "the shortest travel time" if _key == "duration" else f"the lowest {label}"
        if tied:
            opening = f"Recommended because {_joined(tied)} are tied, and it has {quality}"
        else:
            opening = f"Recommended because it has {quality}"
        return f"{opening} ({fmt(winner_value)} versus {versus})."
    return "Recommended because the routes match on weather and time."

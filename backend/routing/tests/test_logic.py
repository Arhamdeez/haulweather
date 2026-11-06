import json
from datetime import datetime, timezone

from django.test import SimpleTestCase

from routing.services.geo import (
    METERS_PER_MILE,
    destination,
    haversine_miles,
    mean_separation_miles,
    sample_mileposts,
    seconds_at,
)
from routing.services.planner import PlanError, evaluate_route, parse_request
from routing.services.risk import (
    assess_conditions,
    classify_rain,
    classify_snow,
    classify_wind,
    explain_recommendation,
    recommendation_sort_key,
    summarize,
)
from routing.services.routing_api import RouteOption, name_routes
from routing.services.weather import HourSeries, sample_at


def _summary(no_travel, severe, high, average):
    return {
        "miles": {
            "no_travel": no_travel,
            "severe": severe,
            "high": high,
            "moderate": 0,
            "low": 0,
        },
        "average_score": average,
    }


def _ranked(name, no_travel, severe, high, average, duration):
    return {
        "id": name,
        "name": name,
        "duration_s": duration,
        "distance_mi": 100,
        "summary": _summary(no_travel, severe, high, average),
    }


def _northbound(miles: float, segments: int, durations: list[float] | None = None) -> RouteOption:
    coords = [(-90.0, 40.0)]
    for index in range(1, segments + 1):
        coords.append(destination(-90.0, 40.0, 0, miles * index / segments))
    if durations is None:
        durations = [100.0] * segments
    return RouteOption(
        coords=coords,
        distance_m=miles * METERS_PER_MILE,
        duration_s=sum(durations),
        annotation_distance=[miles * METERS_PER_MILE / segments] * segments,
        annotation_duration=durations,
    )


class ThresholdTests(SimpleTestCase):
    def test_wind_bins_and_load_rules(self):
        cases = [
            (24.9, 80000, "low"),
            (25, 1000, "moderate"),
            (34.9, 80000, "moderate"),
            (35, 40000, "high"),
            (44.9, 40000, "high"),
            (35, 40000.1, "severe"),
            (44.9, 80000, "severe"),
            (45, 30000, "severe"),
            (54.9, 30000, "severe"),
            (45, 30000.1, "no_travel"),
            (54.9, 80000, "no_travel"),
            (55, 1, "no_travel"),
            (70, 1000, "no_travel"),
        ]
        for mph, load, expected in cases:
            self.assertEqual(classify_wind(mph, load), expected, f"{mph} mph at {load} lb")

    def test_rain_bins(self):
        self.assertEqual(classify_rain(0.099), "low")
        self.assertEqual(classify_rain(0.10), "moderate")
        self.assertEqual(classify_rain(0.249), "moderate")
        self.assertEqual(classify_rain(0.25), "high")
        self.assertEqual(classify_rain(0.499), "high")
        self.assertEqual(classify_rain(0.50), "severe")
        self.assertEqual(classify_rain(1.0), "severe")
        self.assertEqual(classify_rain(1.001), "no_travel")

    def test_snow_bins(self):
        self.assertEqual(classify_snow(0.49), "low")
        self.assertEqual(classify_snow(0.5), "moderate")
        self.assertEqual(classify_snow(0.99), "moderate")
        self.assertEqual(classify_snow(1.0), "high")
        self.assertEqual(classify_snow(1.99), "high")
        self.assertEqual(classify_snow(2.0), "severe")
        self.assertEqual(classify_snow(3.0), "severe")
        self.assertEqual(classify_snow(3.01), "no_travel")

    def test_worst_condition_wins_and_load_note(self):
        quiet = assess_conditions(10, 0.4, 0.1, 20000)
        self.assertEqual(quiet["risk"], "high")
        self.assertEqual(quiet["drivers"], ["rain"])

        heavy = assess_conditions(36, 0, 0, 45000)
        self.assertEqual(heavy["risk"], "severe")
        self.assertIn("40,000", heavy["load_note"])

        light = assess_conditions(36, 0, 0, 20000)
        self.assertEqual(light["risk"], "high")
        self.assertIsNone(light["load_note"])


class RankingTests(SimpleTestCase):
    def test_no_travel_miles_outrank_severe_miles(self):
        clear_of_block = recommendation_sort_key(_summary(0, 40, 0, 3), 99999)
        blocked = recommendation_sort_key(_summary(5, 0, 0, 1.2), 100)
        self.assertLess(clear_of_block, blocked)

    def test_spec_order_after_no_travel(self):
        fewer_severe = recommendation_sort_key(_summary(0, 1, 30, 3), 9000)
        more_severe = recommendation_sort_key(_summary(0, 8, 0, 1.2), 1000)
        self.assertLess(fewer_severe, more_severe)

        fewer_high = recommendation_sort_key(_summary(0, 2, 4, 3), 9000)
        more_high = recommendation_sort_key(_summary(0, 2, 15, 1.4), 1000)
        self.assertLess(fewer_high, more_high)

        lower_average = recommendation_sort_key(_summary(0, 2, 4, 1.4), 9000)
        higher_average = recommendation_sort_key(_summary(0, 2, 4, 2.2), 1000)
        self.assertLess(lower_average, higher_average)

        faster = recommendation_sort_key(_summary(0, 2, 4, 2), 1000)
        slower = recommendation_sort_key(_summary(0, 2, 4, 2), 5000)
        self.assertLess(faster, slower)

    def test_severe_sample_owns_about_one_interval(self):
        checkpoints = [
            {"mile": 0, "risk": "low"},
            {"mile": 25, "risk": "low"},
            {"mile": 50, "risk": "severe"},
            {"mile": 75, "risk": "low"},
            {"mile": 100, "risk": "low"},
        ]
        summary = summarize(checkpoints)
        self.assertEqual(summary["miles"]["severe"], 25.0)
        self.assertEqual(summary["miles"]["low"], 75.0)
        self.assertEqual(summary["total_mi"], 100.0)

    def test_explanation_names_the_deciding_rule(self):
        ranked = [
            _ranked("Northern", 0, 0, 10, 1.8, 8000),
            _ranked("Fastest", 0, 12, 4, 2.2, 6000),
            _ranked("Southern", 0, 6, 8, 2.0, 7000),
        ]
        text = explain_recommendation(ranked)
        self.assertIn("lowest severe miles (0.0 mi versus", text)
        self.assertIn("Fastest 12.0 mi", text)
        self.assertIn("Southern 6.0 mi", text)
        self.assertIn("adds", text)


class GeometryTests(SimpleTestCase):
    def test_one_degree_of_latitude_is_about_69_miles(self):
        distance = haversine_miles(-87.63, 41.88, -87.63, 42.88)
        self.assertGreater(distance, 68.5)
        self.assertLess(distance, 69.5)

    def test_destination_inverts_haversine(self):
        lon, lat = destination(-90, 40, 0, 100)
        self.assertAlmostEqual(haversine_miles(-90, 40, lon, lat), 100, delta=0.05)
        self.assertGreater(lat, 40)

    def test_mileposts_keep_both_ends(self):
        self.assertEqual(sample_mileposts(100, 25), [0, 25, 50, 75, 100])
        self.assertEqual(sample_mileposts(30, 25), [0, 25, 30])
        self.assertEqual(sample_mileposts(26, 25), [0, 26])
        self.assertEqual(sample_mileposts(10, 50), [0, 10])

    def test_seconds_follow_the_segment_that_contains_the_point(self):
        self.assertEqual(seconds_at([100, 100, 700], 1, 1, 0), 200)
        self.assertEqual(seconds_at(None, 0, 0, 42), 42)

    def test_identical_geometry_has_no_separation(self):
        route = _northbound(80, 8)
        self.assertLess(mean_separation_miles(route.coords, route.coords), 0.2)


class RouteBuildTests(SimpleTestCase):
    def test_constant_wind_and_load_rule_cover_the_whole_trip(self):
        route = _northbound(100, 4)
        departure = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)

        def calm(_lon, _lat, _eta):
            return {"wind": 36, "sustained": 30, "gust": 36, "rain": 0, "snow": 0, "clamped": False}

        light = evaluate_route(route, interval=25, departure=departure, load_lb=20000, weather_at=calm)
        heavy = evaluate_route(route, interval=25, departure=departure, load_lb=45000, weather_at=calm)
        self.assertEqual(len(light["checkpoints"]), 5)
        self.assertEqual(light["checkpoints"][0]["mile"], 0)
        self.assertEqual(light["checkpoints"][-1]["mile"], 100)
        self.assertEqual(light["summary"]["miles"]["high"], 100.0)
        self.assertEqual(heavy["summary"]["miles"]["severe"], 100.0)
        self.assertTrue(heavy["checkpoints"][0]["load_note"])
        self.assertEqual(light["checkpoints"][0]["risk"], "high")
        self.assertEqual(heavy["checkpoints"][0]["risk"], "severe")

    def test_eta_uses_road_time_not_a_flat_fraction_when_speed_changes(self):
        durations = [100, 100, 100, 700]
        route = _northbound(100, 4, durations)
        departure = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)

        def calm(_lon, _lat, _eta):
            return {"wind": 5, "sustained": 5, "gust": 5, "rain": 0, "snow": 0, "clamped": False}

        built = evaluate_route(route, interval=25, departure=departure, load_lb=10000, weather_at=calm)
        halfway = next(point for point in built["checkpoints"] if point["mile"] == 50)
        eta = datetime.fromisoformat(halfway["eta"])
        elapsed = (eta - departure).total_seconds()
        self.assertAlmostEqual(elapsed, 200, delta=20)
        self.assertLess(elapsed, 500)

    def test_checkpoint_times_increase(self):
        route = _northbound(75, 3)
        departure = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)

        def calm(_lon, _lat, _eta):
            return {"wind": 0, "sustained": 0, "gust": 0, "rain": 0, "snow": 0, "clamped": False}

        built = evaluate_route(route, interval=25, departure=departure, load_lb=10000, weather_at=calm)
        etas = [datetime.fromisoformat(point["eta"]) for point in built["checkpoints"]]
        self.assertEqual(etas, sorted(etas))
        self.assertGreater(etas[-1], etas[0])

    def test_names_follow_the_middle_of_each_path(self):
        fastest = RouteOption([(-91, 41), (-90, 41), (-89, 41)], 1000, 100)
        northern = RouteOption([(-91, 43), (-90, 43), (-89, 43)], 1200, 140)
        southern = RouteOption([(-91, 39), (-90, 39), (-89, 39)], 1300, 160)
        name_routes([southern, fastest, northern])
        self.assertEqual(fastest.name, "Fastest")
        self.assertEqual(northern.name, "Northern")
        self.assertEqual(southern.name, "Southern")


class WeatherIndexTests(SimpleTestCase):
    def test_sample_uses_the_hour_that_contains_the_arrival(self):
        series = HourSeries(
            times=[1_000_000, 1_003_600, 1_007_200],
            wind_mph=[10, 40, 55],
            sustained_mph=[8, 30, 50],
            gust_mph=[10, 40, 55],
            rain_in=[0, 0.3, 0],
            snow_in=[0, 0, 1.2],
        )
        inside = sample_at(series, datetime.fromtimestamp(1_000_100, tz=timezone.utc))
        self.assertEqual(inside["wind"], 10)
        self.assertFalse(inside["clamped"])
        later = sample_at(series, datetime.fromtimestamp(1_004_000, tz=timezone.utc))
        self.assertEqual(later["wind"], 40)
        self.assertEqual(later["rain"], 0.3)
        before = sample_at(series, datetime.fromtimestamp(500, tz=timezone.utc))
        self.assertTrue(before["clamped"])
        self.assertEqual(before["wind"], 10)


class RequestTests(SimpleTestCase):
    def _body(self, **overrides):
        body = {
            "origin": "Chicago, IL",
            "destination": "Indianapolis, IN",
            "departure": "2026-11-07T07:00",
            "timezone": "America/Chicago",
            "load_lb": 44000,
            "sample_miles": 25,
        }
        body.update(overrides)
        return body

    def test_parses_a_local_departure(self):
        parsed = parse_request(self._body())
        self.assertEqual(parsed["sample_miles"], 25)
        self.assertEqual(parsed["load_lb"], 44000)
        self.assertEqual(parsed["departure"].tzinfo, timezone.utc)
        self.assertEqual(parsed["departure"].hour, 13)

    def test_api_rejects_a_bad_interval(self):
        response = self.client.post(
            "/api/plan",
            data=json.dumps(self._body(sample_miles=15)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("10, 25, or 50", response.json()["error"])

    def test_api_rejects_a_missing_place(self):
        response = self.client.post(
            "/api/plan",
            data=json.dumps(self._body(origin="")),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_bad_departure_raises(self):
        with self.assertRaises(PlanError):
            parse_request(self._body(departure="tomorrow morning"))

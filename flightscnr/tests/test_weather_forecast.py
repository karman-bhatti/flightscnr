"""Tests for forecast day rollover after midnight."""

import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from display.round_touch import weather_data


def _interval(day: date) -> dict:
    start = datetime.combine(day, datetime.min.time()).isoformat()
    return {
        "startTime": start,
        "values": {
            "temperatureMin": 50,
            "temperatureMax": 72,
            "weatherCodeFullDay": 1000,
            "precipitationProbabilityAvg": 0,
        },
    }


class TestForecastDayRollover:
    def test_parse_days_skips_yesterday(self, monkeypatch):
        today = date(2026, 7, 7)
        monkeypatch.setattr(weather_data, "_today", lambda: today)

        intervals = [
            _interval(today - timedelta(days=1)),
            _interval(today),
            _interval(today + timedelta(days=1)),
            _interval(today + timedelta(days=2)),
        ]
        days = weather_data._parse_days(intervals, max_days=3)

        assert len(days) == 3
        assert days[0]["label"] == "Today"
        assert days[1]["label"] == "Wed"
        assert days[2]["label"] == "Thu"

    def test_refresh_invalidates_on_date_change(self, monkeypatch):
        weather_data.invalidate_cache()
        day_a = date(2026, 7, 6)
        day_b = date(2026, 7, 7)
        calls = {"day": day_a}

        monkeypatch.setattr(weather_data, "_today", lambda: calls["day"])
        monkeypatch.setattr(
            weather_data,
            "grab_temperature_and_humidity",
            lambda: (70, 40),
            raising=False,
        )

        def fake_grab_forecast(_tag):
            return [_interval(calls["day"])]

        monkeypatch.setitem(
            sys.modules,
            "utilities.temperature",
            type(sys)("utilities.temperature"),
        )
        import utilities.temperature as temp_mod

        temp_mod.grab_forecast = fake_grab_forecast
        temp_mod.grab_temperature_and_humidity = lambda: (70, 40)

        def unit_symbol():
            return "F"

        def temperature_units():
            return "imperial"

        monkeypatch.setitem(sys.modules, "weather_prefs", type(sys)("weather_prefs"))
        import weather_prefs

        weather_prefs.temperature_units = temperature_units
        weather_prefs.unit_symbol = unit_symbol

        first = weather_data.refresh(force=True)
        assert first is not None
        assert first["days"][0]["label"] == "Today"

        calls["day"] = day_b
        second = weather_data.refresh(force=False)
        assert second is not None
        assert second["days"][0]["label"] == "Today"

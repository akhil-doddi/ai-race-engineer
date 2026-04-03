"""
tests/test_telemetry.py

Tests for TelemetrySimulator and build_race_state.

These tests do NOT start the background thread (no time.sleep calls).
They test the pure logic: snapshot shape, value bounds, and state normalisation.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.telemetry.simulator import TelemetrySimulator
from src.race_state.state_manager import build_race_state


# ---------------------------------------------------------------------------
# TelemetrySimulator — snapshot shape and bounds
# ---------------------------------------------------------------------------

class TestTelemetrySimulator:

    def setup_method(self):
        """Create a fresh simulator. Does NOT call .start() — no background thread."""
        self.sim = TelemetrySimulator()

    def test_get_snapshot_returns_dict(self):
        assert isinstance(self.sim.get_snapshot(), dict)

    def test_snapshot_has_required_keys(self):
        required = {
            "lap", "total_laps", "laps_remaining", "position",
            "gap_ahead", "gap_behind",
            "tire_compound", "tire_wear", "tire_age_laps",
            "fuel", "fuel_per_lap",
            "last_lap_time", "best_lap_time", "lap_delta",
            "speed", "gear", "drs",
        }
        snapshot = self.sim.get_snapshot()
        for key in required:
            assert key in snapshot, f"Missing key in snapshot: {key}"

    def test_tire_wear_starts_at_100(self):
        assert self.sim.get_snapshot()["tire_wear"] == 100.0

    def test_fuel_starts_at_100(self):
        assert self.sim.get_snapshot()["fuel"] == 100.0

    def test_position_within_valid_range(self):
        pos = self.sim.get_snapshot()["position"]
        assert 1 <= pos <= 20, f"position out of range: {pos}"

    def test_tire_compound_is_valid(self):
        compound = self.sim.get_snapshot()["tire_compound"]
        assert compound in ("Soft", "Medium", "Hard"), f"Unexpected compound: {compound}"

    def test_lap_starts_at_1(self):
        assert self.sim.get_snapshot()["lap"] == 1

    def test_snapshot_returns_copy_not_reference(self):
        """Modifying the snapshot must not mutate internal simulator state."""
        snap = self.sim.get_snapshot()
        snap["fuel"] = 0.0
        assert self.sim.get_snapshot()["fuel"] == 100.0

    def test_format_laptime_produces_correct_string(self):
        """92.5 seconds → '1:32.500'"""
        result = self.sim._format_laptime(92.5)
        assert result == "1:32.500", f"Unexpected format: {result}"

    def test_parse_laptime_round_trips(self):
        """Parsing the formatted time should return the original seconds."""
        original = 91.456
        formatted = self.sim._format_laptime(original)
        parsed = self.sim._parse_laptime(formatted)
        assert abs(parsed - original) < 0.001, f"Round-trip error: {parsed} vs {original}"

    def test_calculate_lap_time_returns_float(self):
        result = self.sim._calculate_lap_time()
        assert isinstance(result, float)
        assert result > 0

    def test_calculate_lap_time_increases_with_tyre_wear(self):
        """A car with 50% tyre life should lap slower than one with 100%."""
        self.sim.data["tire_wear"] = 100.0
        time_fresh = self.sim._calculate_lap_time()

        self.sim.data["tire_wear"] = 50.0
        time_worn = self.sim._calculate_lap_time()

        # Worn tyres add ~1.5s of penalty — even with variance, should be slower
        # We run this assertion 20 times to beat random variance
        fresher_wins = 0
        for _ in range(20):
            self.sim.data["tire_wear"] = 100.0
            t_fresh = self.sim._calculate_lap_time()
            self.sim.data["tire_wear"] = 50.0
            t_worn = self.sim._calculate_lap_time()
            if t_worn > t_fresh:
                fresher_wins += 1
        assert fresher_wins >= 15, "Worn tyres should almost always produce slower laps"


# ---------------------------------------------------------------------------
# build_race_state — normalisation and derived fields
# ---------------------------------------------------------------------------

class TestBuildRaceState:

    def _valid_raw(self, overrides=None):
        """Return a valid raw telemetry dict matching simulator output format."""
        raw = {
            "lap": 10,
            "total_laps": 58,
            "laps_remaining": 48,
            "position": 3,
            "gap_ahead": 1.2,
            "gap_behind": 3.4,
            "tire_compound": "Medium",
            "tire_wear": 72.5,
            "tire_age_laps": 10,
            "fuel": 80.0,
            "fuel_per_lap": 1.9,
            "last_lap_time": "1:32.456",
            "best_lap_time": "1:31.800",
            "lap_delta": "+0.656",
            "speed": 250,
            "gear": 6,
            "drs": False,
        }
        if overrides:
            raw.update(overrides)
        return raw

    def test_returns_dict(self):
        assert isinstance(build_race_state(self._valid_raw()), dict)

    def test_all_required_keys_present(self):
        state = build_race_state(self._valid_raw())
        required = {
            "lap", "total_laps", "laps_remaining", "position",
            "gap_ahead", "gap_behind",
            "tire_compound", "tire_wear", "tire_age_laps",
            "fuel", "fuel_per_lap",
            "last_lap_time", "best_lap_time", "lap_delta",
            "speed", "gear", "drs",
        }
        for key in required:
            assert key in state, f"Missing key in race_state: {key}"

    def test_data_types_correct(self):
        state = build_race_state(self._valid_raw())
        assert isinstance(state["lap"], int)
        assert isinstance(state["laps_remaining"], int)
        assert isinstance(state["tire_wear"], float)
        assert isinstance(state["fuel"], float)
        assert isinstance(state["position"], int)
        assert isinstance(state["drs"], bool)
        assert isinstance(state["last_lap_time"], str)

    def test_handles_missing_keys_with_defaults(self):
        """build_race_state must not crash if optional keys are absent."""
        state = build_race_state({})  # completely empty dict
        assert state["lap"] == 1
        assert state["tire_compound"] == "Medium"
        assert state["fuel"] == 100.0

    def test_tire_wear_preserved(self):
        state = build_race_state(self._valid_raw({"tire_wear": 65.3}))
        assert state["tire_wear"] == 65.3

    def test_position_within_bounds(self):
        state = build_race_state(self._valid_raw({"position": 15}))
        assert state["position"] == 15

    def test_drs_false_by_default(self):
        state = build_race_state({})
        assert state["drs"] is False

    def test_drs_true_when_set(self):
        state = build_race_state(self._valid_raw({"drs": True}))
        assert state["drs"] is True


# ---------------------------------------------------------------------------
# get_event — event detection logic
# ---------------------------------------------------------------------------

class TestGetEvent:

    def setup_method(self):
        from src.events.event_detector import get_event
        self.get_event = get_event

    def _race_state(self, overrides=None):
        """Base race state: healthy car, mid-race."""
        state = {
            "lap": 20,
            "total_laps": 58,
            "laps_remaining": 38,
            "position": 5,
            "gap_ahead": 2.0,
            "gap_behind": 3.0,
            "tire_compound": "Medium",
            "tire_wear": 80.0,
            "tire_age_laps": 20,
            "fuel": 60.0,
            "fuel_per_lap": 1.9,
            "last_lap_time": "1:32.000",
            "best_lap_time": "1:31.500",
            "lap_delta": "+0.500",
            "speed": 250,
            "gear": 6,
            "drs": False,
        }
        if overrides:
            state.update(overrides)
        return state

    def test_healthy_car_returns_green(self):
        event = self.get_event(self._race_state())
        assert event["urgency"] == "green"

    def test_critical_tyre_returns_red(self):
        event = self.get_event(self._race_state({"tire_wear": 10.0, "tire_age_laps": 30}))
        assert event["urgency"] == "red"
        assert event["should_pit"] is True

    def test_low_tyre_returns_yellow(self):
        event = self.get_event(self._race_state({"tire_wear": 25.0, "tire_age_laps": 20}))
        assert event["urgency"] in ("yellow", "red")

    def test_end_of_race_overrides_pit_recommendation(self):
        """With 3 laps left and tyre life >20%, engineer should say bring it home."""
        event = self.get_event(self._race_state({
            "laps_remaining": 3,
            "tire_wear": 35.0,
            "tire_age_laps": 20,
        }))
        assert event["should_pit"] is False
        assert event["urgency"] == "green"

    def test_event_always_has_required_keys(self):
        event = self.get_event(self._race_state())
        for key in ("urgency", "should_pit", "reason", "laps_left_on_tyre", "fuel_laps_remaining"):
            assert key in event, f"Missing key: {key}"

    def test_undercut_risk_flagged_when_close(self):
        """Car within 1.5s behind + yellow urgency → undercut should be mentioned."""
        event = self.get_event(self._race_state({
            "tire_wear": 25.0,
            "tire_age_laps": 20,
            "gap_behind": 1.0,
            "laps_remaining": 20,
        }))
        assert "undercut" in event["reason"].lower()

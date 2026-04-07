"""
tests/test_telemetry.py

Tests for TelemetrySimulator, build_race_state, PitStateMachine, and TelemetryController.

These tests do NOT start the background thread (no time.sleep calls).
They test the pure logic: snapshot shape, value bounds, state normalisation,
pit FSM transitions, and controller override application.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.telemetry.simulator import TelemetrySimulator
from src.race_state.state_manager import build_race_state
from src.telemetry.pit_state_machine import PitStateMachine, PitState
from src.telemetry.telemetry_controller import TelemetryController


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
        for key in ("urgency", "should_pit", "reason", "laps_left_on_tyre",
                    "fuel_laps_remaining", "race_phase", "endgame_override"):
            assert key in event, f"Missing key: {key}"

    def test_race_phase_mid_during_normal_race(self):
        """Mid-race state should return phase 'mid'."""
        event = self.get_event(self._race_state())   # lap 20/58, 38 remaining
        assert event["race_phase"] == "mid"

    def test_race_phase_endgame_in_final_10_laps(self):
        """With 8 laps left the phase should be 'endgame'."""
        event = self.get_event(self._race_state({"laps_remaining": 8}))
        assert event["race_phase"] == "endgame"

    def test_endgame_suppresses_pit_with_drivable_tyre(self):
        """Endgame + tyre above ENDGAME_CRITICAL_TYRE → should_pit suppressed."""
        event = self.get_event(self._race_state({
            "laps_remaining": 8,
            "tire_wear": 30.0,       # worn but above 15% critical threshold
            "tire_age_laps": 25,
        }))
        assert event["should_pit"] is False
        assert event["endgame_override"] is True
        assert event["race_phase"] == "endgame"

    def test_endgame_does_not_suppress_critical_tyre(self):
        """Endgame + tyre BELOW ENDGAME_CRITICAL_TYRE → pit still recommended."""
        event = self.get_event(self._race_state({
            "laps_remaining": 8,
            "tire_wear": 10.0,       # below 15% critical threshold
            "tire_age_laps": 30,
        }))
        assert event["should_pit"] is True
        assert event["endgame_override"] is False

    def test_endgame_does_not_suppress_sc_pit(self):
        """Safety car in endgame must NOT be suppressed — free stop remains valid."""
        event = self.get_event(self._race_state({
            "laps_remaining": 9,
            "track_status": "safety_car",
            "tire_wear": 45.0,
            "tire_age_laps": 10,
        }))
        assert event["safety_car"] is True
        assert event["should_pit"] is True
        assert event["endgame_override"] is False

    def test_endgame_override_false_when_no_pit_would_have_been_called(self):
        """endgame_override is only True when a pit was actively suppressed."""
        event = self.get_event(self._race_state({
            "laps_remaining": 8,
            "tire_wear": 85.0,       # healthy tyre — no pit needed anyway
            "tire_age_laps": 5,
        }))
        assert event["endgame_override"] is False

    def test_undercut_risk_flagged_when_close(self):
        """Car within 1.5s behind + yellow urgency → undercut should be mentioned."""
        event = self.get_event(self._race_state({
            "tire_wear": 25.0,
            "tire_age_laps": 20,
            "gap_behind": 1.0,
            "laps_remaining": 20,
        }))
        assert "undercut" in event["reason"].lower()

    def test_safety_car_triggers_should_pit_with_old_tyres(self):
        """SC deployed + tyre age >= SC_MIN_TYRE_AGE → should_pit=True."""
        event = self.get_event(self._race_state({
            "track_status": "safety_car",
            "tire_age_laps": 10,   # well above SC_MIN_TYRE_AGE (5)
            "laps_remaining": 20,
        }))
        assert event["safety_car"] is True
        assert event["should_pit"] is True

    def test_safety_car_does_not_trigger_with_fresh_tyres(self):
        """SC deployed but tyre age < SC_MIN_TYRE_AGE → should_pit=False."""
        event = self.get_event(self._race_state({
            "track_status": "safety_car",
            "tire_age_laps": 2,    # below SC_MIN_TYRE_AGE (5)
            "laps_remaining": 20,
        }))
        assert event["safety_car"] is True
        assert event["should_pit"] is False

    def test_green_flag_safety_car_false(self):
        """No safety car on green flag conditions."""
        event = self.get_event(self._race_state({"track_status": "green"}))
        assert event["safety_car"] is False


# ---------------------------------------------------------------------------
# PitStateMachine — FSM state transitions
# ---------------------------------------------------------------------------

class TestPitStateMachine:

    def setup_method(self):
        self.fsm = PitStateMachine()

    def test_initial_state_is_racing(self):
        assert self.fsm.state == PitState.RACING

    def test_is_pitting_false_initially(self):
        assert self.fsm.is_pitting is False

    def test_trigger_pit_returns_true_from_racing(self):
        result = self.fsm.trigger_pit("Medium")
        assert result is True

    def test_trigger_pit_changes_state(self):
        self.fsm.trigger_pit("Medium")
        assert self.fsm.state == PitState.PIT_ENTRY

    def test_is_pitting_true_after_trigger(self):
        self.fsm.trigger_pit("Medium")
        assert self.fsm.is_pitting is True

    def test_duplicate_trigger_returns_false(self):
        """Triggering a pit while already pitting must be rejected."""
        self.fsm.trigger_pit("Medium")
        result = self.fsm.trigger_pit("Medium")  # duplicate
        assert result is False

    def test_compound_rotation_medium_to_hard(self):
        self.fsm.trigger_pit("Medium")
        assert self.fsm.new_compound == "Hard"

    def test_compound_rotation_soft_to_medium(self):
        self.fsm.trigger_pit("Soft")
        assert self.fsm.new_compound == "Medium"

    def test_compound_rotation_hard_to_medium(self):
        self.fsm.trigger_pit("Hard")
        assert self.fsm.new_compound == "Medium"

    def test_get_overrides_empty_in_racing(self):
        """No overrides when car is racing normally."""
        assert self.fsm.get_overrides() == {}

    def test_get_overrides_has_tyre_fields_after_trigger(self):
        """Once pit sequence starts, overrides provide fresh tyre data."""
        self.fsm.trigger_pit("Medium")
        # Manually advance to PIT_STOP (where overrides are non-empty)
        self.fsm.state = PitState.PIT_STOP
        overrides = self.fsm.get_overrides()
        assert "tire_wear" in overrides
        assert "tire_age_laps" in overrides
        assert "tire_compound" in overrides
        assert overrides["tire_wear"] == 100.0
        assert overrides["tire_age_laps"] == 0


# ---------------------------------------------------------------------------
# TelemetryController — override application and pit delegation
# ---------------------------------------------------------------------------

class TestTelemetryController:

    def _make_mock_source(self, snapshot=None):
        """Create a mock telemetry source with a fixed snapshot."""
        if snapshot is None:
            snapshot = {
                "lap": 10,
                "total_laps": 58,
                "laps_remaining": 48,
                "position": 5,
                "gap_ahead": 1.5,
                "gap_behind": 2.0,
                "tire_compound": "Medium",
                "tire_wear": 72.0,
                "tire_age_laps": 10,
                "fuel": 70.0,
                "fuel_per_lap": 1.9,
                "last_lap_time": "1:32.000",
                "best_lap_time": "1:31.500",
                "lap_delta": "+0.500",
                "speed": 250,
                "gear": 6,
                "drs": False,
            }
        mock = MagicMock()
        mock.get_snapshot.return_value = dict(snapshot)
        return mock

    def test_get_snapshot_returns_dict(self):
        source = self._make_mock_source()
        controller = TelemetryController(source)
        assert isinstance(controller.get_snapshot(), dict)

    def test_raw_data_passes_through_when_not_pitting(self):
        """When no pit is active, controller returns raw data unchanged."""
        source = self._make_mock_source()
        controller = TelemetryController(source)
        snap = controller.get_snapshot()
        assert snap["tire_compound"] == "Medium"
        assert snap["tire_wear"] == 72.0

    def test_is_pitting_false_initially(self):
        source = self._make_mock_source()
        controller = TelemetryController(source)
        assert controller.is_pitting is False

    def test_is_pitting_true_after_trigger(self):
        source = self._make_mock_source()
        controller = TelemetryController(source)
        controller.trigger_pit("Medium")
        assert controller.is_pitting is True

    def test_trigger_pit_returns_true_first_time(self):
        source = self._make_mock_source()
        controller = TelemetryController(source)
        assert controller.trigger_pit("Medium") is True

    def test_trigger_pit_returns_false_when_already_pitting(self):
        source = self._make_mock_source()
        controller = TelemetryController(source)
        controller.trigger_pit("Medium")
        assert controller.trigger_pit("Medium") is False

    def test_overrides_applied_during_pit_stop_phase(self):
        """During PIT_STOP, tyre fields should show fresh compound data."""
        source = self._make_mock_source()
        controller = TelemetryController(source)
        controller.trigger_pit("Medium")
        # Force the FSM into PIT_STOP state (skip wall-clock wait)
        controller._pit.state = PitState.PIT_STOP
        snap = controller.get_snapshot()
        # Fresh tyre values should override the raw 72% wear
        assert snap["tire_wear"] == 100.0
        assert snap["tire_age_laps"] == 0
        assert snap["tire_compound"] == "Hard"   # Medium → Hard rotation

"""
tests/test_strategy.py

Tests for StrategyTracker — the proactive trigger engine.

WHY THESE TESTS MATTER:
StrategyTracker is the most stateful, complex module in the system.
It accumulates flags across laps (_pit_called, _sc_pit_called, etc.)
and must fire each trigger exactly once under the right conditions.
These tests verify the trigger logic, anti-spam guards, and SC blocking.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.strategy.strategy_tracker import StrategyTracker


# ---------------------------------------------------------------------------
# Helpers — build test inputs quickly
# ---------------------------------------------------------------------------

def _race_state(overrides=None):
    """Return a baseline mid-race race_state dict."""
    state = {
        "lap": 10,
        "total_laps": 58,
        "laps_remaining": 35,
        "position": 5,
        "gap_ahead": 2.0,
        "gap_behind": 3.0,
        "tire_compound": "Medium",
        "tire_wear": 72.0,
        "tire_age_laps": 10,
        "fuel": 65.0,
        "fuel_per_lap": 1.9,
        "last_lap_time": "1:32.000",
        "best_lap_time": "1:31.500",
        "lap_delta": "+0.500",
        "speed": 250,
        "gear": 6,
        "drs": False,
        "track_status": "green",
    }
    if overrides:
        state.update(overrides)
    return state


def _event(overrides=None):
    """Return a baseline event dict — no urgency, no SC."""
    ev = {
        "urgency": "green",
        "should_pit": False,
        "reason": "tyres and fuel nominal",
        "laps_left_on_tyre": 12,
        "fuel_laps_remaining": 34.0,
        "safety_car": False,
    }
    if overrides:
        ev.update(overrides)
    return ev


# ---------------------------------------------------------------------------
# INITIAL_BRIEF trigger
# ---------------------------------------------------------------------------

class TestInitialBrief:

    def test_fires_on_lap_2(self):
        """INITIAL_BRIEF should fire on the first lap >= 2."""
        tracker = StrategyTracker()
        state = _race_state({"lap": 2, "laps_remaining": 55})
        triggers = tracker.evaluate(state, _event())
        assert "INITIAL_BRIEF" in triggers

    def test_does_not_fire_on_lap_1(self):
        """INITIAL_BRIEF must not fire on lap 1 — wear rate not yet established."""
        tracker = StrategyTracker()
        state = _race_state({"lap": 1, "laps_remaining": 57})
        triggers = tracker.evaluate(state, _event())
        assert "INITIAL_BRIEF" not in triggers

    def test_fires_only_once(self):
        """INITIAL_BRIEF should never fire twice, even across multiple laps."""
        tracker = StrategyTracker()
        # First call on lap 2 — fires
        state2 = _race_state({"lap": 2, "laps_remaining": 55})
        triggers_lap2 = tracker.evaluate(state2, _event())
        assert "INITIAL_BRIEF" in triggers_lap2

        # Second call on lap 3 — must NOT fire again
        state3 = _race_state({"lap": 3, "laps_remaining": 54})
        triggers_lap3 = tracker.evaluate(state3, _event())
        assert "INITIAL_BRIEF" not in triggers_lap3


# ---------------------------------------------------------------------------
# SC_OPPORTUNITY trigger
# ---------------------------------------------------------------------------

class TestSCOpportunity:

    def test_sc_opportunity_fires_when_sc_active_and_old_tyres(self):
        """SC deployed + should_pit=True + old enough tyres → SC_OPPORTUNITY."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True   # skip initial brief
        state = _race_state({"lap": 15, "laps_remaining": 30})
        ev = _event({"safety_car": True, "should_pit": True})
        triggers = tracker.evaluate(state, ev)
        assert "SC_OPPORTUNITY" in triggers

    def test_sc_opportunity_fires_only_once_per_sc_period(self):
        """SC_OPPORTUNITY must not repeat while the safety car is still out."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 15, "laps_remaining": 30})
        ev = _event({"safety_car": True, "should_pit": True})

        # First lap under SC — fires
        triggers_lap15 = tracker.evaluate(state, ev)
        assert "SC_OPPORTUNITY" in triggers_lap15

        # Second lap under SC — must be silent
        state16 = _race_state({"lap": 16, "laps_remaining": 29})
        triggers_lap16 = tracker.evaluate(state16, ev)
        assert "SC_OPPORTUNITY" not in triggers_lap16

    def test_sc_does_not_fire_without_should_pit(self):
        """SC active but should_pit=False (fresh tyres) → no SC_OPPORTUNITY."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 15, "laps_remaining": 30})
        ev = _event({"safety_car": True, "should_pit": False})   # fresh tyres
        triggers = tracker.evaluate(state, ev)
        assert "SC_OPPORTUNITY" not in triggers

    def test_sc_blocks_normal_triggers(self):
        """During safety car, no PIT_NOW / PLAN_CHANGED should fire."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker._sc_pit_called = True   # already called this SC period
        state = _race_state({"lap": 16, "laps_remaining": 29})
        ev = _event({"safety_car": True, "should_pit": True})
        triggers = tracker.evaluate(state, ev)
        # After SC call is made, evaluate() returns [] — no normal triggers leak through
        assert "PIT_NOW" not in triggers
        assert "PLAN_CHANGED" not in triggers
        assert len(triggers) == 0

    def test_sc_flag_resets_on_green(self):
        """After SC ends, _sc_pit_called resets so a second SC period is handled."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True

        # First SC period — fires once
        state15 = _race_state({"lap": 15, "laps_remaining": 30})
        ev_sc = _event({"safety_car": True, "should_pit": True})
        tracker.evaluate(state15, ev_sc)
        assert tracker._sc_pit_called is True

        # Green flag lap — flag should reset
        state20 = _race_state({"lap": 20, "laps_remaining": 25})
        ev_green = _event({"safety_car": False})
        tracker.evaluate(state20, ev_green)
        assert tracker._sc_pit_called is False


# ---------------------------------------------------------------------------
# PIT_NOW trigger
# ---------------------------------------------------------------------------

class TestPitNow:

    def test_pit_now_fires_at_planned_lap(self):
        """PIT_NOW fires when current_lap >= planned_pit_lap and should_pit=True."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 20   # pre-set the plan
        tracker._approaching_called = True  # skip PIT_APPROACHING for this test

        state = _race_state({"lap": 20, "laps_remaining": 10})
        ev = _event({"should_pit": True})
        triggers = tracker.evaluate(state, ev)
        assert "PIT_NOW" in triggers

    def test_pit_now_does_not_fire_without_should_pit(self):
        """PIT_NOW requires should_pit=True, not just reaching the planned lap."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 20
        tracker._approaching_called = True

        state = _race_state({"lap": 20, "laps_remaining": 10})
        ev = _event({"should_pit": False})
        triggers = tracker.evaluate(state, ev)
        assert "PIT_NOW" not in triggers

    def test_pit_now_fires_only_once(self):
        """PIT_NOW must not repeat every lap — _pit_called prevents this."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 20
        tracker._approaching_called = True

        state20 = _race_state({"lap": 20, "laps_remaining": 10})
        ev = _event({"should_pit": True})
        triggers_lap20 = tracker.evaluate(state20, ev)
        assert "PIT_NOW" in triggers_lap20

        state21 = _race_state({"lap": 21, "laps_remaining": 9})
        triggers_lap21 = tracker.evaluate(state21, ev)
        assert "PIT_NOW" not in triggers_lap21


# ---------------------------------------------------------------------------
# PIT_APPROACHING trigger
# ---------------------------------------------------------------------------

class TestPitApproaching:

    def test_pit_approaching_fires_3_laps_before_plan(self):
        """PIT_APPROACHING fires exactly 3 laps before planned_pit_lap."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 25

        state = _race_state({"lap": 22, "laps_remaining": 15})   # 25 - 3 = 22
        ev = _event({"should_pit": False, "laps_left_on_tyre": 3})
        triggers = tracker.evaluate(state, ev)
        assert "PIT_APPROACHING" in triggers

    def test_pit_approaching_fires_only_once(self):
        """_approaching_called flag prevents re-firing if plan shifts."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 25

        # Fires on lap 22
        state22 = _race_state({"lap": 22, "laps_remaining": 15})
        ev = _event({"laps_left_on_tyre": 3})
        triggers_lap22 = tracker.evaluate(state22, ev)
        assert "PIT_APPROACHING" in triggers_lap22

        # Shouldn't fire again if plan shifts to lap 28 (new threshold = lap 25)
        tracker.planned_pit_lap = 28
        state25 = _race_state({"lap": 25, "laps_remaining": 12})
        triggers_lap25 = tracker.evaluate(state25, ev)
        assert "PIT_APPROACHING" not in triggers_lap25


# ---------------------------------------------------------------------------
# reset_pit — state reset between stints
# ---------------------------------------------------------------------------

class TestResetPit:

    def test_reset_clears_pit_called(self):
        tracker = StrategyTracker()
        tracker._pit_called = True
        tracker.reset_pit()
        assert tracker._pit_called is False

    def test_reset_clears_approaching_called(self):
        tracker = StrategyTracker()
        tracker._approaching_called = True
        tracker.reset_pit()
        assert tracker._approaching_called is False

    def test_reset_keeps_initial_brief_true(self):
        """Initial brief should not fire again after a pit stop."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.reset_pit()
        assert tracker._initial_brief_done is True   # stays True

    def test_reset_does_not_clear_sc_pit_called(self):
        """_sc_pit_called must NOT reset between stints — only on green flag."""
        tracker = StrategyTracker()
        tracker._sc_pit_called = True
        tracker.reset_pit()
        assert tracker._sc_pit_called is True   # untouched


# ---------------------------------------------------------------------------
# build_prompt — prompt string formatting
# ---------------------------------------------------------------------------

class TestBuildPrompt:

    def setup_method(self):
        self.tracker = StrategyTracker()
        self.tracker.planned_pit_lap = 25
        self.tracker._prev_planned_pit_lap = 22

    def _state(self):
        return _race_state({"lap": 10, "position": 3, "tire_compound": "Medium", "tire_wear": 72.0})

    def test_initial_brief_prompt_contains_position(self):
        prompt = self.tracker.build_prompt("INITIAL_BRIEF", self._state(), _event())
        assert "P3" in prompt or "Position" in prompt.lower() or "3" in prompt

    def test_pit_now_prompt_contains_box(self):
        prompt = self.tracker.build_prompt("PIT_NOW", self._state(), _event())
        assert "box" in prompt.lower()

    def test_sc_opportunity_prompt_contains_safety_car(self):
        prompt = self.tracker.build_prompt("SC_OPPORTUNITY", self._state(), _event())
        assert "safety car" in prompt.lower()

    def test_plan_changed_prompt_contains_new_lap(self):
        prompt = self.tracker.build_prompt("PLAN_CHANGED", self._state(), _event())
        assert "25" in prompt   # the new planned_pit_lap

    def test_pit_approaching_prompt_contains_laps(self):
        prompt = self.tracker.build_prompt("PIT_APPROACHING", self._state(), _event())
        assert "3" in prompt or "25" in prompt   # 3 laps away or the pit lap itself

    def test_prompt_returns_string(self):
        for trigger in ("INITIAL_BRIEF", "PLAN_CHANGED", "PIT_APPROACHING",
                        "PIT_NOW", "SC_OPPORTUNITY"):
            result = self.tracker.build_prompt(trigger, self._state(), _event())
            assert isinstance(result, str), f"build_prompt({trigger}) did not return str"
            assert len(result) > 10, f"Prompt for {trigger} looks too short"

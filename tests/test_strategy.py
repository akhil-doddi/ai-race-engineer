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

    def test_reset_clears_endgame_manage_called(self):
        """_endgame_manage_called resets on pit — second stint may also end in endgame."""
        tracker = StrategyTracker()
        tracker._endgame_manage_called = True
        tracker.reset_pit()
        assert tracker._endgame_manage_called is False

    def test_reset_clears_finish_race_called(self):
        """_finish_race_called resets on pit — new stint may also reach endgame."""
        tracker = StrategyTracker()
        tracker._finish_race_called = True
        tracker.reset_pit()
        assert tracker._finish_race_called is False


# ---------------------------------------------------------------------------
# FINISH_RACE trigger
# ---------------------------------------------------------------------------

class TestFinishRace:
    """
    FINISH_RACE fires when a planned pit stop window arrives during endgame.
    This is the fix for the bug where PIT_APPROACHING would announce
    'Pit in 3 laps' even when only 9 laps remained.
    """

    def _endgame_ev(self):
        """Event dict for endgame phase with no pit suppressed (healthy-ish tyre)."""
        return _event({
            "should_pit":       False,
            "race_phase":       "endgame",
            "endgame_override": False,   # tyre not worn enough to trigger endgame_override
        })

    def test_finish_race_fires_at_pit_window_in_endgame(self):
        """The exact bug scenario: lap 49, planned pit 52, laps_remaining 9."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        state = _race_state({"lap": 49, "laps_remaining": 9})
        triggers = tracker.evaluate(state, self._endgame_ev())
        assert "FINISH_RACE" in triggers

    def test_finish_race_suppresses_pit_approaching(self):
        """PIT_APPROACHING must NOT fire when FINISH_RACE already covers the same moment."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        state = _race_state({"lap": 49, "laps_remaining": 9})
        triggers = tracker.evaluate(state, self._endgame_ev())
        assert "PIT_APPROACHING" not in triggers

    def test_finish_race_fires_only_once(self):
        """_finish_race_called prevents FINISH_RACE from repeating every lap."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52

        state49 = _race_state({"lap": 49, "laps_remaining": 9})
        triggers_49 = tracker.evaluate(state49, self._endgame_ev())
        assert "FINISH_RACE" in triggers_49

        state50 = _race_state({"lap": 50, "laps_remaining": 8})
        triggers_50 = tracker.evaluate(state50, self._endgame_ev())
        assert "FINISH_RACE" not in triggers_50

    def test_finish_race_does_not_fire_in_mid_race(self):
        """FINISH_RACE must not fire when race_phase is 'mid'."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 30
        state = _race_state({"lap": 27, "laps_remaining": 30})  # mid-race
        ev = _event({"race_phase": "mid", "endgame_override": False})
        triggers = tracker.evaluate(state, ev)
        assert "FINISH_RACE" not in triggers
        assert "PIT_APPROACHING" in triggers   # normal flow still works

    def test_finish_race_does_not_fire_without_planned_pit(self):
        """No planned pit lap → nothing to cancel, FINISH_RACE stays silent."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = None
        state = _race_state({"lap": 49, "laps_remaining": 9})
        triggers = tracker.evaluate(state, self._endgame_ev())
        assert "FINISH_RACE" not in triggers

    def test_finish_race_does_not_fire_if_pit_already_happened(self):
        """If the driver already pitted this stint, no finish race message needed."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        tracker._pit_called = True   # pit already happened
        state = _race_state({"lap": 49, "laps_remaining": 9})
        triggers = tracker.evaluate(state, self._endgame_ev())
        assert "FINISH_RACE" not in triggers

    def test_finish_race_reset_allows_refiring_after_pit(self):
        """After reset_pit(), FINISH_RACE can fire again if new stint also hits endgame."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        state49 = _race_state({"lap": 49, "laps_remaining": 9})
        tracker.evaluate(state49, self._endgame_ev())
        assert tracker._finish_race_called is True

        tracker.reset_pit()
        assert tracker._finish_race_called is False

        tracker.planned_pit_lap = 56
        state53 = _race_state({"lap": 53, "laps_remaining": 5})
        triggers_after_reset = tracker.evaluate(state53, self._endgame_ev())
        assert "FINISH_RACE" in triggers_after_reset

    def test_pit_approaching_blocked_in_endgame(self):
        """PIT_APPROACHING must never fire when race_phase == endgame."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        tracker._finish_race_called = True  # as if FINISH_RACE already fired
        state = _race_state({"lap": 49, "laps_remaining": 9})
        triggers = tracker.evaluate(state, self._endgame_ev())
        assert "PIT_APPROACHING" not in triggers

    def test_pit_now_blocked_in_endgame(self):
        """PIT_NOW must not fire in endgame even if we've reached the planned lap."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        tracker.planned_pit_lap = 52
        tracker._finish_race_called = True  # finish already called
        state = _race_state({"lap": 52, "laps_remaining": 6})
        ev = _event({"should_pit": True, "race_phase": "endgame", "endgame_override": False})
        triggers = tracker.evaluate(state, ev)
        assert "PIT_NOW" not in triggers


# ---------------------------------------------------------------------------
# ENDGAME_MANAGE trigger
# ---------------------------------------------------------------------------

class TestEndgameManage:

    def _endgame_event(self, tire_wear=30.0):
        """Event dict with endgame_override=True (pit was suppressed by phase)."""
        return _event({
            "should_pit":       False,       # suppressed by endgame override
            "endgame_override": True,
            "race_phase":       "endgame",
        })

    def test_endgame_manage_fires_with_worn_tyre(self):
        """ENDGAME_MANAGE fires when pit is suppressed and tyre is below 40%."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        triggers = tracker.evaluate(state, self._endgame_event())
        assert "ENDGAME_MANAGE" in triggers

    def test_endgame_manage_does_not_fire_with_healthy_tyre(self):
        """ENDGAME_MANAGE should stay silent if tyre is above 40% — nothing to manage."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 75.0})
        triggers = tracker.evaluate(state, self._endgame_event())
        assert "ENDGAME_MANAGE" not in triggers

    def test_endgame_manage_fires_only_once(self):
        """_endgame_manage_called prevents the trigger from repeating every lap."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True

        state50 = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        ev = self._endgame_event()
        triggers_lap50 = tracker.evaluate(state50, ev)
        assert "ENDGAME_MANAGE" in triggers_lap50

        state51 = _race_state({"lap": 51, "laps_remaining": 7, "tire_wear": 26.0})
        triggers_lap51 = tracker.evaluate(state51, ev)
        assert "ENDGAME_MANAGE" not in triggers_lap51

    def test_endgame_manage_does_not_fire_without_override_flag(self):
        """ENDGAME_MANAGE must not fire if event_detector did not set endgame_override."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        normal_ev = _event({"should_pit": True, "endgame_override": False})
        triggers = tracker.evaluate(state, normal_ev)
        assert "ENDGAME_MANAGE" not in triggers

    def test_endgame_manage_not_blocked_by_sc(self):
        """SC active blocks normal triggers — but endgame is after SC block so unreachable during SC."""
        # During SC, evaluate() returns early before reaching ENDGAME_MANAGE block.
        # This test confirms ENDGAME_MANAGE does NOT fire during an active SC.
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 50, "laps_remaining": 9, "tire_wear": 30.0})
        sc_ev = _event({
            "safety_car":       True,
            "should_pit":       False,
            "endgame_override": True,  # even with this set, SC block takes priority
        })
        triggers = tracker.evaluate(state, sc_ev)
        # SC block returns [] immediately — ENDGAME_MANAGE never evaluated
        assert "ENDGAME_MANAGE" not in triggers

    def test_endgame_manage_reset_allows_refiring_after_pit(self):
        """After reset_pit(), ENDGAME_MANAGE can fire again for the new stint."""
        tracker = StrategyTracker()
        tracker._initial_brief_done = True
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        ev = self._endgame_event()

        tracker.evaluate(state, ev)
        assert tracker._endgame_manage_called is True

        tracker.reset_pit()
        assert tracker._endgame_manage_called is False

        state51 = _race_state({"lap": 51, "laps_remaining": 7, "tire_wear": 26.0})
        triggers_after_reset = tracker.evaluate(state51, ev)
        assert "ENDGAME_MANAGE" in triggers_after_reset


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

    def test_endgame_manage_prompt_contains_no_stop(self):
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        prompt = self.tracker.build_prompt("ENDGAME_MANAGE", state, _event())
        assert "no stop" in prompt.lower() or "not pitting" in prompt.lower() or "staying out" in prompt.lower()

    def test_endgame_manage_prompt_contains_laps_remaining(self):
        state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
        prompt = self.tracker.build_prompt("ENDGAME_MANAGE", state, _event())
        assert "8" in prompt   # laps remaining

    def test_prompt_returns_string(self):
        for trigger in ("INITIAL_BRIEF", "PLAN_CHANGED", "PIT_APPROACHING",
                        "PIT_NOW", "SC_OPPORTUNITY", "ENDGAME_MANAGE"):
            state = _race_state({"lap": 50, "laps_remaining": 8, "tire_wear": 30.0})
            result = self.tracker.build_prompt(trigger, state, _event())
            assert isinstance(result, str), f"build_prompt({trigger}) did not return str"
            assert len(result) > 10, f"Prompt for {trigger} looks too short"

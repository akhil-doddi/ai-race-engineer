"""
tests/test_response_generator.py

Tests for the communication layer: build_user_message and prompt construction.

WHY NO API CALLS HERE:
Tests must be fast and free. We never call the OpenAI API in tests.
Instead we test the prompt construction logic — the part we own and control.
ask_engineer() itself is covered by a mock-based test to verify message structure.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.communication.response_generator import build_user_message, SYSTEM_MESSAGE


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _race_state(overrides=None):
    """Minimal valid race_state for prompt tests."""
    state = {
        "lap": 15,
        "total_laps": 58,
        "laps_remaining": 43,
        "position": 4,
        "gap_ahead": 1.5,
        "gap_behind": 2.3,
        "tire_compound": "Medium",
        "tire_wear": 68.0,
        "tire_age_laps": 15,
        "fuel": 72.0,
        "fuel_per_lap": 1.9,
        "last_lap_time": "1:32.456",
        "best_lap_time": "1:31.800",
        "lap_delta": "+0.656",
        "speed": 255,
        "gear": 6,
        "drs": False,
    }
    if overrides:
        state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# build_user_message tests
# ---------------------------------------------------------------------------

class TestBuildUserMessage:

    def test_returns_dict_with_role_and_content(self):
        msg = build_user_message("what lap are we on?", _race_state())
        assert isinstance(msg, dict)
        assert "role" in msg
        assert "content" in msg

    def test_role_is_user(self):
        msg = build_user_message("any question", _race_state())
        assert msg["role"] == "user"

    def test_driver_input_present_in_content(self):
        msg = build_user_message("should I pit?", _race_state())
        assert "should I pit?" in msg["content"]

    def test_lap_number_in_content(self):
        msg = build_user_message("test", _race_state({"lap": 20, "total_laps": 58}))
        assert "20" in msg["content"]
        assert "58" in msg["content"]

    def test_position_in_content(self):
        msg = build_user_message("test", _race_state({"position": 7}))
        assert "P7" in msg["content"] or "7" in msg["content"]

    def test_tyre_compound_in_content(self):
        msg = build_user_message("test", _race_state({"tire_compound": "Soft"}))
        assert "Soft" in msg["content"]

    def test_drs_on_shows_in_content(self):
        msg = build_user_message("test", _race_state({"drs": True}))
        assert "ON" in msg["content"]

    def test_drs_off_shows_in_content(self):
        msg = build_user_message("test", _race_state({"drs": False}))
        assert "OFF" in msg["content"]

    def test_content_is_string(self):
        msg = build_user_message("any input", _race_state())
        assert isinstance(msg["content"], str)

    def test_content_not_empty(self):
        msg = build_user_message("anything", _race_state())
        assert len(msg["content"]) > 50


# ---------------------------------------------------------------------------
# SYSTEM_MESSAGE tests
# ---------------------------------------------------------------------------

class TestSystemMessage:

    def test_system_message_has_correct_role(self):
        assert SYSTEM_MESSAGE["role"] == "system"

    def test_system_message_has_content(self):
        assert isinstance(SYSTEM_MESSAGE["content"], str)
        assert len(SYSTEM_MESSAGE["content"]) > 100

    def test_system_message_includes_engineer_persona(self):
        content = SYSTEM_MESSAGE["content"].lower()
        assert "engineer" in content

    def test_system_message_includes_strategy_guidance(self):
        content = SYSTEM_MESSAGE["content"].lower()
        assert "strategy" in content


# ---------------------------------------------------------------------------
# ask_engineer — mocked to avoid real API calls
# ---------------------------------------------------------------------------

class TestAskEngineerMocked:

    def _mock_openai_response(self, reply_text: str):
        """Build a mock that mimics openai client.chat.completions.create()"""
        mock_choice = MagicMock()
        mock_choice.message.content = reply_text
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    def test_ask_engineer_returns_reply_and_history(self):
        from src.communication.response_generator import ask_engineer

        mock_response = self._mock_openai_response("You're in P4, 43 laps remaining.")

        with patch("src.communication.response_generator.client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_response
            reply, history = ask_engineer("what lap are we on?", _race_state(), [])

        assert isinstance(reply, str)
        assert isinstance(history, list)
        assert len(reply) > 0

    def test_ask_engineer_appends_to_history(self):
        from src.communication.response_generator import ask_engineer

        mock_response = self._mock_openai_response("Tyres are at 68%, looking good.")

        with patch("src.communication.response_generator.client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_response
            _, history = ask_engineer("how are the tyres?", _race_state(), [])

        # Should have: 1 user message + 1 assistant message = 2 entries
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_ask_engineer_history_grows_correctly(self):
        """After 3 questions, history should have 6 entries (3 user + 3 assistant)."""
        from src.communication.response_generator import ask_engineer

        mock_response = self._mock_openai_response("Confirmed.")

        history = []
        with patch("src.communication.response_generator.client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_response
            for question in ["q1", "q2", "q3"]:
                _, history = ask_engineer(question, _race_state(), history)

        assert len(history) == 6

    def test_ask_engineer_caps_history_at_20(self):
        """History should never exceed 20 messages sent to the API."""
        from src.communication.response_generator import ask_engineer

        mock_response = self._mock_openai_response("OK.")

        history = []
        call_messages = []

        def capture_call(**kwargs):
            call_messages.append(kwargs["messages"])
            return mock_response

        with patch("src.communication.response_generator.client") as mock_client:
            mock_client.chat.completions.create.side_effect = capture_call
            for i in range(15):
                _, history = ask_engineer(f"question {i}", _race_state(), history)

        # The messages sent to API = system_message + capped history
        # History cap is 20, so messages sent = min(len(history), 20) + 1 (system)
        last_call = call_messages[-1]
        history_messages = [m for m in last_call if m["role"] != "system"]
        assert len(history_messages) <= 20

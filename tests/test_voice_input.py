"""
tests/test_voice_input.py

Tests for the voice layer: tts_engine (speech cleaning) and voice_input (listen).

WHY NO REAL AUDIO HERE:
Tests must run on any machine without a microphone or speakers.
We test the logic we control — the speech cleaning regex and the error handling.
Actual audio hardware calls are mocked.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# TTS speech cleaning tests (the regex we own)
# ---------------------------------------------------------------------------

class TestCleanForSpeech:

    def setup_method(self):
        from src.voice.tts_engine import clean_for_speech
        self.clean = clean_for_speech

    def test_position_abbreviation_expanded(self):
        """P14 should become 'Position 14' so TTS doesn't say 'page 14'."""
        assert "Position 14" in self.clean("You are P14.")

    def test_p1_expanded(self):
        assert "Position 1" in self.clean("You're in P1!")

    def test_p20_expanded(self):
        assert "Position 20" in self.clean("Running P20.")

    def test_drs_expanded(self):
        """DRS should become 'D R S' so TTS reads each letter."""
        result = self.clean("DRS is available.")
        assert "D R S" in result

    def test_plain_text_unchanged(self):
        """Text with no special patterns should pass through untouched."""
        text = "The tyres are looking good."
        assert self.clean(text) == text

    def test_p_in_word_not_expanded(self):
        """'Plan' or 'Push' should not get expanded — only standalone P+digits."""
        result = self.clean("Plan A is the strategy.")
        assert "Position" not in result

    def test_semicolons_replaced(self):
        """Semicolons cause some TTS engines to pause incorrectly."""
        result = self.clean("First; then second.")
        assert ";" not in result

    def test_em_dash_replaced(self):
        """Em dashes caused pyttsx3 to cut off speech early."""
        result = self.clean("Box box — come in now.")
        assert "—" not in result

    def test_multiple_positions_all_expanded(self):
        result = self.clean("Gap from P3 to P7 is three seconds.")
        assert "Position 3" in result
        assert "Position 7" in result

    def test_returns_string(self):
        assert isinstance(self.clean("Any input"), str)


# ---------------------------------------------------------------------------
# speak() — mocked subprocess (macOS `say` command)
# ---------------------------------------------------------------------------

class TestSpeak:

    def test_speak_calls_subprocess_run(self):
        """speak() must invoke subprocess.run with the `say` command on macOS."""
        with patch("src.voice.tts_engine.subprocess.run") as mock_run, \
             patch("src.voice.tts_engine.sys.platform", "darwin"):
            from src.voice import tts_engine
            tts_engine.speak("Tyres critical, box box box.")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]   # first positional arg is the command list
            assert args[0] == "say", f"Expected 'say' command, got: {args[0]}"

    def test_speak_passes_cleaned_text(self):
        """speak() must clean the text before passing to subprocess."""
        with patch("src.voice.tts_engine.subprocess.run") as mock_run, \
             patch("src.voice.tts_engine.sys.platform", "darwin"):
            from src.voice import tts_engine
            tts_engine.speak("You are P14, DRS available.")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            # The command list's last element is the cleaned text
            spoken_text = args[-1]
            assert "Position 14" in spoken_text, "P14 was not expanded before speaking"
            assert "D R S" in spoken_text, "DRS was not expanded before speaking"


# ---------------------------------------------------------------------------
# listen() — mocked microphone
# ---------------------------------------------------------------------------

class TestListen:

    def test_listen_returns_string_on_success(self):
        """listen() must return a non-empty string when speech is recognised."""
        mock_audio = MagicMock()
        mock_recogniser = MagicMock()
        mock_recogniser.listen.return_value = mock_audio
        mock_recogniser.recognize_google.return_value = "what lap are we on"

        with patch("src.voice.voice_input.sr.Recognizer", return_value=mock_recogniser), \
             patch("src.voice.voice_input.sr.Microphone"):
            from src.voice import voice_input
            result = voice_input.listen()

        assert isinstance(result, str)

    def test_listen_returns_empty_string_on_error(self):
        """listen() must return '' (not crash) when recognition fails."""
        mock_recogniser = MagicMock()
        mock_recogniser.listen.side_effect = Exception("No microphone found")

        with patch("src.voice.voice_input.sr.Recognizer", return_value=mock_recogniser), \
             patch("src.voice.voice_input.sr.Microphone"):
            from src.voice import voice_input
            result = voice_input.listen()

        assert result == ""

    def test_listen_returns_empty_on_unknown_value_error(self):
        """listen() must return '' when Google can't understand the audio."""
        import speech_recognition as sr

        mock_audio = MagicMock()
        mock_recogniser = MagicMock()
        mock_recogniser.listen.return_value = mock_audio
        mock_recogniser.recognize_google.side_effect = sr.UnknownValueError()

        with patch("src.voice.voice_input.sr.Recognizer", return_value=mock_recogniser), \
             patch("src.voice.voice_input.sr.Microphone"):
            from src.voice import voice_input
            result = voice_input.listen()

        assert result == ""

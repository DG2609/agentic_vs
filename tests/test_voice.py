"""
Tests for agent/tools/voice.py — voice input recording & transcription.
"""
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError


# ── Schema validation ────────────────────────────────────────

def test_voice_input_args_defaults():
    from agent.tools.voice import VoiceInputArgs
    args = VoiceInputArgs()
    assert args.duration == 5
    assert args.language == "en"
    assert args.use_local is False


def test_voice_input_args_duration_bounds():
    from agent.tools.voice import VoiceInputArgs
    # Lower bound
    with pytest.raises(ValidationError):
        VoiceInputArgs(duration=0)
    # Upper bound
    with pytest.raises(ValidationError):
        VoiceInputArgs(duration=31)
    # Valid extremes
    assert VoiceInputArgs(duration=1).duration == 1
    assert VoiceInputArgs(duration=30).duration == 30


def test_voice_input_args_language_override():
    from agent.tools.voice import VoiceInputArgs
    args = VoiceInputArgs(language="ja")
    assert args.language == "ja"


# ── Error paths (mocked availability flags) ──────────────────

def test_voice_input_no_sounddevice(monkeypatch):
    import agent.tools.voice as voice_mod
    monkeypatch.setattr(voice_mod, "_SOUNDDEVICE_AVAILABLE", False)
    result = voice_mod.voice_input.invoke({"duration": 2, "language": "en"})
    assert "sounddevice" in result
    assert "Error" in result


def test_voice_input_no_numpy(monkeypatch):
    import agent.tools.voice as voice_mod
    monkeypatch.setattr(voice_mod, "_SOUNDDEVICE_AVAILABLE", True)
    monkeypatch.setattr(voice_mod, "_NUMPY_AVAILABLE", False)
    result = voice_mod.voice_input.invoke({"duration": 2, "language": "en"})
    assert "numpy" in result
    assert "Error" in result


def test_transcribe_openai_no_api_key(monkeypatch, tmp_path):
    """_transcribe_openai returns error when OPENAI_API_KEY is empty."""
    import agent.tools.voice as voice_mod
    import config

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"\x00" * 100)

    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    # Mock the openai import inside _transcribe_openai
    fake_openai = MagicMock()
    with patch.dict("sys.modules", {"openai": fake_openai}):
        result = voice_mod._transcribe_openai(str(wav), "en")
    assert "OPENAI_API_KEY" in result
    assert "Error" in result


def test_transcribe_local_no_whisper(monkeypatch, tmp_path):
    """_transcribe_local returns error when whisper package is missing."""
    import agent.tools.voice as voice_mod

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"\x00" * 100)

    # Ensure whisper is not importable
    with patch.dict("sys.modules", {"whisper": None}):
        result = voice_mod._transcribe_local(str(wav), "en")
    assert "whisper" in result
    assert "Error" in result


def test_voice_input_recording_mocked(monkeypatch):
    """Full recording path with sounddevice mocked."""
    import agent.tools.voice as voice_mod
    import numpy as np

    monkeypatch.setattr(voice_mod, "_SOUNDDEVICE_AVAILABLE", True)
    monkeypatch.setattr(voice_mod, "_NUMPY_AVAILABLE", True)
    monkeypatch.setattr(voice_mod, "np", np)

    fake_audio = np.zeros((16000, 1), dtype="float32")
    fake_sd = MagicMock()
    fake_sd.rec.return_value = fake_audio
    fake_sd.wait.return_value = None
    monkeypatch.setattr(voice_mod, "sd", fake_sd)

    # Mock transcription to avoid needing OpenAI
    monkeypatch.setattr(
        voice_mod, "_transcribe_openai",
        lambda path, lang: "Transcribed text: hello world",
    )

    result = voice_mod.voice_input.invoke({"duration": 1, "language": "en"})
    assert "hello world" in result
    fake_sd.rec.assert_called_once()
    fake_sd.wait.assert_called_once()

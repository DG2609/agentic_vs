"""
Voice input tool — record audio and transcribe via Whisper API or local whisper.

Dependencies (optional):
  pip install sounddevice numpy openai
"""

import os
import io
import tempfile
import logging
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Check availability at import time
_SOUNDDEVICE_AVAILABLE = False
_NUMPY_AVAILABLE = False
try:
    import sounddevice as sd
    _SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None


class VoiceInputArgs(BaseModel):
    """Arguments for voice input recording."""
    duration: int = Field(
        default=5, ge=1, le=30,
        description="Recording duration in seconds (1-30)."
    )
    language: str = Field(
        default="en",
        description="Language code for transcription (e.g., 'en', 'vi', 'ja')."
    )
    use_local: bool = Field(
        default=False,
        description="Use local whisper model instead of OpenAI API. Requires 'whisper' package."
    )


@tool(args_schema=VoiceInputArgs)
def voice_input(duration: int = 5, language: str = "en", use_local: bool = False) -> str:
    """Record audio from microphone and transcribe to text using Whisper.

    Returns the transcribed text from speech input. Useful when the user
    wants to dictate code changes or instructions by voice.
    """
    if not _SOUNDDEVICE_AVAILABLE:
        return "Error: 'sounddevice' package not installed. Run: pip install sounddevice"
    if not _NUMPY_AVAILABLE:
        return "Error: 'numpy' package not installed. Run: pip install numpy"

    sample_rate = 16000
    channels = 1

    try:
        logger.info(f"Recording {duration}s of audio at {sample_rate}Hz...")
        audio_data = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
        )
        sd.wait()  # Block until recording is done
        logger.info("Recording complete.")
    except Exception as e:
        return f"Error recording audio: {e}"

    # Save to temp WAV file
    try:
        import wave
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name

        audio_int16 = (audio_data * 32767).astype(np.int16)
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(audio_int16.tobytes())
    except Exception as e:
        return f"Error saving audio: {e}"

    # Transcribe
    try:
        if use_local:
            return _transcribe_local(tmp_path, language)
        else:
            return _transcribe_openai(tmp_path, language)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _transcribe_openai(wav_path: str, language: str) -> str:
    """Transcribe using OpenAI Whisper API."""
    try:
        from openai import OpenAI
    except ImportError:
        return "Error: 'openai' package not installed. Run: pip install openai"

    import config as cfg
    api_key = cfg.OPENAI_API_KEY
    if not api_key:
        return "Error: OPENAI_API_KEY not set. Required for Whisper API transcription."

    client = OpenAI(api_key=api_key)

    with open(wav_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,
        )

    text = transcript.text.strip()
    if not text:
        return "(No speech detected)"
    return f"Transcribed text: {text}"


def _transcribe_local(wav_path: str, language: str) -> str:
    """Transcribe using local whisper model."""
    try:
        import whisper
    except ImportError:
        return "Error: 'whisper' package not installed. Run: pip install openai-whisper"

    model = whisper.load_model("base")
    result = model.transcribe(wav_path, language=language)
    text = result.get("text", "").strip()
    if not text:
        return "(No speech detected)"
    return f"Transcribed text: {text}"

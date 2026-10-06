"""Speech-to-text via faster-whisper.

The model is loaded lazily on first call to avoid startup delay when voice is unused.
Transcription runs in a thread pool so the event loop stays unblocked.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

# Defaults (overridden by Settings)
_DEFAULT_MODEL = "base.en"
_DEFAULT_DEVICE = "cpu"
_DEFAULT_COMPUTE = "int8"

# Expected input format from the browser
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM


@lru_cache(maxsize=1)
def _load_model(
    model_size: str, device: str = _DEFAULT_DEVICE, compute_type: str = _DEFAULT_COMPUTE
) -> WhisperModel:
    """Load (and cache) the faster-whisper model. First call downloads if needed."""
    from faster_whisper import WhisperModel

    log.info(
        "Loading faster-whisper model=%s device=%s compute=%s", model_size, device, compute_type
    )
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _pcm_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert raw 16-bit signed LE PCM bytes to float32 in [-1, 1]."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    """Read a WAV file from bytes and return float32 audio at native sample rate."""
    with io.BytesIO(wav_bytes) as buf, wave.open(buf, "rb") as wf:
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
        sw = wf.getsampwidth()
    if sw == 2:
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if sw == 4:
        return np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    msg = f"Unsupported sample width: {sw}"
    raise ValueError(msg)


def _transcribe_sync(audio: np.ndarray, model_size: str) -> str:
    """Run whisper transcription (blocking). Called inside a thread."""
    model = _load_model(model_size)
    segments, _info = model.transcribe(audio, beam_size=1, language="en", vad_filter=True)
    parts: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)


async def transcribe_pcm(pcm_bytes: bytes, *, model_size: str = _DEFAULT_MODEL) -> str:
    """Transcribe raw 16kHz 16-bit mono PCM bytes. Returns the recognised text.

    Runs the model in a thread pool so the event loop is not blocked.
    """
    if not pcm_bytes:
        return ""
    audio = _pcm_to_float32(pcm_bytes)
    if audio.size == 0:
        return ""
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _transcribe_sync, audio, model_size)
    return text.strip()


async def transcribe_wav(wav_bytes: bytes, *, model_size: str = _DEFAULT_MODEL) -> str:
    """Transcribe a WAV file given as bytes. Returns the recognised text."""
    if not wav_bytes:
        return ""
    audio = _wav_to_float32(wav_bytes)
    if audio.size == 0:
        return ""
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _transcribe_sync, audio, model_size)
    return text.strip()


def make_wav(
    pcm_bytes: bytes,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    sample_width: int = SAMPLE_WIDTH,
) -> bytes:
    """Wrap raw PCM bytes into a valid WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()

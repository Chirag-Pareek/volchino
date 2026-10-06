"""Text-to-speech via edge-tts (online) with offline fallback (pyttsx3 / piper).

Returns audio bytes and MIME format. The caller base64-encodes them for WebSocket transport.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Literal

log = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"

AudioFormat = Literal["audio/mp3", "audio/wav"]


def _fallback_piper(text: str) -> bytes | None:
    """Try offline synthesis with piper binary if installed."""
    piper_bin = shutil.which("piper")
    if not piper_bin:
        return None
    try:
        proc = subprocess.run(
            [piper_bin, "--output-raw"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            # Wrap raw 16kHz mono 16-bit PCM into WAV
            from server.audio.stt import make_wav

            return make_wav(proc.stdout)
    except Exception:
        log.debug("piper synthesis failed", exc_info=True)
    return None


def _fallback_pyttsx3_sync(text: str) -> bytes | None:
    """Run pyttsx3 offline synthesis (blocking). Saves to temp WAV and reads bytes."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
        try:
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                with open(tmp_path, "rb") as f:
                    return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception:
        log.debug("pyttsx3 offline synthesis failed", exc_info=True)
    return None


async def _offline_fallback(text: str) -> tuple[bytes, AudioFormat]:
    """Attempt piper or pyttsx3 offline synthesis; fall back to silent MP3."""
    loop = asyncio.get_running_loop()

    # 1. Try piper
    piper_audio = await loop.run_in_executor(None, _fallback_piper, text)
    if piper_audio:
        return piper_audio, "audio/wav"

    # 2. Try pyttsx3
    pyttsx3_audio = await loop.run_in_executor(None, _fallback_pyttsx3_sync, text)
    if pyttsx3_audio:
        return pyttsx3_audio, "audio/wav"

    # 3. Last resort: silent MP3
    return _silent_mp3(), "audio/mp3"


async def synthesize(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> tuple[bytes, AudioFormat]:
    """Synthesize *text* to audio bytes using edge-tts.

    Falls back to pyttsx3 / piper (WAV) if edge-tts fails (offline / network error),
    or silent MP3 if all TTS engines fail.
    """
    if not text.strip():
        return _silent_mp3(), "audio/mp3"
    try:
        audio = await _edge_tts(text, voice=voice, rate=rate, pitch=pitch)
        return audio, "audio/mp3"
    except Exception:
        log.warning("edge-tts failed; attempting offline fallback", exc_info=True)
        return await _offline_fallback(text)


async def synthesize_b64(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
) -> tuple[str, AudioFormat]:
    """Convenience: synthesize and return ``(base64_audio, format)``."""
    audio_bytes, fmt = await synthesize(text, voice=voice)
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    return b64, fmt


async def _edge_tts(
    text: str,
    *,
    voice: str,
    rate: str,
    pitch: str,
) -> bytes:
    """Call edge-tts and collect the full MP3 audio."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        msg = f"edge-tts returned no audio chunks for: {text[:60]}"
        raise RuntimeError(msg)
    return b"".join(chunks)


def _silent_mp3() -> bytes:
    """Return a minimal valid MP3 frame (silence) as offline fallback.

    This is an MPEG-1 Layer III 128kbps 44.1kHz stereo frame of silence.
    """
    header = b"\xff\xfb\x90\x04"
    frame_body = b"\x00" * (417 - len(header))
    return header + frame_body

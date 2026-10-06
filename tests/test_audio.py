"""Tests for the audio engine: STT transcription and TTS generation."""

from __future__ import annotations

import base64
import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np

from server.audio import stt, tts

# -- Helpers --


def make_wav_bytes(
    duration_s: float = 0.5, freq_hz: float = 440.0, sample_rate: int = 16000
) -> bytes:
    """Generate a short WAV file with a sine wave for testing."""
    n_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n_samples, dtype=np.float32)
    audio = (np.sin(2 * np.pi * freq_hz * t) * 0.5).astype(np.float32)
    # Convert to int16
    pcm = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def make_pcm_bytes(
    duration_s: float = 0.5, freq_hz: float = 440.0, sample_rate: int = 16000
) -> bytes:
    """Generate raw 16-bit mono PCM bytes."""
    n_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n_samples, dtype=np.float32)
    audio = (np.sin(2 * np.pi * freq_hz * t) * 0.5).astype(np.float32)
    pcm = (audio * 32767).astype(np.int16)
    return pcm.tobytes()


def make_silent_pcm(duration_s: float = 0.2, sample_rate: int = 16000) -> bytes:
    """Generate silent PCM bytes."""
    n_samples = int(sample_rate * duration_s)
    return b"\x00\x00" * n_samples


# -- STT Tests --


class TestPcmConversion:
    def test_pcm_to_float32_range(self):
        pcm = make_pcm_bytes(0.1)
        audio = stt._pcm_to_float32(pcm)
        assert audio.dtype == np.float32
        assert audio.min() >= -1.0
        assert audio.max() <= 1.0
        assert len(audio) > 0

    def test_pcm_to_float32_empty(self):
        audio = stt._pcm_to_float32(b"")
        assert len(audio) == 0

    def test_wav_to_float32(self):
        wav = make_wav_bytes(0.2)
        audio = stt._wav_to_float32(wav)
        assert audio.dtype == np.float32
        assert len(audio) > 0


class TestMakeWav:
    def test_roundtrip(self):
        pcm = make_pcm_bytes(0.1)
        wav = stt.make_wav(pcm)
        # Verify it's valid WAV
        with io.BytesIO(wav) as buf, wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            frames = wf.readframes(wf.getnframes())
        assert frames == pcm


class TestTranscribePcm:
    async def test_empty_returns_empty(self):
        assert await stt.transcribe_pcm(b"") == ""

    @patch("server.audio.stt._load_model")
    async def test_mocked_transcription(self, mock_load):
        # Mock the whisper model
        mock_model = MagicMock()
        fake_seg = MagicMock()
        fake_seg.text = " hello world "
        mock_model.transcribe.return_value = ([fake_seg], MagicMock())
        mock_load.return_value = mock_model

        pcm = make_pcm_bytes(0.5)
        result = await stt.transcribe_pcm(pcm, model_size="tiny.en")
        assert result == "hello world"
        mock_model.transcribe.assert_called_once()

    @patch("server.audio.stt._load_model")
    async def test_no_segments(self, mock_load):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        mock_load.return_value = mock_model

        pcm = make_pcm_bytes(0.2)
        result = await stt.transcribe_pcm(pcm, model_size="tiny.en")
        assert result == ""


class TestTranscribeWav:
    @patch("server.audio.stt._load_model")
    async def test_wav_transcription(self, mock_load):
        mock_model = MagicMock()
        fake_seg = MagicMock()
        fake_seg.text = "test wav"
        mock_model.transcribe.return_value = ([fake_seg], MagicMock())
        mock_load.return_value = mock_model

        wav = make_wav_bytes(0.3)
        result = await stt.transcribe_wav(wav, model_size="tiny.en")
        assert result == "test wav"

    async def test_empty_wav(self):
        assert await stt.transcribe_wav(b"") == ""


# -- TTS Tests --


class TestTtsSynthesize:
    @patch("server.audio.tts._edge_tts")
    async def test_synthesize_returns_bytes(self, mock_edge):
        mock_edge.return_value = b"\xff\xfb\x90\x04" + b"\x00" * 413
        audio, fmt = await tts.synthesize("hello")
        assert isinstance(audio, bytes)
        assert fmt == "audio/mp3"
        assert len(audio) > 0
        mock_edge.assert_called_once()

    @patch("server.audio.tts._edge_tts", side_effect=Exception("offline"))
    @patch("server.audio.tts._fallback_pyttsx3_sync", return_value=b"RIFFdummywav")
    async def test_synthesize_fallback_pyttsx3(self, mock_pyttsx3, mock_edge):
        audio, fmt = await tts.synthesize("hello")
        assert isinstance(audio, bytes)
        assert fmt == "audio/wav"
        assert audio == b"RIFFdummywav"

    @patch("server.audio.tts._edge_tts", side_effect=Exception("offline"))
    @patch("server.audio.tts._fallback_piper", return_value=None)
    @patch("server.audio.tts._fallback_pyttsx3_sync", return_value=None)
    async def test_synthesize_fallback_silent_mp3_on_error(
        self, mock_pyttsx3, mock_piper, mock_edge
    ):
        audio, fmt = await tts.synthesize("hello")
        assert isinstance(audio, bytes)
        assert fmt == "audio/mp3"
        assert len(audio) > 0  # silent MP3 fallback

    async def test_synthesize_empty_text(self):
        audio, fmt = await tts.synthesize("")
        assert isinstance(audio, bytes)
        assert fmt == "audio/mp3"
        assert len(audio) > 0  # silent MP3

    async def test_synthesize_whitespace_text(self):
        audio, fmt = await tts.synthesize("   ")
        assert isinstance(audio, bytes)
        assert fmt == "audio/mp3"


class TestTtsSynthesizeB64:
    @patch("server.audio.tts._edge_tts")
    async def test_returns_b64_and_format(self, mock_edge):
        mock_edge.return_value = b"\xff\xfb\x90\x04" + b"\x00" * 413
        b64, fmt = await tts.synthesize_b64("hello")
        assert fmt == "audio/mp3"
        assert isinstance(b64, str)
        # Verify it's valid base64
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0


class TestSilentMp3:
    def test_valid_mp3_header(self):
        mp3 = tts._silent_mp3()
        assert mp3[:2] == b"\xff\xfb"  # MPEG sync word
        assert len(mp3) == 417


# -- WebSocket audio_chunk integration test --


class TestWsAudioChunk:
    async def test_audio_chunk_round_trip(self, tmp_path):
        """Test that audio_chunk message triggers STT -> pipeline -> TTS flow."""
        from starlette.testclient import TestClient

        from server.config import Settings
        from server.main import create_app

        settings = Settings(
            auth_token="test-secret",
            sqlite_path=tmp_path / "test.db",
            dry_run=True,
            web_dir=tmp_path,  # no static files needed
            default_repo=tmp_path,
            screenshot_dir=tmp_path / "screenshots",
            power_supply_dir=tmp_path / "power",
            pet_success_timeout_s=0.05,
            pet_sleep_after_s=600,
            tool_timeout_s=5.0,
            stt_model="tiny.en",
        )
        app = create_app(settings)

        with (
            TestClient(app) as client,
            client.websocket_connect(f"/ws?token={settings.auth_token}") as ws,
        ):
            ws.receive_json()  # pet snapshot

            # Mock STT to return a known command
            with (
                patch("server.audio.stt.transcribe_pcm", return_value="volume 30%"),
                patch("server.audio.tts.synthesize_b64", return_value=("dGVzdA==", "audio/mp3")),
            ):
                pcm = make_pcm_bytes(0.2)
                b64 = base64.b64encode(pcm).decode()
                ws.send_json({"type": "audio_chunk", "data": b64})

                # Collect messages
                messages = []
                for _ in range(15):
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg["type"] == "voice_response":
                        break

                types = [m["type"] for m in messages]
                assert "transcript" in types
                assert "voice_response" in types

                voice_msg = next(m for m in messages if m["type"] == "voice_response")
                assert voice_msg["text"]
                assert voice_msg["audio"] == "dGVzdA=="
                assert voice_msg["format"] == "audio/mp3"

    async def test_empty_audio_chunk(self, tmp_path):
        from starlette.testclient import TestClient

        from server.config import Settings
        from server.main import create_app

        settings = Settings(
            auth_token="test-secret",
            sqlite_path=tmp_path / "test.db",
            dry_run=True,
            web_dir=tmp_path,
            default_repo=tmp_path,
            screenshot_dir=tmp_path / "screenshots",
            power_supply_dir=tmp_path / "power",
            pet_success_timeout_s=0.05,
            pet_sleep_after_s=600,
            tool_timeout_s=5.0,
        )
        app = create_app(settings)

        with (
            TestClient(app) as client,
            client.websocket_connect(f"/ws?token={settings.auth_token}") as ws,
        ):
            ws.receive_json()  # pet
            ws.send_json({"type": "audio_chunk", "data": ""})
            err_msg = None
            for _ in range(5):
                msg = ws.receive_json()
                if msg.get("type") == "error":
                    err_msg = msg
                    break
            assert err_msg is not None
            assert "Empty" in err_msg["text"]

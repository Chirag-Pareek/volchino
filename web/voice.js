/* ── Volchino Voice Engine ── */
/* Handles mic recording (push-to-talk + wake word), base64 encoding,
   and TTS audio playback via Web Audio API. */

(function () {
  'use strict';

  // ── State ──
  let audioCtx = null;
  let mediaStream = null;
  let recorder = null;
  let isRecording = false;
  let wakeWordEnabled = false;
  let analyser = null;
  let wakeWordInterval = null;

  const SAMPLE_RATE = 16000;
  const WAKE_PHRASE = 'wake up'; // matched against live transcript

  // ── Public API (attached to window.VoiceEngine) ──

  /**
   * Initialize the audio context and request mic permission.
   * Call once after user gesture.
   */
  async function init() {
    if (audioCtx) return true;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true }
      });
      return true;
    } catch (err) {
      console.error('[voice] mic access denied:', err);
      return false;
    }
  }

  /**
   * Start recording audio. Returns immediately.
   * Call stopRecording() to get the base64 PCM data.
   */
  function startRecording() {
    if (!audioCtx || !mediaStream || isRecording) return false;
    isRecording = true;

    // Use ScriptProcessorNode for broad compatibility (AudioWorklet needs HTTPS + separate file)
    const source = audioCtx.createMediaStreamSource(mediaStream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    const chunks = [];

    processor.onaudioprocess = function (e) {
      if (!isRecording) return;
      const float32 = e.inputBuffer.getChannelData(0);
      // Convert float32 -> int16
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      chunks.push(int16);
    };

    source.connect(processor);
    processor.connect(audioCtx.destination); // required for onaudioprocess to fire

    recorder = { source, processor, chunks };
    return true;
  }

  /**
   * Stop recording and return base64-encoded 16kHz 16-bit mono PCM.
   */
  function stopRecording() {
    if (!isRecording || !recorder) return null;
    isRecording = false;

    recorder.source.disconnect();
    recorder.processor.disconnect();

    // Merge all chunks
    let totalLen = 0;
    for (const c of recorder.chunks) totalLen += c.length;
    const merged = new Int16Array(totalLen);
    let offset = 0;
    for (const c of recorder.chunks) {
      merged.set(c, offset);
      offset += c.length;
    }

    recorder = null;

    // Convert to base64
    const bytes = new Uint8Array(merged.buffer);
    return arrayBufferToBase64(bytes);
  }

  /**
   * Play base64-encoded audio through the speakers.
   * Returns a Promise that resolves when playback finishes.
   */
  async function playAudio(b64Data, format) {
    if (!b64Data) return;
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === 'suspended') {
      await audioCtx.resume();
    }
    const raw = base64ToArrayBuffer(b64Data);
    try {
      const audioBuffer = await audioCtx.decodeAudioData(raw);
      const src = audioCtx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(audioCtx.destination);
      return new Promise((resolve) => {
        src.onended = resolve;
        src.start(0);
      });
    } catch (err) {
      // MP3 decoding may fail on some browsers; try Blob + Audio element fallback
      console.warn('[voice] AudioContext.decodeAudioData failed, trying Audio element', err);
      return playViaElement(raw, format || 'audio/mp3');
    }
  }

  /**
   * Fallback: play via <audio> element.
   */
  function playViaElement(arrayBuf, mimeType) {
    return new Promise((resolve, reject) => {
      const blob = new Blob([arrayBuf], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
      audio.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
      audio.play().catch(reject);
    });
  }

  /**
   * Start simple energy-based "wake word" detection.
   * When sustained audio energy is detected, calls onWake().
   * This is a simple voice-activity detector (not a true keyword spotter).
   * For real wake word detection, integrate openWakeWord WASM or Porcupine Web.
   */
  function startWakeWordDetection(onWake) {
    if (!audioCtx || !mediaStream) return false;
    wakeWordEnabled = true;

    const source = audioCtx.createMediaStreamSource(mediaStream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    let activeFrames = 0;
    const THRESHOLD = 30;   // energy threshold (0-255 scale)
    const MIN_FRAMES = 3;   // consecutive active frames to trigger

    wakeWordInterval = setInterval(() => {
      if (!wakeWordEnabled) return;
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
      const avg = sum / dataArray.length;

      if (avg > THRESHOLD) {
        activeFrames++;
        if (activeFrames >= MIN_FRAMES) {
          activeFrames = 0;
          wakeWordEnabled = false; // pause detection during recording
          onWake();
        }
      } else {
        activeFrames = 0;
      }
    }, 200);

    return true;
  }

  function stopWakeWordDetection() {
    wakeWordEnabled = false;
    if (wakeWordInterval) {
      clearInterval(wakeWordInterval);
      wakeWordInterval = null;
    }
  }

  function resumeWakeWordDetection() {
    wakeWordEnabled = true;
  }

  // ── Helpers ──

  function arrayBufferToBase64(buffer) {
    let binary = '';
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const len = bytes.byteLength;
    // Process in chunks to avoid stack overflow on large arrays
    const CHUNK = 8192;
    for (let i = 0; i < len; i += CHUNK) {
      const slice = bytes.subarray(i, Math.min(i + CHUNK, len));
      binary += String.fromCharCode.apply(null, slice);
    }
    return btoa(binary);
  }

  function base64ToArrayBuffer(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  // ── Export ──
  window.VoiceEngine = {
    init,
    startRecording,
    stopRecording,
    playAudio,
    startWakeWordDetection,
    stopWakeWordDetection,
    resumeWakeWordDetection,
    get isRecording() { return isRecording; },
  };
})();

/* -- Volchino PWA client -- */
/* WebSocket client with voice support, auto-reconnect, chat log, confirm dialog. */

(function () {
  'use strict';

  // -- DOM refs --
  const chatLog    = document.getElementById('chat-log');
  const input      = document.getElementById('input');
  const form       = document.getElementById('input-bar');
  const connBadge  = document.getElementById('connection-status');
  const petMood    = document.getElementById('pet-mood');
  const petStage   = document.getElementById('pet-stage');
  const petName    = document.getElementById('pet-name');
  const confirmBar = document.getElementById('confirm-bar');
  const confirmTxt = document.getElementById('confirm-text');
  const confirmYes = document.getElementById('confirm-yes');
  const confirmNo  = document.getElementById('confirm-no');
  const micBtn     = document.getElementById('mic-btn');
  const canvas     = document.getElementById('avatar');
  const cat        = new CyberCat(canvas);

  // -- Config --
  const TOKEN = new URLSearchParams(location.search).get('token') || '';
  const WS_URL = (function () {
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return proto + '://' + location.host + '/ws?token=' + encodeURIComponent(TOKEN);
  })();

  var ws = null;
  var reconnectDelay = 1000;
  var MAX_DELAY = 30000;
  var pendingConfirmId = null;
  var wakeLock = null;
  var micInitialized = false;
  var wakeWordActive = false;

  // -- WebSocket --
  function connect() {
    ws = new WebSocket(WS_URL);
    ws.onopen = function () {
      reconnectDelay = 1000;
      connBadge.textContent = 'online';
      connBadge.className = 'badge connected';
      cat.start();
      requestWakeLock();
    };
    ws.onclose = function (e) {
      connBadge.textContent = 'offline';
      connBadge.className = 'badge disconnected';
      if (e.code === 4001) {
        addMsg('Unauthorized. Check the token in the URL.', 'err');
        return;
      }
      setTimeout(function () {
        reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_DELAY);
        connect();
      }, reconnectDelay);
    };
    ws.onerror = function () {};
    ws.onmessage = function (e) {
      var msg;
      try { msg = JSON.parse(e.data); } catch (_) { return; }
      handleMessage(msg);
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // -- Message handling --
  function handleMessage(msg) {
    switch (msg.type) {
      case 'result':
        addMsg(msg.text, msg.status === 'failed' || msg.status === 'permission_denied' ? 'err' : 'bot',
               msg.tool, msg.tokens_used, msg.stage);
        break;
      case 'pet':
        updatePet(msg);
        break;
      case 'confirm':
        showConfirm(msg.id, msg.action);
        break;
      case 'error':
        addMsg(msg.text, 'err');
        break;
      case 'transcript':
        addMsg('[you said] ' + msg.text, 'user');
        break;
      case 'voice_response':
        addMsg(msg.text, 'bot', msg.tool);
        playVoiceResponse(msg);
        break;
    }
  }

  function updatePet(p) {
    cat.setState(p.state, p.evolution_stage);
    petMood.textContent = p.mood || '';
    petStage.textContent = (p.evolution_stage || '?') + ' - day ' + (p.age_days || '?');
    if (p.pet_name) petName.textContent = p.pet_name;
  }

  // -- Chat --
  function addMsg(text, kind, tool, tokens, stage) {
    var div = document.createElement('div');
    div.className = 'msg ' + kind;
    div.textContent = text;
    if (kind === 'bot' && (tool || typeof tokens === 'number')) {
      var meta = document.createElement('span');
      meta.className = 'meta';
      var parts = [];
      if (tool) parts.push(tool);
      if (stage) parts.push(stage);
      if (typeof tokens === 'number') parts.push(tokens + ' tokens');
      meta.textContent = parts.join(' - ');
      div.appendChild(meta);
    }
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    send({ type: 'text', text: text });
    input.value = '';
  });

  // -- Confirm dialog --
  function showConfirm(id, action) {
    pendingConfirmId = id;
    confirmTxt.textContent = action;
    confirmBar.classList.remove('hidden');
  }

  function resolveConfirm(approved) {
    if (!pendingConfirmId) return;
    send({ type: 'confirm', id: pendingConfirmId, approved: approved });
    confirmBar.classList.add('hidden');
    pendingConfirmId = null;
  }

  confirmYes.addEventListener('click', function () { resolveConfirm(true); });
  confirmNo.addEventListener('click', function () { resolveConfirm(false); });

  // -- Voice: push-to-talk on mic button --

  async function initMic() {
    if (micInitialized) return true;
    var ok = await VoiceEngine.init();
    if (ok) {
      micInitialized = true;
      micBtn.title = 'Hold to talk';
    } else {
      addMsg('Microphone access denied.', 'err');
    }
    return ok;
  }

  // Press and hold to record
  micBtn.addEventListener('mousedown', startVoice);
  micBtn.addEventListener('touchstart', function (e) { e.preventDefault(); startVoice(); });
  micBtn.addEventListener('mouseup', stopVoice);
  micBtn.addEventListener('touchend', function (e) { e.preventDefault(); stopVoice(); });
  micBtn.addEventListener('mouseleave', function () { if (VoiceEngine.isRecording) stopVoice(); });

  async function startVoice() {
    var ok = await initMic();
    if (!ok) return;
    VoiceEngine.stopWakeWordDetection();
    VoiceEngine.startRecording();
    micBtn.classList.add('recording');
    micBtn.textContent = 'REC';
    cat.setState('listening');
  }

  function stopVoice() {
    if (!VoiceEngine.isRecording) return;
    var b64 = VoiceEngine.stopRecording();
    micBtn.classList.remove('recording');
    micBtn.textContent = 'MIC';
    if (b64) {
      send({ type: 'audio_chunk', data: b64 });
      cat.setState('thinking');
    } else {
      cat.setState('idle');
    }
    // Re-enable wake word after a short delay
    setTimeout(function () {
      if (micInitialized && wakeWordActive) VoiceEngine.resumeWakeWordDetection();
    }, 1000);
  }

  // -- Voice response playback --
  async function playVoiceResponse(msg) {
    if (!msg.audio) return;
    cat.setState('speaking');
    try {
      await VoiceEngine.playAudio(msg.audio, msg.format);
    } catch (err) {
      console.error('[app] TTS playback failed:', err);
    }
    cat.setState('idle');
    if (wakeWordActive) VoiceEngine.resumeWakeWordDetection();
  }

  // -- Wake word detection --
  async function enableWakeWord() {
    var ok = await initMic();
    if (!ok) return;
    wakeWordActive = true;
    VoiceEngine.startWakeWordDetection(function onWake() {
      // Voice activity detected -- start recording for 3 seconds then stop
      startVoice();
      setTimeout(function () { stopVoice(); }, 3000);
    });
  }

  // Auto-enable wake word if connection is live and mic is ready
  // User must click the mic button once to grant permission (browser requirement)
  micBtn.addEventListener('dblclick', function () {
    if (!wakeWordActive) {
      enableWakeWord();
      addMsg('[wake word detection enabled -- say something to activate]', 'bot');
    } else {
      VoiceEngine.stopWakeWordDetection();
      wakeWordActive = false;
      addMsg('[wake word detection disabled]', 'bot');
    }
  });

  // -- Screen Wake Lock --
  async function requestWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', function () { wakeLock = null; });
    } catch (_) { /* user denied or not supported */ }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      requestWakeLock();
      if (ws && ws.readyState !== WebSocket.OPEN) connect();
      send({ type: 'wake' });
    }
  });

  // -- PWA install prompt --
  var deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); deferredPrompt = e; });

  // -- Service Worker --
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  }

  // -- Boot --
  cat.start();
  connect();
})();

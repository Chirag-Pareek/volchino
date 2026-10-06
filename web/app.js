/* ── Volchino PWA client ── */
/* WebSocket client with auto-reconnect, chat log, confirm dialog, Screen Wake Lock. */

(function () {
  'use strict';

  // ── DOM refs ──
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

  // ── Config ──
  const TOKEN = new URLSearchParams(location.search).get('token') || '';
  const WS_URL = (() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`;
  })();

  let ws = null;
  let reconnectDelay = 1000;
  const MAX_DELAY = 30000;
  let pendingConfirmId = null;
  let wakeLock = null;

  // ── WebSocket ──
  function connect() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      reconnectDelay = 1000;
      connBadge.textContent = 'online';
      connBadge.className = 'badge connected';
      cat.start();
      requestWakeLock();
    };
    ws.onclose = (e) => {
      connBadge.textContent = 'offline';
      connBadge.className = 'badge disconnected';
      if (e.code === 4001) {
        addMsg('Unauthorized. Check the token in the URL.', 'err');
        return; // don't reconnect on auth failure
      }
      setTimeout(() => {
        reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_DELAY);
        connect();
      }, reconnectDelay);
    };
    ws.onerror = () => {};
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      handleMessage(msg);
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // ── Message handling ──
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
    }
  }

  function updatePet(p) {
    cat.setState(p.state, p.evolution_stage);
    petMood.textContent = p.mood || '';
    petStage.textContent = `${p.evolution_stage || '?'} · day ${p.age_days || '?'}`;
    if (p.pet_name) petName.textContent = p.pet_name;
  }

  // ── Chat ──
  function addMsg(text, kind, tool, tokens, stage) {
    const div = document.createElement('div');
    div.className = `msg ${kind}`;
    div.textContent = text;
    if (kind === 'bot' && (tool || typeof tokens === 'number')) {
      const meta = document.createElement('span');
      meta.className = 'meta';
      const parts = [];
      if (tool) parts.push(tool);
      if (stage) parts.push(stage);
      if (typeof tokens === 'number') parts.push(`${tokens} tokens`);
      meta.textContent = parts.join(' · ');
      div.appendChild(meta);
    }
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    send({ type: 'text', text });
    input.value = '';
  });

  // ── Confirm dialog ──
  function showConfirm(id, action) {
    pendingConfirmId = id;
    confirmTxt.textContent = action;
    confirmBar.classList.remove('hidden');
  }

  function resolveConfirm(approved) {
    if (!pendingConfirmId) return;
    send({ type: 'confirm', id: pendingConfirmId, approved });
    confirmBar.classList.add('hidden');
    pendingConfirmId = null;
  }

  confirmYes.addEventListener('click', () => resolveConfirm(true));
  confirmNo.addEventListener('click', () => resolveConfirm(false));

  // ── Mic stub (Phase 4) ──
  micBtn.addEventListener('click', () => {
    // TODO(phase-4): start in-browser wake-word detection or manual push-to-talk.
    // 1. Request getUserMedia audio stream.
    // 2. Run openWakeWord / Porcupine Web detection.
    // 3. On detection, record chunk, send as binary frame over WS.
    // 4. Server runs faster-whisper STT, returns transcript, pipeline continues.
    addMsg('Voice input is coming in Phase 4.', 'bot');
  });

  // ── Screen Wake Lock ──
  async function requestWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch { /* user denied or not supported */ }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      requestWakeLock();
      if (ws && ws.readyState !== WebSocket.OPEN) connect();
      send({ type: 'wake' });
    }
  });

  // ── PWA install prompt ──
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredPrompt = e; });

  // ── Service Worker ──
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // ── Boot ──
  cat.start();
  connect();
})();

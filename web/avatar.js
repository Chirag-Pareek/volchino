/* ── CyberCat canvas avatar ── */
/* Draws a procedural cat on a <canvas> with visual states and evolution stages. */

const COLORS = {
  bg: '#0d1117', surface: '#161b22', border: '#30363d',
  accent: '#58a6ff', ok: '#3fb950', warn: '#d29922', err: '#f85149',
  body: '#c9d1d9', bodyDark: '#8b949e', eye: '#58a6ff', pupil: '#0d1117',
  nose: '#f0a0b0', blush: '#d29922',
};

const EVO_SCALE = { egg: 0.55, baby: 0.7, young: 0.88, adult: 1.0 };
const EVO_FEATURES = {
  egg: { ears: false, tail: false, whiskers: false, label: '[egg]' },
  baby: { ears: true, tail: false, whiskers: false, label: '[baby]' },
  young: { ears: true, tail: true, whiskers: true, label: '[young]' },
  adult: { ears: true, tail: true, whiskers: true, label: '[adult]' },
};

class CyberCat {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'sleeping';
    this.evolution = 'egg';
    this._frame = 0;
    this._animId = null;
    this._blink = 0;
    this._breathe = 0;
  }

  setState(state, evolution) {
    this.state = state || this.state;
    this.evolution = evolution || this.evolution;
  }

  start() { if (!this._animId) this._loop(); }
  stop() { if (this._animId) { cancelAnimationFrame(this._animId); this._animId = null; } }

  _loop() {
    this._frame++;
    this._breathe = Math.sin(this._frame * 0.03) * 3;
    if (this._frame % 180 === 0) this._blink = 6;
    if (this._blink > 0) this._blink--;
    this._draw();
    this._animId = requestAnimationFrame(() => this._loop());
  }

  _draw() {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const scale = EVO_SCALE[this.evolution] || 1;
    const feat = EVO_FEATURES[this.evolution] || EVO_FEATURES.adult;

    ctx.clearRect(0, 0, W, H);

    // state-specific background glow
    const glow = this._stateGlow();
    if (glow) {
      const grad = ctx.createRadialGradient(cx, cy, 20, cx, cy, 120);
      grad.addColorStop(0, glow + '30');
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);
    }

    ctx.save();
    ctx.translate(cx, cy + this._breathe);
    ctx.scale(scale, scale);

    if (this.evolution === 'egg') {
      this._drawEgg(ctx);
    } else {
      if (feat.tail) this._drawTail(ctx);
      this._drawBody(ctx);
      if (feat.ears) this._drawEars(ctx);
      this._drawFace(ctx);
      if (feat.whiskers) this._drawWhiskers(ctx);
    }

    ctx.restore();

    // state label
    const label = this._stateLabel();
    if (label) {
      ctx.font = '13px system-ui';
      ctx.fillStyle = COLORS.bodyDark;
      ctx.textAlign = 'center';
      ctx.fillText(label, cx, H - 12);
    }
  }

  _drawEgg(ctx) {
    ctx.beginPath();
    ctx.ellipse(0, 0, 40, 50, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.body;
    ctx.fill();
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 2;
    ctx.stroke();
    // crack line
    ctx.beginPath();
    ctx.moveTo(-15, -8); ctx.lineTo(-5, 2); ctx.lineTo(5, -6); ctx.lineTo(15, 4);
    ctx.strokeStyle = COLORS.bodyDark;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // eyes peeking
    if (this.state !== 'sleeping') {
      ctx.fillStyle = COLORS.eye;
      ctx.beginPath(); ctx.arc(-10, 10, 4, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(10, 10, 4, 0, Math.PI * 2); ctx.fill();
    }
  }

  _drawBody(ctx) {
    ctx.beginPath();
    ctx.ellipse(0, 10, 50, 55, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.body;
    ctx.fill();
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  _drawEars(ctx) {
    // left ear
    ctx.beginPath();
    ctx.moveTo(-35, -35); ctx.lineTo(-20, -65); ctx.lineTo(-5, -35);
    ctx.fillStyle = COLORS.body;
    ctx.fill();
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 2;
    ctx.stroke();
    // inner ear
    ctx.beginPath();
    ctx.moveTo(-30, -38); ctx.lineTo(-20, -56); ctx.lineTo(-10, -38);
    ctx.fillStyle = COLORS.nose;
    ctx.globalAlpha = 0.3;
    ctx.fill();
    ctx.globalAlpha = 1;
    // right ear
    ctx.beginPath();
    ctx.moveTo(35, -35); ctx.lineTo(20, -65); ctx.lineTo(5, -35);
    ctx.fillStyle = COLORS.body;
    ctx.fill();
    ctx.strokeStyle = COLORS.border;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(30, -38); ctx.lineTo(20, -56); ctx.lineTo(10, -38);
    ctx.fillStyle = COLORS.nose;
    ctx.globalAlpha = 0.3;
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  _drawFace(ctx) {
    const eyeY = -10;
    const eyeOpen = this._blink <= 0 && this.state !== 'sleeping';

    if (eyeOpen) {
      // eyes
      ctx.fillStyle = '#fff';
      ctx.beginPath(); ctx.ellipse(-18, eyeY, 10, 11, 0, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.ellipse(18, eyeY, 10, 11, 0, 0, Math.PI * 2); ctx.fill();
      // pupils
      const px = this.state === 'thinking' ? Math.sin(this._frame * 0.05) * 3 : 0;
      ctx.fillStyle = COLORS.pupil;
      ctx.beginPath(); ctx.arc(-18 + px, eyeY + 1, 5, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(18 + px, eyeY + 1, 5, 0, Math.PI * 2); ctx.fill();
      // iris highlight
      ctx.fillStyle = COLORS.eye;
      ctx.beginPath(); ctx.arc(-18 + px - 1, eyeY - 1, 2, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(18 + px - 1, eyeY - 1, 2, 0, Math.PI * 2); ctx.fill();
    } else {
      // closed / sleeping eyes
      ctx.strokeStyle = COLORS.bodyDark;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(-26, eyeY); ctx.quadraticCurveTo(-18, eyeY + 6, -10, eyeY); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(10, eyeY); ctx.quadraticCurveTo(18, eyeY + 6, 26, eyeY); ctx.stroke();
    }

    // nose
    ctx.fillStyle = COLORS.nose;
    ctx.beginPath();
    ctx.moveTo(0, 5); ctx.lineTo(-4, 1); ctx.lineTo(4, 1); ctx.closePath();
    ctx.fill();

    // mouth
    const mouthY = 10;
    ctx.strokeStyle = COLORS.bodyDark;
    ctx.lineWidth = 1.5;
    if (this.state === 'success') {
      ctx.beginPath(); ctx.arc(-6, mouthY, 6, 0, Math.PI); ctx.stroke();
      ctx.beginPath(); ctx.arc(6, mouthY, 6, 0, Math.PI); ctx.stroke();
    } else if (this.state === 'error') {
      ctx.beginPath(); ctx.arc(0, mouthY + 8, 8, Math.PI, 0); ctx.stroke();
    } else {
      ctx.beginPath(); ctx.moveTo(-6, mouthY); ctx.quadraticCurveTo(0, mouthY + 5, 6, mouthY); ctx.stroke();
    }

    // blush on success
    if (this.state === 'success') {
      ctx.fillStyle = COLORS.blush;
      ctx.globalAlpha = 0.2;
      ctx.beginPath(); ctx.ellipse(-28, 5, 8, 5, 0, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.ellipse(28, 5, 8, 5, 0, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  _drawWhiskers(ctx) {
    ctx.strokeStyle = COLORS.bodyDark;
    ctx.lineWidth = 1;
    for (const dir of [-1, 1]) {
      for (const dy of [-4, 2, 8]) {
        ctx.beginPath();
        ctx.moveTo(dir * 22, dy);
        ctx.lineTo(dir * 58, dy - 4 + Math.sin(this._frame * 0.04 + dy) * 2);
        ctx.stroke();
      }
    }
  }

  _drawTail(ctx) {
    ctx.strokeStyle = COLORS.body;
    ctx.lineWidth = 6;
    ctx.lineCap = 'round';
    const sway = Math.sin(this._frame * 0.04) * 12;
    ctx.beginPath();
    ctx.moveTo(45, 30);
    ctx.quadraticCurveTo(70 + sway, 10, 60 + sway, -25);
    ctx.stroke();
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  _stateGlow() {
    return {
      sleeping: null, idle: null, listening: COLORS.accent,
      thinking: COLORS.warn, working: COLORS.accent,
      success: COLORS.ok, error: COLORS.err,
    }[this.state] || null;
  }

  _stateLabel() {
    return {
      sleeping: '~ sleeping', idle: null, listening: ')) listening',
      thinking: '.. thinking', working: '>> working',
      success: '[ok] done!', error: '[!] error',
    }[this.state] || null;
  }
}

window.CyberCat = CyberCat;

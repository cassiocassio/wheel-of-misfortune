/* Wheel of Misfortune — client (SPEC §7).
   The SERVER is the outcome authority: a flick POSTs /api/spin, the server draws
   the task, and the wheel only *animates to* that result. Flick velocity buys
   rotations + duration (drama), never the outcome. */
'use strict';

// --------------------------------------------------------------------------- //
//  Tiny helpers                                                                //
// --------------------------------------------------------------------------- //
const $ = (id) => document.getElementById(id);
const rnd = (a, b) => a + Math.random() * (b - a);
const initial = (name) => ((name || '').trim()[0] || '?').toUpperCase();
const REDUCE = matchMedia('(prefers-reduced-motion: reduce)').matches;

async function api(path, body) {
  const opt = body
    ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) {
    const err = (data && (data.detail?.error || data.error)) || `HTTP ${r.status}`;
    throw Object.assign(new Error(err), { status: r.status, error: err });
  }
  return data;
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('on'), 1800);
}

// --------------------------------------------------------------------------- //
//  Sound — Web Audio, synthesised so it works offline with zero asset files.   //
//  (Drop real samples into /audio later and swap playSfx if you prefer.)        //
// --------------------------------------------------------------------------- //
const Sound = (() => {
  let ctx = null;
  let muted = localStorage.getItem('wheel_sound') === '0';
  function unlock() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) ctx = new AC();
    }
    if (ctx && ctx.state === 'suspended') ctx.resume();
  }
  function blip({ freq = 600, type = 'sine', dur = 0.12, vol = 0.2, glide = 0 }) {
    if (muted || !ctx) return;
    const t = ctx.currentTime;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    if (glide) o.frequency.exponentialRampToValueAtTime(Math.max(60, freq * glide), t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(ctx.destination);
    o.start(t);
    o.stop(t + dur + 0.03);
  }
  const cues = {
    pop:     () => blip({ freq: rnd(520, 660), type: 'triangle', dur: 0.12, vol: 0.25, glide: 1.7 }),
    ding:    () => blip({ freq: rnd(860, 1000), type: 'sine', dur: 0.18, vol: 0.22 }),
    boing:   () => blip({ freq: rnd(200, 260), type: 'sawtooth', dur: 0.22, vol: 0.18, glide: 2.3 }),
    coin:    () => { blip({ freq: 988, type: 'square', dur: 0.06, vol: 0.16 }); setTimeout(() => blip({ freq: 1319, type: 'square', dur: 0.13, vol: 0.16 }), 60); },
    sparkle: () => [0, 70, 140].forEach((d) => setTimeout(() => blip({ freq: rnd(900, 1500), type: 'sine', dur: 0.1, vol: 0.14 }), d)),
  };
  return {
    unlock,
    isMuted: () => muted,
    toggle() { muted = !muted; localStorage.setItem('wheel_sound', muted ? '0' : '1'); return muted; },
    sfx(cue) { unlock(); (cues[cue] || cues.pop)(); },
    tick() { blip({ freq: rnd(1150, 1450), type: 'square', dur: 0.018, vol: 0.05 }); },
    chime() { unlock(); [523, 659, 784, 1046].forEach((f, i) => setTimeout(() => blip({ freq: f, type: 'triangle', dur: 0.22, vol: 0.2 }), i * 95)); },
  };
})();

// --------------------------------------------------------------------------- //
//  Global state pulled from the server                                         //
// --------------------------------------------------------------------------- //
const G = {
  config: null, tasks: [], kinds: {}, scale: {}, players: [], tints: {},
  player: null, rerollsPerSpin: 1,
  sectors: [], chosen: null, rerollsLeft: 1, spinning: false, angle: 0,
};

const effortPoints = (token) => (typeof token === 'number' ? token : (G.scale[token] || 0));
const kindColour = (k) => (G.kinds[k] && (G.kinds[k].hue || G.kinds[k].colour)) || '#888';
const kindLabel = (k) => (G.kinds[k] && G.kinds[k].label) || k;
// kinds carry an icon *name* — give it a glyph so kind is never colour-only (SPEC §9 a11y)
const KIND_ICONS = { sparkle: '✨', broom: '🧹', shirt: '👕', trash: '🗑️', spray: '🧴', dot: '●' };
const kindIcon = (k) => KIND_ICONS[(G.kinds[k] && G.kinds[k].icon) || 'dot'] || '●';
const wheelPool = () => G.tasks.filter((t) => t.in_play && t.on_wheel);
const dailyPool = () => G.tasks.filter((t) => t.in_play && !t.on_wheel);

// --------------------------------------------------------------------------- //
//  Boot                                                                        //
// --------------------------------------------------------------------------- //
async function boot() {
  const state = await api('/api/state');
  G.config = state.config;
  G.tasks = state.tasks;
  G.kinds = state.config.kinds || {};
  G.scale = state.config.effort_scale || { XS: 2, S: 3, M: 4, L: 5 };
  G.players = state.config.players || [];
  G.tints = state.config.player_tints || {};
  G.rerollsPerSpin = state.config.rerolls_per_spin ?? 1;

  buildPicker();
  sizeWheel();
  drawWheel(0);
  initMute();
}

function buildPicker() {
  const wrap = $('profiles');
  wrap.innerHTML = '';
  G.players.forEach((name) => {
    const b = document.createElement('button');
    b.className = 'profile';
    const tint = G.tints[name] || '#7d8fa6';
    b.innerHTML = `<div class="avatar" style="background:${tint}">${initial(name)}</div><div class="pname">${name}</div>`;
    b.onclick = () => { Sound.unlock(); startTurn(name); };
    wrap.appendChild(b);
  });
}

function show(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('on'));
  $(id).classList.add('on');
}

function startTurn(name) {
  G.player = name;
  $('whoName').textContent = name;
  const dot = $('whoDot');
  dot.textContent = initial(name);
  dot.style.background = G.tints[name] || '#7d8fa6';
  resetCard();
  G.sectors = pickSectors(null);
  drawWheel(G.angle);
  $('hint').textContent = 'Flick the wheel';
  show('screen-app');
  switchView('view-spin');
  refreshDashboard();
}

// --------------------------------------------------------------------------- //
//  Wheel                                                                       //
// --------------------------------------------------------------------------- //
const cv = $('wheel');
const cx = cv.getContext('2d');
const DPR = Math.min(2, window.devicePixelRatio || 1);

function sizeWheel() {
  const s = Math.min(330, window.innerWidth - 60);
  cv.style.width = s + 'px';
  cv.style.height = s + 'px';
  cv.width = s * DPR;
  cv.height = s * DPR;
}

function pickSectors(winner) {
  // Visual candidate pool (cap 8). The winner — when known — is guaranteed in.
  let pool = wheelPool().slice();
  for (let i = pool.length - 1; i > 0; i--) { const j = (Math.random() * (i + 1)) | 0; [pool[i], pool[j]] = [pool[j], pool[i]]; }
  if (winner) {
    pool = pool.filter((t) => t.id !== winner.id);
    pool = [winner, ...pool].slice(0, 8);
  } else {
    pool = pool.slice(0, 8);
  }
  for (let i = pool.length - 1; i > 0; i--) { const j = (Math.random() * (i + 1)) | 0; [pool[i], pool[j]] = [pool[j], pool[i]]; }
  return pool.length ? pool : wheelPool().slice(0, 1);
}

function drawWheel(a) {
  G.angle = a;
  const n = G.sectors.length || 1;
  const W = cv.width, R = W / 2;
  cx.clearRect(0, 0, W, W);
  cx.save();
  cx.translate(R, R);
  cx.rotate(a);
  const step = (2 * Math.PI) / n;
  for (let i = 0; i < n; i++) {
    const t = G.sectors[i];
    cx.beginPath(); cx.moveTo(0, 0); cx.arc(0, 0, R - 2, i * step, (i + 1) * step); cx.closePath();
    cx.fillStyle = t ? kindColour(t.kind) : '#333'; cx.fill();
    cx.strokeStyle = 'rgba(0,0,0,.25)'; cx.lineWidth = 2; cx.stroke();
    if (t) {
      cx.save(); cx.rotate(i * step + step / 2); cx.textAlign = 'right';
      cx.fillStyle = 'rgba(15,12,20,.92)';
      cx.font = `700 ${Math.max(9, R * 0.058)}px 'Hanken Grotesk',sans-serif`;
      const nm = t.name.length > 18 ? t.name.slice(0, 17) + '…' : t.name;
      cx.fillText(`${kindIcon(t.kind)} ${nm}`, R - 16, 4); cx.restore();
    }
  }
  cx.restore();
}

function targetAngleFor(idx) {
  const n = G.sectors.length, step = (2 * Math.PI) / n;
  const centre = idx * step + step / 2;            // pointer sits at top = -PI/2
  return -Math.PI / 2 - centre;
}

function animateTo(idx, power, done) {
  // whole rotations only — a fractional count would offset the final resting
  // angle and the wheel would not land on the winning sector. Reduced-motion:
  // no extra spins and a quick settle, but still land on the result.
  const turns = REDUCE ? 0 : Math.floor(4 + Math.min(4, power * 3) + Math.random());
  const start = G.angle;
  const base = targetAngleFor(idx);
  // wrap forward to the next equivalent angle, then add full turns
  let end = base;
  while (end < start) end += 2 * Math.PI;
  end += turns * 2 * Math.PI;
  const dur = REDUCE ? 350 : 2600 + power * 900, t0 = performance.now();
  let lastTick = 0;
  (function frame(now) {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);              // ease-out cubic
    const ang = start + (end - start) * e;
    drawWheel(ang);
    if (now - lastTick > 70 + 240 * e) { Sound.tick(); lastTick = now; }
    if (k < 1) requestAnimationFrame(frame);
    else done();
  })(t0);
}

async function flick(power) {
  if (G.spinning || !G.player) return;
  G.spinning = true;
  resetCard();
  $('hint').textContent = '…';
  let res;
  try {
    res = await api('/api/spin', { player: G.player, spin_token: crypto.randomUUID() });
  } catch (e) {
    G.spinning = false;
    if (e.status === 409) { $('hint').textContent = 'All done this week 🎉'; }
    else { $('hint').textContent = e.error || 'Spin failed'; }
    return;
  }
  const winner = res.task;
  G.sectors = pickSectors(winner);
  const idx = G.sectors.findIndex((t) => t.id === winner.id);
  drawWheel(G.angle);
  animateTo(idx, power, () => {
    G.spinning = false;
    G.rerollsLeft = G.rerollsPerSpin;
    showCard(winner, res.credit_remaining);
  });
}

// flick / drag
let down = null;
cv.addEventListener('pointerdown', (e) => { Sound.unlock(); down = { x: e.clientX, y: e.clientY, t: performance.now() }; });
cv.addEventListener('pointerup', (e) => {
  if (!down) return;
  const dx = e.clientX - down.x, dy = e.clientY - down.y;
  const dist = Math.hypot(dx, dy), dt = performance.now() - down.t;
  const power = Math.max(0, Math.min(1, (dist / dt) * 2.2));
  down = null;
  flick(power || 0.2);
});
cv.addEventListener('click', () => { if (!G.spinning && !down) flick(0.4); });

// --------------------------------------------------------------------------- //
//  Task card                                                                   //
// --------------------------------------------------------------------------- //
const card = $('card');
function resetCard() {
  card.classList.remove('on');
  $('acceptRow').style.display = 'flex';
  $('doneRow').style.display = 'none';
}
function showCard(t, credit, opts = {}) {
  // A *claimed* task was chosen deliberately: no reroll, and it goes straight to
  // "I'm done" (you either already did it, or you picked it to do now).
  const claimed = !!opts.claimed;
  G.chosen = t;
  $('reroll').style.display = claimed ? 'none' : '';
  $('rr').textContent = G.rerollsLeft;
  $('reroll').disabled = G.rerollsLeft <= 0;
  const tag = $('kindtag');
  tag.textContent = `${kindIcon(t.kind)} ${kindLabel(t.kind)}`;
  tag.style.background = kindColour(t.kind);
  $('taskName').textContent = t.name;
  $('taskRoom').textContent = t.room || '';
  $('taskEffort').textContent = `${t.effort} (${effortPoints(t.effort)})`;
  const pips = $('taskPips'); pips.innerHTML = '';
  for (let i = 0; i < 3; i++) { const d = document.createElement('span'); d.className = 'pip' + (i < t.ick ? ' on' : ''); pips.appendChild(d); }
  $('acceptRow').style.display = claimed ? 'none' : 'flex';
  $('doneRow').style.display = claimed ? 'block' : 'none';
  $('hint').textContent = typeof credit === 'number' ? `${credit > 0 ? credit.toFixed(0) + ' effort still owed' : 'fair share met — bonus round'}` : '';
  card.classList.add('on');
}

$('reroll').onclick = async () => {
  if (G.rerollsLeft <= 0 || !G.chosen) return;
  const old = G.chosen;
  $('reroll').disabled = true;
  let res;
  try {
    res = await api('/api/reroll', { player: G.player, task_id: old.id });
  } catch (e) {
    if (e.status === 403) { G.rerollsLeft = 0; $('rr').textContent = 0; toast('No rerolls left — it’s yours'); }
    else toast(e.error || 'Reroll failed');
    return;
  }
  G.rerollsLeft -= 1;
  G.spinning = true;
  G.sectors = pickSectors(res.task);
  const idx = G.sectors.findIndex((t) => t.id === res.task.id);
  animateTo(idx, 0.5, () => { G.spinning = false; showCard(res.task); });
};

$('accept').onclick = async () => {
  if (!G.chosen) return;
  try { await api('/api/accept', { player: G.player, task_id: G.chosen.id }); }
  catch (e) { toast(e.error || 'Could not accept'); return; }
  $('acceptRow').style.display = 'none';
  $('doneRow').style.display = 'block';
};

$('done').onclick = async () => {
  if (!G.chosen) return;
  const t = G.chosen;
  let res;
  try { res = await api('/api/done', { player: G.player, task_id: t.id }); }
  catch (e) { toast(e.error || 'Could not mark done'); return; }
  burst(kindColour(t.kind), G.tints[G.player] || '#E8B84B');
  Sound.chime();
  resetCard();
  await refreshDashboard();
  G.sectors = pickSectors(null);
  drawWheel(G.angle);
  $('hint').textContent = res.credit_remaining > 0
    ? `Nice. ${res.credit_remaining.toFixed(0)} effort to fair share. Flick again`
    : 'Fair share met 🎉 Flick again or rest';
};

// --------------------------------------------------------------------------- //
//  Daily bonus button + bottom sheet                                          //
// --------------------------------------------------------------------------- //
$('dailyBtn').onclick = () => { Sound.unlock(); openSheet('daily'); };
$('claimBtn').onclick = () => { Sound.unlock(); openSheet('claim'); };
$('scrim').onclick = closeSheet;

async function openSheet(mode) {
  const list = $('dailyList');
  list.innerHTML = '';
  if (mode === 'claim') {
    $('sheetTitle').textContent = 'Weekly chores';
    $('sheetSub').textContent = 'Claim one you’ve already done, or pick one to do now — it lands on your pile.';
    let st;
    try { st = await api('/api/state'); } catch (e) { toast(e.error || 'Could not load chores'); return; }
    const claimed = new Set(Object.keys((st.assignments || {})[st.current_week] || {}));
    const avail = (st.tasks || []).filter((t) => t.in_play && t.on_wheel && !claimed.has(t.id));
    if (!avail.length) {
      list.innerHTML = '<p class="sheet-empty">Every chore is taken — a fair week. 🎉</p>';
    }
    avail.forEach((t) => {
      const b = document.createElement('button');
      b.className = 'daily-item';
      b.innerHTML = `<span class="swatch" style="background:${kindColour(t.kind)}">${kindIcon(t.kind)}</span><span>${t.name}</span><span class="cnt">${effortPoints(t.effort)}</span>`;
      b.onclick = () => claimTask(t);
      list.appendChild(b);
    });
  } else {
    $('sheetTitle').textContent = 'Daily chores';
    $('sheetSub').textContent = 'One tap logs it — a happy noise, a weekly tally. Never on the wheel, never on the pile.';
    dailyPool().forEach((t) => {
      const b = document.createElement('button');
      b.className = 'daily-item';
      b.innerHTML = `<span class="swatch" style="background:${kindColour(t.kind)}">${kindIcon(t.kind)}</span><span>${t.name}</span><span class="cnt" id="cnt-${t.id}"></span>`;
      b.onclick = () => logDaily(t, b);
      list.appendChild(b);
    });
  }
  $('scrim').classList.add('on');
  $('sheet').classList.add('on');
}
function closeSheet() {
  $('scrim').classList.remove('on');
  $('sheet').classList.remove('on');
}

async function logDaily(t, btn) {
  let res;
  try { res = await api('/api/daily', { player: G.player, task_id: t.id }); }
  catch (e) { toast(e.error || 'Could not log'); return; }
  Sound.sfx(res.sfx);
  btn.classList.remove('tapped'); void btn.offsetWidth; btn.classList.add('tapped');
  const cnt = $('cnt-' + t.id); if (cnt) cnt.textContent = '✓';
  dailyPop(btn);
  updateDailyTally(res.daily_count);
}

async function claimTask(t) {
  let res;
  try { res = await api('/api/claim', { player: G.player, task_id: t.id }); }
  catch (e) {
    toast(e.status === 409 ? 'That chore is already taken' : (e.error || 'Could not claim'));
    return;
  }
  closeSheet();
  Sound.sfx('pop');
  G.rerollsLeft = 0;
  switchView('view-spin');
  showCard(res.task, res.credit_remaining, { claimed: true });
}
function updateDailyTally(n) {
  if (typeof n === 'number') $('dailyTally').textContent = `${n} today`;
}

// --------------------------------------------------------------------------- //
//  Dashboard + piles                                                           //
// --------------------------------------------------------------------------- //
function switchView(view) {
  document.querySelectorAll('.view').forEach((v) => (v.style.display = 'none'));
  $(view).style.display = 'block';
  document.querySelectorAll('.tab').forEach((t) => {
    const on = t.dataset.view === view;
    t.classList.toggle('on', on);
    t.setAttribute('aria-selected', String(on));
  });
  if (view === 'view-dash') refreshDashboard();
}
document.querySelectorAll('.tab').forEach((t) => (t.onclick = () => switchView(t.dataset.view)));
$('back').onclick = () => { resetCard(); show('screen-pick'); };

const PXPER = 13; // pixels of pile height per effort point

async function refreshDashboard() {
  let d;
  try { d = await api('/api/dashboard'); } catch (_) { return; }
  G.dash = d;
  // daily tally for the active player
  if (G.player && d.players[G.player]) updateDailyTally(d.players[G.player].daily_taps);
  renderPiles('piles', 'foot', 'fairline', d, false);
  renderPiles('dpiles', 'dfoot', 'dfairline', d, true);
  renderPlayerCards(d);
  renderGaps(d);
  $('weekLabel').textContent = d.week;
}

function renderPiles(pilesId, footId, fairId, d, withNames) {
  const piles = $(pilesId), foot = $(footId);
  [...piles.querySelectorAll('.col')].forEach((c) => c.remove());
  foot.innerHTML = '';
  G.players.forEach((p) => {
    const pd = d.players[p];
    const col = document.createElement('div'); col.className = 'col';
    (pd.tiles || []).forEach((tile) => {
      const el = document.createElement('div'); el.className = 'tile';
      el.style.height = (tile.effort * PXPER) + 'px';
      el.style.background = kindColour(tile.kind);
      el.textContent = tile.effort >= 4 ? `${kindIcon(tile.kind)} ${tile.name}` : kindIcon(tile.kind);
      el.title = `${kindLabel(tile.kind)} · ${tile.name} · effort ${tile.effort}`;
      col.appendChild(el);
    });
    const base = document.createElement('div'); base.className = 'colbase';
    base.style.background = pd.tint || G.tints[p]; col.appendChild(base);
    piles.appendChild(col);

    const pf = document.createElement('div'); pf.className = 'pf';
    const credit = pd.credit_remaining;
    const creditTxt = credit > 0
      ? `<span class="owe">owes ${credit.toFixed(0)}</span>`
      : `<span class="ahead">+${Math.abs(credit).toFixed(0)}</span>`;
    pf.innerHTML = `<div class="nm" style="color:${pd.tint || G.tints[p]}">${p}</div>
      <div class="tot"><b>${pd.spent}</b> effort · ${pd.jobs} jobs<br>${creditTxt}</div>`;
    foot.appendChild(pf);
  });
  const avg = d.fair_share_line || 0;
  $(fairId).style.bottom = (avg * PXPER + 6) + 'px';
}

function renderPlayerCards(d) {
  const wrap = $('pcards'); wrap.innerHTML = '';
  G.players.forEach((p) => {
    const pd = d.players[p];
    const c = document.createElement('div'); c.className = 'pcard';
    const credit = pd.credit_remaining;
    const creditRow = credit > 0
      ? `<span class="row-l">to fair share</span><b class="owe">${credit.toFixed(0)}</b>`
      : `<span class="row-l">ahead by</span><b class="ahead">${Math.abs(credit).toFixed(0)}</b>`;
    c.innerHTML = `
      <div class="hd"><span class="dot" style="background:${pd.tint || G.tints[p]}">${initial(p)}</span>${p}</div>
      <div class="row"><span>effort this week</span><b>${pd.spent}</b></div>
      <div class="row">${creditRow}</div>
      <div class="row"><span>banked</span><b>${(pd.banked || 0).toFixed(0)}</b></div>
      <div class="row"><span>ick this week</span><b>${pd.ick_spent}</b></div>
      <div class="row"><span>daily taps</span><b>${pd.daily_taps}</b></div>
      <div class="row"><span>good streak</span><b>${pd.streak}×</b></div>
      <div class="row"><span>lifetime effort</span><b>${pd.lifetime_effort}</b></div>`;
    wrap.appendChild(c);
  });
}

function renderGaps(d) {
  const wrap = $('gaps');
  [...wrap.querySelectorAll('.gap')].forEach((g) => g.remove());
  (d.gaps || []).slice().sort((a, b) => {
    const aw = a.weeks_ago == null ? 1e9 : a.weeks_ago;
    const bw = b.weeks_ago == null ? 1e9 : b.weeks_ago;
    return bw - aw;
  }).forEach((g) => {
    const row = document.createElement('div'); row.className = 'gap';
    const when = g.last_player == null
      ? '<span class="never">never done</span>'
      : `last by ${g.last_player}, ${g.weeks_ago === 0 ? 'this week' : g.weeks_ago + 'w ago'}`;
    row.innerHTML = `<span class="swatch" style="background:${kindColour(g.kind)}">${kindIcon(g.kind)}</span>
      <span class="g-name">${g.name}</span><span class="g-when">${when}</span>`;
    wrap.appendChild(row);
  });
}

// --------------------------------------------------------------------------- //
//  Mute toggle                                                                 //
// --------------------------------------------------------------------------- //
function initMute() {
  const b = $('mute');
  const paint = () => {
    const m = Sound.isMuted();
    b.innerHTML = m ? '&#128263;' : '&#128266;';
    b.setAttribute('aria-pressed', String(m));
  };
  paint();
  b.onclick = () => { const m = Sound.toggle(); if (!m) Sound.sfx('ding'); paint(); };
}

// --------------------------------------------------------------------------- //
//  Confetti                                                                    //
// --------------------------------------------------------------------------- //
const fx = $('fx'), fc = fx.getContext('2d');
function rs() { fx.width = innerWidth; fx.height = innerHeight; }
rs();
addEventListener('resize', () => { rs(); sizeWheel(); drawWheel(G.angle); });
let parts = [];
function burst(c1, c2) {
  if (REDUCE) return;  // sound still plays; skip the motion
  const cols = [c1, c2, '#E8B84B', '#f4ecff'];
  for (let i = 0; i < 90; i++) parts.push({
    x: innerWidth / 2, y: innerHeight * 0.42,
    vx: (Math.random() - 0.5) * 11, vy: Math.random() * -13 - 4,
    g: 0.34, s: 5 + Math.random() * 6, c: cols[i % cols.length], a: 1, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
  });
  if (parts.length <= 90) tick();
}
function dailyPop(btn) {
  if (REDUCE) return;  // sound still plays; skip the motion
  const r = btn.getBoundingClientRect();
  const cx0 = r.left + r.width / 2, cy0 = r.top + r.height / 2;
  for (let i = 0; i < 26; i++) parts.push({
    x: cx0, y: cy0, vx: (Math.random() - 0.5) * 7, vy: Math.random() * -8 - 2,
    g: 0.3, s: 4 + Math.random() * 4, c: ['#E8B84B', '#36b36b', '#f4ecff'][i % 3], a: 1, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
  });
  if (parts.length <= 26) tick();
}
function tick() {
  fc.clearRect(0, 0, fx.width, fx.height);
  parts.forEach((p) => {
    p.vy += p.g; p.x += p.vx; p.y += p.vy; p.r += p.vr; p.a -= 0.012;
    fc.save(); fc.globalAlpha = Math.max(0, p.a); fc.translate(p.x, p.y); fc.rotate(p.r);
    fc.fillStyle = p.c; fc.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.6); fc.restore();
  });
  parts = parts.filter((p) => p.a > 0 && p.y < fx.height + 40);
  if (parts.length) requestAnimationFrame(tick); else fc.clearRect(0, 0, fx.width, fx.height);
}

// global first-gesture audio unlock (iOS)
['pointerdown', 'keydown'].forEach((ev) => addEventListener(ev, () => Sound.unlock(), { once: true }));

// PWA: cache the shell so add-to-home-screen launches instantly on the LAN.
if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

function showBootError(e) {
  console.error(e);
  const wrap = $('profiles');
  if (!wrap) return;
  wrap.innerHTML = '';
  const box = document.createElement('div');
  box.style.cssText = 'text-align:center;color:var(--muted);display:flex;flex-direction:column;gap:14px;align-items:center';
  const msg = document.createElement('p');
  msg.style.margin = '0';
  msg.textContent = 'Couldn’t reach the server. Is it running on the LAN?';
  const btn = document.createElement('button');
  btn.textContent = 'Retry';
  btn.style.cssText = 'padding:12px 24px;border:0;border-radius:12px;background:var(--gold);color:#1a1206;font-weight:700;font-size:16px;cursor:pointer';
  btn.onclick = () => { wrap.textContent = 'Loading…'; boot().catch(showBootError); };
  box.append(msg, btn);
  wrap.appendChild(box);
}

boot().catch(showBootError);

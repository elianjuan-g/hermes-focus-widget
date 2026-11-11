/**
 * Hermes Focus Widget — Desktop surface.
 *
 * Three surfaces, all reading the same loopback state server
 * (http://127.0.0.1:<port>/api/state, served by the agent-side plugin):
 *
 * 1. A status-bar chip: a small dot + time left (no emoji), e.g.
 *    "24:48". Clicking it opens a small popover with today/week/streak
 *    and a link to the full page. The chip is the glanceable surface;
 *    it polls the same endpoint as the other surfaces (one shared 2s poll).
 *
 * 2. A small floating widget pinned to the Desktop window
 *    (bottom-right, draggable, position saved in plugin-scoped
 *    storage). It polls the state endpoint directly with a plain
 *    fetch; the server sends `Access-Control-Allow-Origin: *` and
 *    requires no login, so the poll works for local installs. If the
 *    server cannot be reached (for example a remote backend), the
 *    floating widget says so and offers to open the full page instead.
 *
 * 3. A "Focus Widget" page (sidebar nav + command palette) and a
 *    separate settings page, embedding the widget UI in the SDK's
 *    SandboxedFrame — the same pattern as the Manas Aegis HUD page.
 *    The page is same-origin with the widget server, so its polling
 *    just works.
 *
 * Phase changes are announced with host.notify (in-app toast) and,
 * when the user is away, ctx.os.notify (native OS notification).
 *
 * Design (locked, MyLord 12 Nov 2026): minimal, modern, cloud-light
 * only. One focus element — the countdown. One thin 2px progress ring
 * (no bar). One calm accent, muted teal #0F766E; state is told by
 * accent + phase label, never by extra colour. Stats live behind one
 * tap. Controls are icon-only Lucide-style SVGs (stroke 2, 24px grid,
 * currentColor), 44px targets. No emoji anywhere.
 *
 * Stays inside the plugin SDK surface: static imports only from
 * '@hermes/plugin-sdk' and 'react/jsx-runtime', no eval, no prototype
 * patching, no injected markup into the host UI (the floating widget
 * is its own root element appended to the body, like the balance
 * widget).
 *
 * Defensive loading (learned from a real Desktop run): the import list
 * uses ONLY names the shipped Aegis desktop surface already proves
 * exist — a named ESM import that the installed SDK does not export
 * kills the whole module at link time, before register() ever runs.
 * The status-bar chip therefore uses the plain area string
 * 'statusBar.right' (the SDK doc's own hello-world example uses this
 * string) instead of the STATUSBAR_AREAS constant. register() also
 * registers the page/nav/palette FIRST and wraps the floating widget
 * and polling in try/catch, so one failing surface can never take the
 * others down with it.
 */
import { atom, host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, SandboxedFrame, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

// Plain area string (see header note): do NOT import STATUSBAR_AREAS.
const STATUS_BAR_RIGHT_AREA = 'statusBar.right'

const PLUGIN_ID = 'hermes-focus-widget'
const PLUGIN_NAME = 'hermes-focus-widget'
const DEFAULT_PORT = 18778
const POLL_MS = 2000
const FLOAT_STORAGE_KEY = 'float.position'
const FLOAT_VISIBLE_KEY = 'float.visible'
// Cloud-light tokens (single scale; v1 has no dark theme).
const ACCENT = '#0F766E'
const ACCENT_DARK = '#0B5E57'
const INK = '#1F2937'
const MUTED = '#6B7280'
const BORDER = '#E5E7EB'
const SURFACE = '#FFFFFF'
const RING_C_FLOAT = 326.73 // 2 * PI * 52

const widgetUrlAtom = atom(null)
const chipDataAtom = atom(null) // { timer, stats } from the shared poll
const chipOpenAtom = atom(false)
let portPromise = null
let floatRoot = null
let floatCtx = null
let pollTimer = null
let pollCtx = null
let lastPhase = null
let lastNotifiedPhase = null
let chipOutsideListener = null
let chipPopoverNode = null
let lastPayload = null // latest /api/state payload, for the chip popover

// Lucide-style icons (stroke 2, 24px grid, currentColor). No emoji.
const ICON_PLAY = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>'
const ICON_PAUSE = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/></svg>'
const ICON_SKIP = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/></svg>'
const ICON_RESET = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>'
const ICON_CHEVRON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
const ICON_X = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'

function widgetUrlFor(port) {
  return `http://127.0.0.1:${port}/`
}

function stateUrlFor(port) {
  return `http://127.0.0.1:${port}/api/state`
}

function controlUrlFor(port) {
  return `http://127.0.0.1:${port}/api/control`
}

async function ensureWidgetPort() {
  if (portPromise) return portPromise
  portPromise = (async () => {
    try {
      const result = await host.request('plugins.manage', { action: 'list' })
      const plugins = (result && result.plugins) || []
      const row = plugins.find((p) => p && (p.key === PLUGIN_NAME || p.name === PLUGIN_NAME))
      const fields = (row && row.settings_schema) || []
      const field = fields.find((f) => f && f.key === 'hud_port')
      const raw = field && (field.value !== undefined && field.value !== null ? field.value : field.default)
      const port = Number(raw)
      if (Number.isInteger(port) && port > 0 && port < 65536) {
        widgetUrlAtom.set(widgetUrlFor(port))
        return port
      }
    } catch (e) {
      // Gateway unreachable, or an older host without plugins.manage.
    }
    widgetUrlAtom.set(widgetUrlFor(DEFAULT_PORT))
    return DEFAULT_PORT
  })()
  return portPromise
}

function fmt(s) {
  s = Math.max(0, Math.round(s || 0))
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
}

function phaseLabel(p) {
  return { idle: 'Idle', focus: 'Focus', short_break: 'Short break', long_break: 'Long break' }[p] || p
}

function notifyPhaseChange(phase) {
  if (phase === lastNotifiedPhase) return
  lastNotifiedPhase = phase
  if (phase === 'short_break' || phase === 'long_break') {
    host.notify({ kind: 'info', title: 'Focus session done', message: 'Nice work — time for a break.' })
    try {
      pollCtx && pollCtx.os && pollCtx.os.notify({ title: 'Focus session done', body: 'Time for a break.' })
    } catch (e) { /* OS notify is best-effort */ }
  } else if (phase === 'focus') {
    host.notify({ kind: 'info', title: 'Break over', message: 'Back to focus.' })
  }
}

async function postControl(port, action) {
  try {
    await fetch(controlUrlFor(port), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    })
  } catch (e) { host.navigate('/focus-widget') }
}

// One shared poll feeds the chip, the floating widget and the
// phase-change notifications. It runs while the plugin is loaded,
// whether or not the floating widget is visible.
async function pollOnce() {
  const port = await ensureWidgetPort()
  let payload = null
  try {
    const res = await fetch(stateUrlFor(port))
    payload = await res.json()
  } catch (e) {
    payload = null
  }
  const st = (payload && payload.timer) || null
  chipDataAtom.set(payload && st ? { timer: st, stats: payload.stats || {} } : null)
  lastPayload = payload
  if (floatRoot) floatRoot._payload = payload
  if (chipPopoverNode) updateChipPopover()
  if (st && st.phase && st.phase !== lastPhase) {
    if (lastPhase !== null) notifyPhaseChange(st.phase)
    lastPhase = st.phase
  }
  updateFloat(st, port)
}

function updateFloat(st, port) {
  if (!floatRoot) return
  const timeEl = floatRoot.querySelector('#hfw-time')
  const phaseEl = floatRoot.querySelector('#hfw-phase')
  const ringEl = floatRoot.querySelector('#hfw-ring')
  const startBtn = floatRoot.querySelector('#hfw-start')
  const skipBtn = floatRoot.querySelector('#hfw-skip')
  const resetBtn = floatRoot.querySelector('#hfw-reset')
  if (!timeEl || !phaseEl || !ringEl || !startBtn) return
  if (!st) {
    timeEl.textContent = '--:--'
    phaseEl.textContent = 'Idle'
    phaseEl.style.color = MUTED
    ringEl.style.stroke = BORDER
    ringEl.setAttribute('stroke-dashoffset', String(RING_C_FLOAT))
    startBtn.innerHTML = ICON_PLAY
    startBtn.setAttribute('aria-label', 'Start focus')
    return
  }
  const running = !!st.running
  const isBreak = st.phase === 'short_break' || st.phase === 'long_break'
  const label = st.phase === 'focus' ? (running ? 'Focus' : 'Paused') : isBreak ? 'Break' : 'Idle'
  const accent = running && (st.phase === 'focus' || isBreak)
  timeEl.textContent = fmt(st.remaining_s)
  phaseEl.textContent = label
  phaseEl.style.color = accent ? ACCENT : MUTED
  const frac = st.duration_s ? Math.min(1, Math.max(0, st.elapsed_s / st.duration_s)) : 0
  ringEl.setAttribute('stroke-dashoffset', (RING_C_FLOAT * (1 - frac)).toFixed(1))
  ringEl.style.stroke = accent ? ACCENT : (st.phase === 'idle' ? BORDER : '#9CA3AF')
  startBtn.innerHTML = running ? ICON_PAUSE : ICON_PLAY
  startBtn.setAttribute('aria-label', running ? 'Pause timer' : (st.phase === 'focus' ? 'Resume timer' : 'Start focus'))
  startBtn.onclick = () => postControl(port, running ? 'pause' : 'start')
  if (skipBtn) {
    skipBtn.disabled = st.phase === 'idle'
    skipBtn.onclick = () => postControl(port, 'skip')
  }
  if (resetBtn) {
    resetBtn.disabled = st.phase === 'idle' && !running
    resetBtn.onclick = () => postControl(port, 'reset')
  }
  const det = floatRoot.querySelector('#hfw-details-body')
  if (det && !det.hidden) updateFloatDetails()
}

// Stats for the float's details view come from the latest poll payload,
// kept on the root by pollOnce (no second fetch).
function updateFloatDetails() {
  if (!floatRoot) return
  const today = floatRoot.querySelector('#hfw-stats-today')
  const week = floatRoot.querySelector('#hfw-stats-week')
  const streak = floatRoot.querySelector('#hfw-stats-streak')
  const empty = floatRoot.querySelector('#hfw-empty')
  if (!today || !week || !streak) return
  const p = floatRoot._payload
  const s = (p && p.stats) || {}
  today.textContent = (s.today_sessions || 0) + ' sessions · ' + (s.today_minutes || 0) + ' min'
  week.textContent = (s.week_sessions || 0) + ' sessions · ' + (s.week_minutes || 0) + ' min'
  streak.textContent = (s.streak_days || 0) + ' days'
  if (empty) empty.hidden = (s.total_sessions || 0) > 0
}

function startPolling(ctx) {
  if (pollTimer) return
  pollCtx = ctx
  void pollOnce()
  // Scoped interval when the host provides one; a plain window timer
  // as fallback on older hosts (cleaned up in stopPolling/onDispose).
  if (ctx.setInterval) {
    pollTimer = ctx.setInterval(pollOnce, POLL_MS)
  } else {
    pollTimer = setInterval(pollOnce, POLL_MS)
  }
}

function stopPolling() {
  if (pollTimer && pollCtx) {
    try {
      if (pollCtx.clearInterval) pollCtx.clearInterval(pollTimer)
      else clearInterval(pollTimer)
    } catch (e) {}
    pollTimer = null
    pollCtx = null
  }
}

function storageGet(ctx, key, fallback) {
  try {
    if (ctx.storage && ctx.storage.get) return ctx.storage.get(key, fallback)
  } catch (e) { /* older host without storage: fall through */ }
  return fallback
}

function storageSet(ctx, key, value) {
  try {
    if (ctx.storage && ctx.storage.set) ctx.storage.set(key, value)
  } catch (e) { /* best effort */ }
}

function ensureFloatStyle() {
  if (typeof document === 'undefined' || document.getElementById('hfw-style')) return
  const style = document.createElement('style')
  style.id = 'hfw-style'
  style.textContent =
    '#hermes-focus-widget-float{font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;user-select:none;}' +
    '#hermes-focus-widget-float .hfw-head{display:flex;justify-content:space-between;align-items:center;padding:12px 12px 0;cursor:grab;}' +
    '#hermes-focus-widget-float .hfw-phase{font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:' + MUTED + ';}' +
    '#hermes-focus-widget-float .hfw-close{border:0;background:none;color:' + MUTED + ';cursor:pointer;padding:4px;display:inline-flex;border-radius:8px;}' +
    '#hermes-focus-widget-float .hfw-close:hover{color:' + INK + ';background:#F5F6F7;}' +
    '#hermes-focus-widget-float .hfw-ringwrap{position:relative;width:120px;height:120px;margin:12px auto 0;}' +
    '#hermes-focus-widget-float .hfw-ring{transform:rotate(-90deg);display:block;}' +
    '#hermes-focus-widget-float .hfw-ring circle{fill:none;stroke-width:2;}' +
    '#hermes-focus-widget-float .hfw-time{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;font-variant-numeric:tabular-nums;color:' + INK + ';}' +
    '#hermes-focus-widget-float .hfw-btns{display:flex;gap:8px;justify-content:center;margin-top:12px;}' +
    '#hermes-focus-widget-float .hfw-btn{width:44px;height:44px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;border:1px solid transparent;cursor:pointer;padding:0;}' +
    '#hermes-focus-widget-float .hfw-btn svg{width:20px;height:20px;}' +
    '#hermes-focus-widget-float .hfw-btn.primary{background:' + ACCENT + ';color:#FFFFFF;}' +
    '#hermes-focus-widget-float .hfw-btn.primary:hover{background:' + ACCENT_DARK + ';}' +
    '#hermes-focus-widget-float .hfw-btn.ghost{background:' + SURFACE + ';border-color:' + BORDER + ';color:' + INK + ';}' +
    '#hermes-focus-widget-float .hfw-btn.ghost:hover{background:#F5F6F7;}' +
    '#hermes-focus-widget-float .hfw-btn:focus-visible{outline:2px solid ' + ACCENT + ';outline-offset:2px;}' +
    '#hermes-focus-widget-float .hfw-btn:disabled{opacity:.45;cursor:default;}' +
    '#hermes-focus-widget-float .hfw-details{width:100%;min-height:44px;display:flex;justify-content:space-between;align-items:center;background:none;border:0;border-top:1px solid ' + BORDER + ';margin-top:12px;padding:12px 12px 0;font-size:14px;font-weight:600;color:' + INK + ';cursor:pointer;text-align:left;}' +
    '#hermes-focus-widget-float .hfw-details .hfw-chev{transition:transform .2s ease;color:' + MUTED + ';}' +
    '#hermes-focus-widget-float .hfw-details[aria-expanded="true"] .hfw-chev{transform:rotate(180deg);}' +
    '#hermes-focus-widget-float .hfw-srow{display:flex;justify-content:space-between;gap:12px;padding:8px 12px;border-bottom:1px solid ' + BORDER + ';font-size:13px;}' +
    '#hermes-focus-widget-float .hfw-srow span:first-child{color:' + MUTED + ';}' +
    '#hermes-focus-widget-float .hfw-srow span:last-child{font-weight:600;font-variant-numeric:tabular-nums;}' +
    '#hermes-focus-widget-float .hfw-open{display:block;text-align:center;font-size:13px;font-weight:600;color:' + ACCENT + ';text-decoration:none;padding:12px;cursor:pointer;}' +
    '#hermes-focus-widget-float .hfw-empty{color:' + MUTED + ';font-size:13px;padding:8px 12px;}'
  document.head.appendChild(style)
}

// --- floating widget -------------------------------------------------
function mountFloatingWidget(ctx) {
  if (floatRoot || typeof document === 'undefined') return
  floatCtx = ctx
  ensureFloatStyle()
  const root = document.createElement('div')
  root.id = 'hermes-focus-widget-float'
  root.style.cssText = 'position:fixed;z-index:9999;left:0;top:0;width:212px;background:' + SURFACE + ';color:' + INK + ';border:1px solid ' + BORDER + ';border-radius:12px;box-shadow:0 4px 16px rgba(31,41,55,.08);'
  root.innerHTML =
    '<div class="hfw-head" id="hfw-drag"><span class="hfw-phase" id="hfw-phase">Idle</span><button class="hfw-close" id="hfw-close" aria-label="Hide floating widget">' + ICON_X + '</button></div>' +
    '<div class="hfw-ringwrap"><svg class="hfw-ring" viewBox="0 0 120 120" width="120" height="120" aria-hidden="true">' +
    '<circle cx="60" cy="60" r="52" stroke="' + BORDER + '"/>' +
    '<circle id="hfw-ring" cx="60" cy="60" r="52" stroke="' + BORDER + '" stroke-linecap="round" stroke-dasharray="' + RING_C_FLOAT + '" stroke-dashoffset="' + RING_C_FLOAT + '"/>' +
    '</svg><div class="hfw-time" id="hfw-time">25:00</div></div>' +
    '<div class="hfw-btns">' +
    '<button class="hfw-btn primary" id="hfw-start" aria-label="Start focus">' + ICON_PLAY + '</button>' +
    '<button class="hfw-btn ghost" id="hfw-skip" aria-label="Skip phase">' + ICON_SKIP + '</button>' +
    '<button class="hfw-btn ghost" id="hfw-reset" aria-label="Reset timer">' + ICON_RESET + '</button>' +
    '</div>' +
    '<button class="hfw-details" id="hfw-details" aria-expanded="false"><span>Session stats</span><span class="hfw-chev">' + ICON_CHEVRON + '</span></button>' +
    '<div id="hfw-details-body" hidden>' +
    '<div class="hfw-srow"><span>Today</span><span id="hfw-stats-today">0 sessions · 0 min</span></div>' +
    '<div class="hfw-srow"><span>This week</span><span id="hfw-stats-week">0 sessions · 0 min</span></div>' +
    '<div class="hfw-srow"><span>Day streak</span><span id="hfw-stats-streak">0 days</span></div>' +
    '<p class="hfw-empty" id="hfw-empty" hidden>No focus sessions yet today — start your first one.</p>' +
    '<a class="hfw-open" id="hfw-open">Open full page</a>' +
    '</div>'
  document.body.appendChild(root)
  floatRoot = root
  root._payload = null

  // Position in VIEWPORT coordinates. The host lays the float out
  // with an offset the DOM rect does not predict (measured live:
  // the rendered card lands ~(+138, +80) from its rect, a transformed
  // ancestor the rect does not account for). So place, MEASURE where
  // it actually landed, and correct by the residual — up to 3 rounds.
  // This converges for a constant offset and for a scale factor alike.
  function placeFloat(vx, vy) {
    const h = root.offsetHeight || 260
    vx = Math.max(8, Math.min(vx, window.innerWidth - 212 - 8))
    vy = Math.max(8, Math.min(vy, window.innerHeight - h - 8))
    root.style.left = vx + 'px'
    root.style.top = vy + 'px'
    for (let i = 0; i < 3; i++) {
      const r = root.getBoundingClientRect()
      const dx = vx - r.left
      const dy = vy - r.top
      if (Math.abs(dx) < 2 && Math.abs(dy) < 2) break
      root.style.left = ((parseFloat(root.style.left) || 0) + dx) + 'px'
      root.style.top = ((parseFloat(root.style.top) || 0) + dy) + 'px'
    }
  }
  const saved = storageGet(ctx, FLOAT_STORAGE_KEY, null)
  if (saved && typeof saved.x === 'number' && typeof saved.y === 'number') {
    placeFloat(saved.x, saved.y)
  } else {
    placeFloat(window.innerWidth - 212 - 16, window.innerHeight - (root.offsetHeight || 260) - 16)
  }
  function onResize() {
    const rect = root.getBoundingClientRect()
    placeFloat(rect.left, rect.top)
  }
  window.addEventListener('resize', onResize)
  root._onResize = onResize
  const drag = root.querySelector('#hfw-drag')
  let dragState = null
  drag.addEventListener('pointerdown', (e) => {
    if (e.target && e.target.closest && e.target.closest('#hfw-close')) return
    const rect = root.getBoundingClientRect()
    dragState = { x: e.clientX, y: e.clientY, vx: rect.left, vy: rect.top }
    drag.style.cursor = 'grabbing'
    try { drag.setPointerCapture(e.pointerId) } catch (err) {}
  })
  drag.addEventListener('pointermove', (e) => {
    if (!dragState) return
    placeFloat(dragState.vx + e.clientX - dragState.x, dragState.vy + e.clientY - dragState.y)
  })
  drag.addEventListener('pointerup', () => {
    drag.style.cursor = 'grab'
    if (dragState) {
      const rect = root.getBoundingClientRect()
      storageSet(ctx, FLOAT_STORAGE_KEY, { x: Math.round(rect.left), y: Math.round(rect.top) })
      dragState = null
    }
  })
  root.querySelector('#hfw-close').addEventListener('click', (e) => {
    e.stopPropagation()
    storageSet(ctx, FLOAT_VISIBLE_KEY, false)
    unmountFloatingWidget()
  })
  root.querySelector('#hfw-open').addEventListener('click', () => host.navigate('/focus-widget'))
  root.querySelector('#hfw-details').addEventListener('click', () => {
    const body = root.querySelector('#hfw-details-body')
    const open = body.hidden
    body.hidden = !open
    root.querySelector('#hfw-details').setAttribute('aria-expanded', String(open))
    if (open) updateFloatDetails()
  })
  // The start/pause button's action is wired by updateFloat on every
  // poll (it flips between start and pause); no second listener here.
  void pollOnce()
}

function unmountFloatingWidget() {
  if (floatRoot) {
    if (floatRoot._onResize) {
      try { window.removeEventListener('resize', floatRoot._onResize) } catch (e) {}
    }
    if (floatRoot.parentNode) floatRoot.parentNode.removeChild(floatRoot)
  }
  floatRoot = null
  floatCtx = null
}

// --- chip popover (body portal) -------------------------------------
// The popover used to render inside the chip's React tree, positioned
// absolute above the chip — but the status-bar host clips anything
// outside its own box (overflow:hidden), so only a sliver of the card
// painted (verified live: full 232x254 rect in the DOM, invisible on
// screen; a nudge loop could not help because the clip, not the
// position, was the problem). The fix: render the popover as a plain
// DOM node appended to document.body with position:fixed, outside the
// clipping ancestor entirely. Placement reuses the float's
// measure-and-correct loop: place by the chip's rect, measure where it
// actually landed, correct the residual (up to 3 rounds), then clamp
// inside the viewport.
function mountChipPopover() {
  if (typeof document === 'undefined' || chipPopoverNode) return
  const btn = document.querySelector('[data-focus-chip] button')
  if (!btn) return
  const node = document.createElement('div')
  node.setAttribute('data-focus-popover', '1')
  node.style.cssText = 'position:fixed;z-index:10000;width:232px;background:' + SURFACE + ';color:' + INK + ';border:1px solid ' + BORDER + ';border-radius:12px;box-shadow:0 4px 16px rgba(31,41,55,.08);padding:12px;font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;text-align:left;'
  node.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;"><span id="hfw-pop-phase" style="font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:' + MUTED + ';">Idle</span><button id="hfw-pop-close" type="button" aria-label="Close" style="border:0;background:none;color:' + MUTED + ';cursor:pointer;padding:4px;display:inline-flex;">' + ICON_X + '</button></div>' +
    '<div id="hfw-pop-time" style="font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;margin-bottom:8px;">--:--</div>' +
    '<div style="display:flex;justify-content:space-between;padding:6px 0;border-top:1px solid ' + BORDER + ';font-size:13px;"><span style="color:' + MUTED + ';">Today</span><span id="hfw-pop-today" style="font-weight:600;">0 sessions · 0 min</span></div>' +
    '<div style="display:flex;justify-content:space-between;padding:6px 0;border-top:1px solid ' + BORDER + ';font-size:13px;"><span style="color:' + MUTED + ';">This week</span><span id="hfw-pop-week" style="font-weight:600;">0 sessions · 0 min</span></div>' +
    '<div style="display:flex;justify-content:space-between;padding:6px 0;border-top:1px solid ' + BORDER + ';border-bottom:1px solid ' + BORDER + ';font-size:13px;"><span style="color:' + MUTED + ';">Day streak</span><span id="hfw-pop-streak" style="font-weight:600;">0 days</span></div>' +
    '<button id="hfw-pop-open" type="button" style="width:100%;min-height:44px;margin-top:8px;background:' + ACCENT + ';color:#FFFFFF;border:0;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;">Open full page</button>'
  document.body.appendChild(node)
  chipPopoverNode = node
  node.querySelector('#hfw-pop-close').addEventListener('click', () => setChipOpen(false))
  node.querySelector('#hfw-pop-open').addEventListener('click', () => { setChipOpen(false); host.navigate('/focus-widget') })
  updateChipPopover()
  placeChipPopover()
  if (!chipOutsideListener) {
    chipOutsideListener = (e) => {
      if (e.target && e.target.closest && !e.target.closest('[data-focus-chip]') && !e.target.closest('[data-focus-popover]')) {
        setChipOpen(false)
      }
    }
    document.addEventListener('click', chipOutsideListener)
  }
}

function placeChipPopover() {
  if (!chipPopoverNode) return
  const btn = document.querySelector('[data-focus-chip] button')
  if (!btn) return
  const br = btn.getBoundingClientRect()
  const w = 232
  const h = chipPopoverNode.offsetHeight || 254
  // Target: right edge meets the chip's right edge; bottom 8px above
  // the chip's top. Fall back below the chip when there is no room above.
  let tx = br.right - w
  let ty = br.top - 8 - h
  if (ty < 8) ty = br.bottom + 8
  tx = Math.max(8, Math.min(tx, window.innerWidth - w - 8))
  ty = Math.max(8, Math.min(ty, window.innerHeight - h - 8))
  chipPopoverNode.style.left = tx + 'px'
  chipPopoverNode.style.top = ty + 'px'
  for (let i = 0; i < 3; i++) {
    const r = chipPopoverNode.getBoundingClientRect()
    const dx = tx - r.left
    const dy = ty - r.top
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2) break
    chipPopoverNode.style.left = ((parseFloat(chipPopoverNode.style.left) || 0) + dx) + 'px'
    chipPopoverNode.style.top = ((parseFloat(chipPopoverNode.style.top) || 0) + dy) + 'px'
  }
}

function updateChipPopover() {
  if (!chipPopoverNode) return
  const node = chipPopoverNode
  const p = lastPayload
  const st = (p && p.timer) || null
  const stats = (p && p.stats) || {}
  const running = !!(st && st.running)
  const phaseEl = node.querySelector('#hfw-pop-phase')
  const timeEl = node.querySelector('#hfw-pop-time')
  if (phaseEl) {
    phaseEl.textContent = st ? (st.phase === 'focus' ? (running ? 'Focus' : 'Paused') : st.phase === 'short_break' || st.phase === 'long_break' ? 'Break' : 'Idle') : 'Idle'
    phaseEl.style.color = running ? ACCENT : MUTED
  }
  if (timeEl) timeEl.textContent = st ? fmt(st.remaining_s) : '--:--'
  const set = (id, text) => { const el = node.querySelector(id); if (el) el.textContent = text }
  set('#hfw-pop-today', (stats.today_sessions || 0) + ' sessions · ' + (stats.today_minutes || 0) + ' min')
  set('#hfw-pop-week', (stats.week_sessions || 0) + ' sessions · ' + (stats.week_minutes || 0) + ' min')
  set('#hfw-pop-streak', (stats.streak_days || 0) + ' days')
}

function unmountChipPopover() {
  if (chipOutsideListener) {
    try { document.removeEventListener('click', chipOutsideListener) } catch (e) {}
    chipOutsideListener = null
  }
  if (chipPopoverNode) {
    if (chipPopoverNode.parentNode) chipPopoverNode.parentNode.removeChild(chipPopoverNode)
    chipPopoverNode = null
  }
}

function setChipOpen(next) {
  chipOpenAtom.set(next)
  if (next) {
    requestAnimationFrame(() => mountChipPopover())
  } else {
    unmountChipPopover()
  }
}

// --- status-bar chip -------------------------------------------------
function FocusChip() {
  const data = useValue(chipDataAtom)
  const open = useValue(chipOpenAtom)
  const st = data && data.timer
  const running = !!(st && st.running)
  const label = st ? fmt(st.remaining_s) : '--:--'
  const dotColor = running ? ACCENT : '#9CA3AF'
  const title = st
    ? (st.phase === 'focus' ? 'Focus ' + st.focus_completed + '/' + st.sessions_before_long_break : phaseLabel(st.phase)) + ' · ' + label + ' left — click for stats'
    : 'Focus Widget — server not running'
  function toggle() {
    setChipOpen(!open)
  }
  // The popover is NOT rendered here: it is a body portal mounted by
  // setChipOpen (see above) — rendering it inside this tree puts it
  // under the status-bar host's overflow:hidden clip.
  return jsx('div', {
    'data-focus-chip': '1',
    style: { position: 'relative', display: 'inline-flex', alignItems: 'center' },
    children: [
      jsx('button', {
        key: 'chip',
        type: 'button',
        className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        title: title,
        onClick: toggle,
        children: [
          jsx('span', { key: 'd', style: { display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: dotColor, marginRight: 6, verticalAlign: '1px' } }),
          jsx('span', { key: 't', style: { fontVariantNumeric: 'tabular-nums', color: INK }, children: label })
        ]
      })
    ]
  })
}

// --- full page (SandboxedFrame, Aegis pattern) -----------------------
function FocusWidgetPage(props) {
  const widgetUrl = useValue(widgetUrlAtom)
  const path = (props && props.path) || ''
  if (!widgetUrl) {
    void ensureWidgetPort()
    return jsx('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%', color: MUTED },
      children: 'Loading Focus Widget…'
    })
  }
  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', width: '100%', height: '100%' },
    children: jsx(SandboxedFrame, {
      src: widgetUrl + path,
      title: 'Focus Widget — Pomodoro',
      style: { flex: '1 1 auto', width: '100%', height: '100%', border: '0' }
    })
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Focus Widget',
  // Explicit: a standalone desktop-plugins/ install loads enabled.
  // (A packaged copy ships opt-in; this file is also shipped as the
  // standalone desktop surface.)
  defaultEnabled: true,
  description: 'Pomodoro widget that only counts focus time while the agent is working.',
  register(ctx) {
    void ensureWidgetPort()
    // Register the page/nav/palette/chip FIRST: the surfaces below
    // (polling, floating widget) are best-effort and must never be
    // able to take the core surfaces down with them.
    ctx.registerMany([
      {
        id: 'chip',
        area: STATUS_BAR_RIGHT_AREA,
        order: 120,
        render: () => jsx(FocusChip, {})
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/focus-widget' },
        render: () => jsx(FocusWidgetPage, {})
      },
      {
        id: 'settings-page',
        area: ROUTES_AREA,
        data: { path: '/focus-widget/settings' },
        render: () => jsx(FocusWidgetPage, { path: 'settings' })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 61,
        data: { codicon: 'symbol-odometer', label: 'Focus Widget', path: '/focus-widget' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-focus-widget.open',
          label: 'Open Focus Widget',
          keywords: ['focus', 'pomodoro', 'timer', 'widget', 'break'],
          run: () => host.navigate('/focus-widget')
        }
      },
      {
        id: 'settings',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-focus-widget.settings',
          label: 'Focus Widget: Settings',
          keywords: ['focus', 'pomodoro', 'settings', 'timer'],
          run: () => host.navigate('/focus-widget/settings')
        }
      },
      {
        id: 'toggle-float',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-focus-widget.toggle-float',
          label: 'Focus Widget: Toggle floating widget',
          keywords: ['focus', 'pomodoro', 'floating', 'widget'],
          run: () => {
            if (floatRoot) {
              storageSet(ctx, FLOAT_VISIBLE_KEY, false)
              unmountFloatingWidget()
            } else {
              storageSet(ctx, FLOAT_VISIBLE_KEY, true)
              mountFloatingWidget(ctx)
            }
          }
        }
      }
    ])
    if (ctx.registerSettingsPage) {
      try {
        ctx.registerSettingsPage({
          id: 'focus-widget-settings',
          title: 'Focus Widget',
          render: () => jsx(FocusWidgetPage, { path: 'settings' })
        })
      } catch (e) { /* settings page is optional */ }
    }
    // Best-effort surfaces: any failure here must not undo the
    // registrations above.
    try { startPolling(ctx) } catch (e) { /* chip/float stay static */ }
    try {
      if (storageGet(ctx, FLOAT_VISIBLE_KEY, true) !== false) {
        mountFloatingWidget(ctx)
      }
    } catch (e) { /* floating widget stays hidden */ }
    ctx.onDispose(() => { unmountFloatingWidget(); unmountChipPopover(); stopPolling() })
  }
}

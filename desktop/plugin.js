/**
 * Hermes Focus Widget — Desktop surface.
 *
 * Three surfaces, all reading the same loopback state server
 * (http://127.0.0.1:<port>/api/state, served by the agent-side plugin):
 *
 * 1. A status-bar chip (bottom-right bar, `STATUSBAR_AREAS.right`):
 *    phase emoji + time left, e.g. "🍅 12:34" while focusing,
 *    "☕ 04:59" on a break. Clicking it opens the full page.
 *    The chip is the glanceable surface; it polls the same endpoint
 *    as the other surfaces (one shared 2s poll).
 *
 * 2. A small floating widget pinned to the Desktop window
 *    (bottom-right, draggable, position saved in plugin-scoped
 *    storage). It polls the state endpoint directly with a plain
 *    fetch; the server sends `Access-Control-Allow-Origin: *` and
 *    requires no login, so the poll works for local installs. If the
 *    server cannot be reached (for example a remote backend), the
 *    floating widget says so and offers to open the full page instead.
 *
 * 3. A "Focus Widget" page (sidebar nav + command palette),
 *    embedding the full widget UI in the SDK's SandboxedFrame — the
 *    same pattern as the Manas Aegis HUD page. The page is
 *    same-origin with the widget server, so its polling just works.
 *
 * Phase changes are announced with host.notify (in-app toast) and,
 * when the user is away, ctx.os.notify (native OS notification).
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

const widgetUrlAtom = atom(null)
const chipAtom = atom({ label: '🍅 --:--', title: 'Focus Widget — idle' })
let portPromise = null
let floatRoot = null
let floatCtx = null
let pollTimer = null
let pollCtx = null
let lastPhase = null
let lastNotifiedPhase = null

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

function chipFor(st) {
  if (!st || !st.phase) return { label: '🍅 --:--', title: 'Focus Widget — server not running' }
  if (st.phase === 'focus') {
    const extra = st.waiting_for_agent ? ' · waiting for agent' : st.running ? '' : ' · paused'
    return { label: '🍅 ' + fmt(st.remaining_s), title: 'Focus ' + st.focus_completed + '/' + st.sessions_before_long_break + extra + ' — click to open' }
  }
  if (st.phase === 'short_break' || st.phase === 'long_break') {
    return { label: '☕ ' + fmt(st.remaining_s), title: phaseLabel(st.phase) + ' — click to open' }
  }
  return { label: '🍅 --:--', title: 'Focus Widget — idle, click to start' }
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
  let st = null
  try {
    const res = await fetch(stateUrlFor(port))
    const data = await res.json()
    st = (data && data.timer) || null
  } catch (e) {
    st = null
  }
  chipAtom.set(chipFor(st))
  if (st && st.phase && st.phase !== lastPhase) {
    if (lastPhase !== null) notifyPhaseChange(st.phase)
    lastPhase = st.phase
  }
  if (floatRoot) {
    const timeEl = floatRoot.querySelector('#hfw-time')
    const statusEl = floatRoot.querySelector('#hfw-status')
    const startBtn = floatRoot.querySelector('#hfw-start')
    if (timeEl && statusEl && startBtn) {
      if (!st) {
        timeEl.textContent = '--:--'
        statusEl.textContent = 'Widget server not running'
      } else {
        timeEl.textContent = fmt(st.remaining_s)
        if (st.phase === 'focus' && st.running) {
          statusEl.textContent = st.agent_aware
            ? (st.waiting_for_agent ? 'Waiting for agent…' : 'Agent active · counting')
            : 'Counting'
          startBtn.textContent = 'Pause'
          startBtn.onclick = () => postControl(port, 'pause')
        } else {
          statusEl.textContent = phaseLabel(st.phase)
          startBtn.textContent = 'Start'
          startBtn.onclick = () => postControl(port, 'start')
        }
      }
    }
  }
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

// --- floating widget -------------------------------------------------
function mountFloatingWidget(ctx) {
  if (floatRoot || typeof document === 'undefined') return
  floatCtx = ctx
  const root = document.createElement('div')
  root.id = 'hermes-focus-widget-float'
  root.style.cssText = 'position:fixed;z-index:9999;right:16px;bottom:16px;width:212px;background:#171a21;color:#e6e6e6;border:1px solid #262b36;border-radius:14px;box-shadow:0 8px 28px rgba(0,0,0,.45);font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;user-select:none;'
  root.innerHTML =
    '<div id="hfw-drag" style="cursor:grab;padding:10px 12px 6px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#9aa0a6;display:flex;justify-content:space-between;align-items:center;"><span>Focus</span><span id="hfw-close" style="cursor:pointer;font-size:14px;line-height:1;">×</span></div>' +
    '<div id="hfw-time" style="text-align:center;font-size:34px;font-weight:700;font-variant-numeric:tabular-nums;">25:00</div>' +
    '<div id="hfw-status" style="text-align:center;font-size:12px;color:#9aa0a6;padding:2px 10px 10px;">Idle</div>' +
    '<div style="display:flex;gap:6px;padding:0 10px 12px;"><button id="hfw-start" style="flex:1;background:#f0b429;color:#141414;border:0;border-radius:8px;padding:7px 0;font-size:13px;font-weight:700;cursor:pointer;">Start</button><button id="hfw-open" style="flex:1;background:#262b36;color:#e6e6e6;border:0;border-radius:8px;padding:7px 0;font-size:13px;font-weight:600;cursor:pointer;">Open</button></div>'
  document.body.appendChild(root)
  floatRoot = root

  // Position with explicit left/top from the VIEWPORT, never right/bottom:
  // the host app has transformed ancestors, which make `position:fixed`
  // resolve `right` against a container wider than the viewport — the
  // card used to hang half off-screen. A saved position is clamped back
  // inside the current viewport, so a stale value from a wider window
  // can never strand the widget off-screen again.
  const FLOAT_W = 212
  function clampFloat(x, y) {
    const h = root.offsetHeight || 170
    return {
      x: Math.max(8, Math.min(x, window.innerWidth - FLOAT_W - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - h - 8))
    }
  }
  const saved = storageGet(ctx, FLOAT_STORAGE_KEY, null)
  const placed = (saved && typeof saved.x === 'number' && typeof saved.y === 'number')
    ? clampFloat(saved.x, saved.y)
    : clampFloat(window.innerWidth - FLOAT_W - 16, window.innerHeight - (root.offsetHeight || 170) - 16)
  root.style.left = placed.x + 'px'
  root.style.top = placed.y + 'px'
  root.style.right = 'auto'
  root.style.bottom = 'auto'
  const drag = root.querySelector('#hfw-drag')
  let dragState = null
  drag.addEventListener('pointerdown', (e) => {
    dragState = { x: e.clientX, y: e.clientY, left: root.offsetLeft, top: root.offsetTop }
    drag.style.cursor = 'grabbing'
    drag.setPointerCapture(e.pointerId)
  })
  drag.addEventListener('pointermove', (e) => {
    if (!dragState) return
    // Keep the whole card on-screen while dragging (same clamp as mount).
    const p = clampFloat(dragState.left + e.clientX - dragState.x, dragState.top + e.clientY - dragState.y)
    root.style.left = p.x + 'px'
    root.style.top = p.y + 'px'
    root.style.right = 'auto'
    root.style.bottom = 'auto'
  })
  drag.addEventListener('pointerup', () => {
    drag.style.cursor = 'grab'
    if (dragState) {
      storageSet(ctx, FLOAT_STORAGE_KEY, { x: root.offsetLeft, y: root.offsetTop })
      dragState = null
    }
  })
  root.querySelector('#hfw-close').addEventListener('click', () => {
    storageSet(ctx, FLOAT_VISIBLE_KEY, false)
    unmountFloatingWidget()
  })
  root.querySelector('#hfw-open').addEventListener('click', () => host.navigate('/focus-widget'))
  root.querySelector('#hfw-start').addEventListener('click', async () => {
    const port = await ensureWidgetPort()
    await postControl(port, 'start')
  })
  void pollOnce()
}

function unmountFloatingWidget() {
  if (floatRoot && floatRoot.parentNode) floatRoot.parentNode.removeChild(floatRoot)
  floatRoot = null
  floatCtx = null
}

// --- status-bar chip -------------------------------------------------
function FocusChip() {
  const chip = useValue(chipAtom)
  return jsx('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    title: chip.title,
    onClick: () => host.navigate('/focus-widget'),
    children: chip.label
  })
}

// --- full page (SandboxedFrame, Aegis pattern) -----------------------
function FocusWidgetPage() {
  const widgetUrl = useValue(widgetUrlAtom)
  if (!widgetUrl) {
    void ensureWidgetPort()
    return jsx('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%', color: '#8b949e' },
      children: 'Loading Focus Widget…'
    })
  }
  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', width: '100%', height: '100%' },
    children: jsx(SandboxedFrame, {
      src: widgetUrl,
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
          render: () => jsx(FocusWidgetPage, {})
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
    ctx.onDispose(() => { unmountFloatingWidget(); stopPolling() })
  }
}

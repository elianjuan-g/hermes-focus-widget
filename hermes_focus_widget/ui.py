"""Self-contained widget page for Hermes Focus Widget.

Served by the loopback server (http://127.0.0.1:<port>/). No external
resources, no CDN, no fonts fetched: everything inline so the page works
offline. The page polls /api/state once a second and posts to
/api/control and /api/settings. A short WebAudio beep marks phase
changes when sound is enabled.

Design (locked, MyLord 12 Nov 2026): minimal, modern, cloud-light only.
One focus element — the countdown number. One thin 2px progress ring
around it (no bar). One calm accent, muted teal #0F766E; state is told
by accent + phase label, never by extra colour. Stats live behind one
tap; settings live on a separate view (/settings). Every control is a
real action; every string is a real label, time or instruction.
"""

from __future__ import annotations

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Focus Widget — Hermes</title>
<style>
  /* Tokens — one scale each. Light cloud theme only (v1).
     bg #F5F6F7, surface #FFFFFF, text #1F2937, muted #6B7280,
     border #E5E7EB, single accent muted teal #0F766E.
     Radius 12 surfaces / 8 controls; spacing 4/8/12/16/24/32;
     type 12 label / 14 body / 56 number; icon stroke 2px;
     touch target 44px; contrast WCAG AA throughout. */
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #F5F6F7; color: #1F2937; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 20px 16px 32px; }
  .topbar { display: flex; justify-content: space-between; align-items: baseline; }
  h1 { font-size: 15px; font-weight: 700; letter-spacing: .01em; }
  .link { color: #0F766E; font-size: 14px; font-weight: 600; text-decoration: none; }
  .link:hover { text-decoration: underline; }
  .card { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; padding: 24px 16px; margin-top: 16px; }
  .phase { text-align: center; font-size: 12px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: #6B7280; }
  .ring-wrap { position: relative; width: 200px; height: 200px; margin: 16px auto 0; }
  .ring { transform: rotate(-90deg); display: block; }
  .ring circle { fill: none; stroke-width: 2; }
  .ring-track { stroke: #E5E7EB; }
  .ring-prog { stroke: #0F766E; stroke-linecap: round; transition: stroke-dashoffset 1s linear, stroke .2s ease; }
  .timer { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 56px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: .01em; color: #1F2937; }
  .caption { text-align: center; font-size: 13px; color: #6B7280; margin-top: 12px; min-height: 18px; }
  .btns { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
  .icon-btn { width: 44px; height: 44px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid transparent; cursor: pointer; padding: 0; }
  .icon-btn svg { width: 20px; height: 20px; }
  .icon-btn.primary { background: #0F766E; color: #FFFFFF; }
  .icon-btn.primary:hover { background: #0B5E57; }
  .icon-btn.primary:active { background: #095048; }
  .icon-btn.ghost { background: #FFFFFF; border-color: #E5E7EB; color: #1F2937; }
  .icon-btn.ghost:hover { background: #F5F6F7; }
  .icon-btn.ghost:active { background: #ECEEF0; }
  .icon-btn:focus-visible { outline: 2px solid #0F766E; outline-offset: 2px; }
  .icon-btn:disabled { opacity: .45; cursor: default; }
  .icon-btn:disabled:hover { background: #0F766E; }
  .icon-btn.ghost:disabled:hover { background: #FFFFFF; }
  .err { color: #B91C1C; font-size: 13px; text-align: center; margin-top: 12px; min-height: 16px; }
  .details-toggle { width: 100%; min-height: 44px; display: flex; justify-content: space-between; align-items: center; background: none; border: 0; border-top: 1px solid #E5E7EB; margin-top: 16px; padding: 12px 2px 0; font-size: 14px; font-weight: 600; color: #1F2937; cursor: pointer; text-align: left; }
  .details-toggle .chev { transition: transform .2s ease; color: #6B7280; }
  .details-toggle[aria-expanded="true"] .chev { transform: rotate(180deg); }
  .details-toggle:focus-visible { outline: 2px solid #0F766E; outline-offset: 2px; border-radius: 8px; }
  .srow { display: flex; justify-content: space-between; gap: 12px; padding: 8px 2px; border-bottom: 1px solid #E5E7EB; font-size: 14px; }
  .srow span:first-child { color: #6B7280; }
  .srow span:last-child { font-weight: 600; font-variant-numeric: tabular-nums; }
  .sect { font-size: 12px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: #6B7280; margin: 16px 0 4px; }
  .log { list-style: none; font-size: 13px; }
  .log li { padding: 8px 2px; border-bottom: 1px solid #E5E7EB; display: flex; justify-content: space-between; gap: 8px; }
  .log li:last-child { border-bottom: 0; }
  .log .muted { color: #6B7280; }
  .empty { color: #6B7280; font-size: 13px; padding: 8px 2px; }
  .settings label { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 44px; padding: 8px 0; border-bottom: 1px solid #E5E7EB; font-size: 14px; }
  .settings label:last-of-type { border-bottom: 0; }
  .settings input[type="number"] { width: 72px; background: #FFFFFF; color: #1F2937; border: 1px solid #E5E7EB; border-radius: 8px; padding: 8px; font-size: 14px; text-align: right; }
  .settings input[type="number"]:focus-visible { outline: 2px solid #0F766E; outline-offset: 1px; }
  .settings input[type="checkbox"] { width: 20px; height: 20px; accent-color: #0F766E; }
  .hint { font-size: 13px; color: #6B7280; line-height: 1.5; margin-top: 12px; }
  .text-btn { background: #0F766E; color: #FFFFFF; border: 0; border-radius: 8px; padding: 0 18px; min-height: 44px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .text-btn:hover { background: #0B5E57; }
  .text-btn.ghost { background: #FFFFFF; color: #1F2937; border: 1px solid #E5E7EB; }
  .text-btn.ghost:hover { background: #F5F6F7; }
  .text-btn:focus-visible { outline: 2px solid #0F766E; outline-offset: 2px; }
  .form-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
</style>
</head>
<body>
<div class="wrap">
  <!-- ============ TIMER VIEW ============ -->
  <div id="view-timer">
    <header class="topbar">
      <h1>Focus Widget</h1>
      <a class="link" href="/settings">Settings</a>
    </header>

    <main class="card">
      <div class="phase" id="phase">Idle</div>
      <div class="ring-wrap">
        <svg class="ring" viewBox="0 0 200 200" width="200" height="200" aria-hidden="true">
          <circle class="ring-track" cx="100" cy="100" r="90" />
          <circle class="ring-prog" id="ring" cx="100" cy="100" r="90" stroke-dasharray="565.49" stroke-dashoffset="565.49" />
        </svg>
        <div class="timer" id="timer">--:--</div>
      </div>
      <div class="caption" id="statusline">Connecting…</div>
      <div class="btns">
        <button id="btnStart" class="icon-btn primary" aria-label="Start focus" disabled></button>
        <button id="btnSkip" class="icon-btn ghost" aria-label="Skip phase" disabled></button>
        <button id="btnReset" class="icon-btn ghost" aria-label="Reset timer" disabled></button>
      </div>
      <div class="err" id="err" role="alert"></div>
    </main>

    <button class="details-toggle" id="btnDetails" aria-expanded="false">
      <span>Session stats</span>
      <svg class="chev" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
    </button>
    <div id="details-body" hidden>
      <div class="srow"><span>Today</span><span id="stToday">0 sessions · 0 min</span></div>
      <div class="srow"><span>This week</span><span id="stWeek">0 sessions · 0 min</span></div>
      <div class="srow"><span>Day streak</span><span id="stStreak">0 days</span></div>
      <div class="sect">Recent sessions</div>
      <ul class="log" id="log"></ul>
      <p class="empty" id="log-empty" hidden>No focus sessions yet today — start your first one.</p>
    </div>
  </div>

  <!-- ============ SETTINGS VIEW ============ -->
  <div id="view-settings" hidden>
    <header class="topbar">
      <a class="link" href="/">← Timer</a>
      <h1>Settings</h1>
    </header>

    <div class="card settings" style="margin-top:16px">
      <label>Focus (minutes) <input type="number" id="setFocus" min="1" max="180"></label>
      <label>Short break (minutes) <input type="number" id="setShort" min="1" max="60"></label>
      <label>Long break (minutes) <input type="number" id="setLong" min="1" max="60"></label>
      <label>Sessions before long break <input type="number" id="setCycle" min="2" max="8"></label>
      <label>Agent-aware timer <input type="checkbox" id="setAware"></label>
      <label>Idle threshold (seconds) <input type="number" id="setIdle" min="10" max="1800"></label>
      <label>Auto-start breaks <input type="checkbox" id="setAutoBreak"></label>
      <label>Auto-start next focus <input type="checkbox" id="setAutoFocus"></label>
      <label>Sound on phase change <input type="checkbox" id="setSound"></label>
      <p class="hint">Agent-aware: the focus timer counts only while the
      agent is active, waits automatically when the agent is idle, and
      resumes on the next agent activity. Off means a classic
      wall-clock Pomodoro. Settings are saved on this machine and
      survive gateway restarts.</p>
      <div class="form-btns">
        <button id="btnClear" class="text-btn ghost" type="button">Clear statistics</button>
        <button id="btnSave" class="text-btn" type="button">Save settings</button>
      </div>
    </div>
  </div>
</div>
<script>
(function () {
  var RING_C = 565.49; // 2 * PI * 90
  var lastPhase = null;
  var audioCtx = null;
  var isSettings = location.pathname.replace(/\/$/, "").endsWith("/settings");
  document.getElementById("view-timer").hidden = isSettings;
  document.getElementById("view-settings").hidden = !isSettings;

  var ICON_PLAY = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>';
  var ICON_PAUSE = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/></svg>';
  var ICON_SKIP = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/></svg>';
  var ICON_RESET = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';
  document.getElementById("btnStart").innerHTML = ICON_PLAY;
  document.getElementById("btnSkip").innerHTML = ICON_SKIP;
  document.getElementById("btnReset").innerHTML = ICON_RESET;

  function fmt(s) {
    s = Math.max(0, Math.round(s));
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = 880;
      g.gain.setValueAtTime(0.12, audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);
      o.connect(g); g.connect(audioCtx.destination);
      o.start(); o.stop(audioCtx.currentTime + 0.6);
    } catch (e) {}
  }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); })
      .catch(function (e) { document.getElementById("err").textContent = "Request failed: " + e; });
  }
  function refresh() {
    fetch("/api/state").then(function (r) { return r.json(); }).then(function (d) {
      document.getElementById("err").textContent = "";
      var st = d.timer || d;
      var phaseEl = document.getElementById("phase");
      var ring = document.getElementById("ring");
      var running = !!st.running;
      var isBreak = st.phase === "short_break" || st.phase === "long_break";
      // One state signal: label + accent colour. Idle/paused are grey,
      // an active phase is the single teal accent.
      var label = "Idle", accent = false;
      if (st.phase === "focus") { label = running ? "Focus" : "Paused"; accent = running; }
      else if (isBreak) { label = "Break"; accent = running; }
      phaseEl.textContent = label;
      phaseEl.style.color = accent ? "#0F766E" : "#6B7280";
      document.getElementById("timer").textContent = fmt(st.remaining_s);
      var frac = st.duration_s ? Math.min(1, Math.max(0, st.elapsed_s / st.duration_s)) : 0;
      ring.setAttribute("stroke-dashoffset", (RING_C * (1 - frac)).toFixed(1));
      ring.style.stroke = accent ? "#0F766E" : (st.phase === "idle" ? "#E5E7EB" : "#9CA3AF");
      var sl = document.getElementById("statusline");
      if (st.phase === "focus" && running) {
        sl.textContent = !st.agent_aware ? "Manual mode — wall-clock timer running"
          : st.waiting_for_agent ? "Waiting for the agent — resumes automatically"
          : "Counting — agent is active";
      } else if (st.phase === "focus") {
        sl.textContent = "Paused — press play to resume";
      } else if (st.phase === "idle") {
        sl.textContent = st.agent_aware ? "Agent-aware — starts when the agent works" : "Manual mode — wall-clock timer";
      } else {
        sl.textContent = "Break — rest until the timer ends";
      }
      var btnStart = document.getElementById("btnStart");
      btnStart.disabled = false;
      if (running) {
        btnStart.innerHTML = ICON_PAUSE;
        btnStart.setAttribute("aria-label", "Pause timer");
      } else {
        btnStart.innerHTML = ICON_PLAY;
        btnStart.setAttribute("aria-label", st.phase === "focus" ? "Resume timer" : "Start focus");
      }
      document.getElementById("btnSkip").disabled = st.phase === "idle";
      document.getElementById("btnReset").disabled = st.phase === "idle" && !running;
      var stats = d.stats || {};
      document.getElementById("stToday").textContent = (stats.today_sessions || 0) + " sessions · " + (stats.today_minutes || 0) + " min";
      document.getElementById("stWeek").textContent = (stats.week_sessions || 0) + " sessions · " + (stats.week_minutes || 0) + " min";
      document.getElementById("stStreak").textContent = (stats.streak_days || 0) + " days";
      var log = document.getElementById("log");
      var items = (d.sessions || []).slice(-5).reverse();
      document.getElementById("log-empty").hidden = items.length > 0;
      log.innerHTML = items.map(function (s) {
        var when = new Date(s.ended_at * 1000).toLocaleString();
        var mins = Math.round(s.duration_s / 60);
        return "<li><span>" + when + " · " + s.mode + "</span><span class=\"muted\">" + mins + " min · " + (s.completed ? "done" : "abandoned") + "</span></li>";
      }).join("");
      if (lastPhase !== null && lastPhase !== st.phase && (d.settings || {}).sound_enabled !== false) beep();
      lastPhase = st.phase;
      var s = d.settings || {};
      if (document.activeElement === document.body) {
        document.getElementById("setFocus").value = s.focus_minutes;
        document.getElementById("setShort").value = s.short_break_minutes;
        document.getElementById("setLong").value = s.long_break_minutes;
        document.getElementById("setCycle").value = s.sessions_before_long_break;
        document.getElementById("setAware").checked = !!s.agent_aware;
        document.getElementById("setIdle").value = s.idle_threshold_seconds;
        document.getElementById("setAutoBreak").checked = !!s.auto_start_breaks;
        document.getElementById("setAutoFocus").checked = !!s.auto_start_focus;
        document.getElementById("setSound").checked = !!s.sound_enabled;
      }
    }).catch(function (e) {
      document.getElementById("err").textContent = "Cannot reach the widget server — check that the agent plugin is enabled, then reload this page.";
    });
  }
  document.getElementById("btnStart").onclick = function () {
    var label = document.getElementById("btnStart").getAttribute("aria-label");
    post("/api/control", { action: label === "Pause timer" ? "pause" : "start" }).then(refresh);
  };
  document.getElementById("btnSkip").onclick = function () { post("/api/control", { action: "skip" }).then(refresh); };
  document.getElementById("btnReset").onclick = function () { post("/api/control", { action: "reset" }).then(refresh); };
  document.getElementById("btnDetails").onclick = function () {
    var body = document.getElementById("details-body");
    var open = body.hidden;
    body.hidden = !open;
    document.getElementById("btnDetails").setAttribute("aria-expanded", String(open));
  };
  document.getElementById("btnSave").onclick = function () {
    var btn = document.getElementById("btnSave");
    post("/api/settings", {
      focus_minutes: parseInt(document.getElementById("setFocus").value, 10),
      short_break_minutes: parseInt(document.getElementById("setShort").value, 10),
      long_break_minutes: parseInt(document.getElementById("setLong").value, 10),
      sessions_before_long_break: parseInt(document.getElementById("setCycle").value, 10),
      agent_aware: document.getElementById("setAware").checked,
      idle_threshold_seconds: parseInt(document.getElementById("setIdle").value, 10),
      auto_start_breaks: document.getElementById("setAutoBreak").checked,
      auto_start_focus: document.getElementById("setAutoFocus").checked,
      sound_enabled: document.getElementById("setSound").checked
    }).then(function () {
      btn.textContent = "Saved";
      setTimeout(function () { btn.textContent = "Save settings"; }, 1500);
      refresh();
    });
  };
  document.getElementById("btnClear").onclick = function () {
    if (confirm("Clear all recorded sessions and statistics?")) post("/api/control", { action: "reset_stats" }).then(refresh);
  };
  refresh();
  setInterval(refresh, 1000);
})();
</script>
</body>
</html>
"""


def build_widget_html() -> str:
    return HTML_PAGE

"""Self-contained widget page for Hermes Focus Widget.

Served by the loopback server (http://127.0.0.1:<port>/). No external
resources, no CDN, no fonts fetched: everything inline so the page works
offline. The page polls /api/state once a second and posts to
/api/control and /api/settings. A short WebAudio beep marks phase
changes when sound is enabled.
"""

from __future__ import annotations

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Focus Widget — Hermes</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1115; color: #e6e6e6; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 20px 16px 32px; }
  h1 { font-size: 17px; font-weight: 700; letter-spacing: .02em; }
  .sub { color: #9aa0a6; font-size: 13px; margin-top: 4px; }
  .card { background: #171a21; border: 1px solid #262b36; border-radius: 16px; padding: 20px; margin-top: 16px; }
  .phase { text-align: center; font-size: 13px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: #f0b429; }
  .phase.break { color: #4cc38a; }
  .phase.idle { color: #9aa0a6; }
  .timer { text-align: center; font-size: 64px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: .02em; margin: 8px 0 4px; }
  .statusline { text-align: center; font-size: 13px; color: #9aa0a6; min-height: 18px; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
  .dot.on { background: #4cc38a; }
  .dot.off { background: #f26d6d; }
  .dot.wait { background: #f0b429; }
  .progress { height: 8px; background: #262b36; border-radius: 99px; overflow: hidden; margin: 14px 0 16px; }
  .progress > div { height: 100%; background: #f0b429; border-radius: 99px; transition: width .9s linear; }
  .btns { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
  button { background: #f0b429; color: #141414; border: 0; border-radius: 10px; padding: 10px 18px; font-size: 14px; font-weight: 700; cursor: pointer; }
  button.ghost { background: #262b36; color: #e6e6e6; font-weight: 600; }
  button:disabled { opacity: .45; cursor: default; }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 16px; }
  .stat { background: #171a21; border: 1px solid #262b36; border-radius: 12px; padding: 12px; text-align: center; }
  .stat b { display: block; font-size: 22px; }
  .stat span { font-size: 12px; color: #9aa0a6; }
  .sect { font-size: 13px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #9aa0a6; margin: 22px 0 8px; }
  .log { list-style: none; font-size: 13px; }
  .log li { padding: 8px 2px; border-bottom: 1px solid #23272f; display: flex; justify-content: space-between; gap: 8px; }
  .log li:last-child { border-bottom: 0; }
  .log .muted { color: #9aa0a6; }
  .empty { color: #9aa0a6; font-size: 13px; padding: 6px 2px; }
  .settings label { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid #23272f; font-size: 14px; }
  .settings label:last-child { border-bottom: 0; }
  .settings input[type="number"] { width: 72px; background: #0f1115; color: #e6e6e6; border: 1px solid #2c313c; border-radius: 8px; padding: 6px 8px; font-size: 14px; text-align: right; }
  .settings input[type="checkbox"] { width: 18px; height: 18px; accent-color: #f0b429; }
  .note { font-size: 12px; color: #9aa0a6; margin-top: 16px; line-height: 1.5; }
  .err { color: #f26d6d; font-size: 13px; text-align: center; margin-top: 10px; min-height: 16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Focus Widget</h1>
  <p class="sub">Pomodoro that only counts while the agent is working.</p>

  <div class="card">
    <div class="phase idle" id="phase">Idle</div>
    <div class="timer" id="timer">25:00</div>
    <div class="statusline" id="statusline"><span class="dot off"></span>Agent status unknown</div>
    <div class="progress"><div id="bar" style="width:0%"></div></div>
    <div class="btns">
      <button id="btnStart">Start focus</button>
      <button id="btnPause" class="ghost">Pause</button>
      <button id="btnSkip" class="ghost">Skip</button>
      <button id="btnReset" class="ghost">Reset</button>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="stats">
    <div class="stat"><b id="stToday">0</b><span>sessions today</span></div>
    <div class="stat"><b id="stTodayMin">0</b><span>minutes today</span></div>
    <div class="stat"><b id="stStreak">0</b><span>day streak</span></div>
  </div>
  <div class="stats">
    <div class="stat"><b id="stWeek">0</b><span>sessions this week</span></div>
    <div class="stat"><b id="stWeekMin">0</b><span>minutes this week</span></div>
    <div class="stat"><b id="stTotal">0</b><span>total sessions</span></div>
  </div>

  <div class="sect">Recent sessions</div>
  <ul class="log" id="log"></ul>

  <div class="sect">Settings</div>
  <div class="card settings" style="margin-top:0">
    <label>Focus (minutes) <input type="number" id="setFocus" min="1" max="180"></label>
    <label>Short break (minutes) <input type="number" id="setShort" min="1" max="60"></label>
    <label>Long break (minutes) <input type="number" id="setLong" min="1" max="60"></label>
    <label>Sessions before long break <input type="number" id="setCycle" min="2" max="8"></label>
    <label>Agent-aware timer <input type="checkbox" id="setAware"></label>
    <label>Idle threshold (seconds) <input type="number" id="setIdle" min="10" max="1800"></label>
    <label>Auto-start breaks <input type="checkbox" id="setAutoBreak"></label>
    <label>Auto-start next focus <input type="checkbox" id="setAutoFocus"></label>
    <label>Sound on phase change <input type="checkbox" id="setSound"></label>
    <div class="btns" style="margin-top:14px; justify-content:flex-end">
      <button id="btnSave" class="ghost">Save settings</button>
      <button id="btnClear" class="ghost">Clear statistics</button>
    </div>
  </div>

  <p class="note">100% local: no network calls, no accounts, no telemetry.
  In agent-aware mode the focus timer runs only while the agent is
  active; it waits automatically when the agent is idle and resumes on
  the next agent activity. Switch it off for a classic wall-clock
  Pomodoro.</p>
</div>
<script>
(function () {
  var lastPhase = null;
  var audioCtx = null;

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
  function phaseLabel(p) {
    return { idle: "Idle", focus: "Focus", short_break: "Short break", long_break: "Long break" }[p] || p;
  }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); })
      .catch(function (e) { document.getElementById("err").textContent = "Request failed: " + e; });
  }
  function refresh() {
    fetch("/api/state").then(function (r) { return r.json(); }).then(function (d) {
      var st = d.timer || d;
      var phaseEl = document.getElementById("phase");
      phaseEl.textContent = phaseLabel(st.phase) + (st.phase === "focus" && st.focus_completed ? "  ·  " + st.focus_completed + "/" + st.sessions_before_long_break : "");
      phaseEl.className = "phase" + (st.phase === "short_break" || st.phase === "long_break" ? " break" : st.phase === "idle" ? " idle" : "");
      document.getElementById("timer").textContent = fmt(st.remaining_s);
      var bar = st.duration_s ? Math.round(100 * st.elapsed_s / st.duration_s) : 0;
      document.getElementById("bar").style.width = bar + "%";
      var sl = document.getElementById("statusline");
      if (st.phase === "focus" && st.running) {
        if (!st.agent_aware) {
          sl.innerHTML = '<span class="dot on"></span>Manual mode — wall-clock timer running';
        } else if (st.waiting_for_agent) {
          sl.innerHTML = '<span class="dot wait"></span>Waiting for the agent… timer paused';
        } else {
          sl.innerHTML = '<span class="dot on"></span>Agent active — focus time counting';
        }
      } else if (st.phase === "focus") {
        sl.innerHTML = '<span class="dot off"></span>Paused';
      } else if (st.phase === "idle") {
        sl.innerHTML = st.agent_aware
          ? '<span class="dot ' + (st.agent_active ? "on" : "off") + '"></span>Agent ' + (st.agent_active ? "active" : "idle") + ' — start a focus session when ready'
          : '<span class="dot off"></span>Manual mode — start a focus session when ready';
      } else {
        sl.innerHTML = '<span class="dot on"></span>Break — rest, the timer resumes afterwards';
      }
      document.getElementById("btnStart").disabled = st.running && st.phase !== "idle";
      document.getElementById("btnPause").disabled = !st.running;
      document.getElementById("btnSkip").disabled = st.phase === "idle";
      document.getElementById("btnReset").disabled = st.phase === "idle" && !st.running;
      var stats = d.stats || {};
      document.getElementById("stToday").textContent = stats.today_sessions || 0;
      document.getElementById("stTodayMin").textContent = stats.today_minutes || 0;
      document.getElementById("stStreak").textContent = stats.streak_days || 0;
      document.getElementById("stWeek").textContent = stats.week_sessions || 0;
      document.getElementById("stWeekMin").textContent = stats.week_minutes || 0;
      document.getElementById("stTotal").textContent = stats.total_sessions || 0;
      var log = document.getElementById("log");
      var items = (d.sessions || []).slice(-8).reverse();
      if (!items.length) {
        log.innerHTML = '<li class="empty">No sessions yet — finish a focus session and it shows up here.</li>';
      } else {
        log.innerHTML = items.map(function (s) {
          var when = new Date(s.ended_at * 1000).toLocaleString();
          var mins = Math.round(s.duration_s / 60);
          return "<li><span>" + when + ' · ' + s.mode + "</span><span class=\"muted\">" + mins + " min · " + (s.completed ? "done" : "abandoned") + "</span></li>";
        }).join("");
      }
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
      document.getElementById("err").textContent = "Cannot reach the widget server: " + e;
    });
  }
  document.getElementById("btnStart").onclick = function () { post("/api/control", { action: "start" }).then(refresh); };
  document.getElementById("btnPause").onclick = function () { post("/api/control", { action: "pause" }).then(refresh); };
  document.getElementById("btnSkip").onclick = function () { post("/api/control", { action: "skip" }).then(refresh); };
  document.getElementById("btnReset").onclick = function () { post("/api/control", { action: "reset" }).then(refresh); };
  document.getElementById("btnSave").onclick = function () {
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
    }).then(refresh);
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

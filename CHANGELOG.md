# Changelog

## 1.0.0 — 2026-11-12

Initial release.

- Agent-aware Pomodoro timer: focus 25 / short break 5 / long break 15 minutes after 4 focus sessions (all configurable). In agent-aware mode the focus timer only counts while the agent is active (hooks on `pre_llm_call`, `pre_tool_call`, `transform_llm_output`, `on_session_start`); it waits automatically on agent idle and resumes on the next activity.
- Manual mode is first-class: agent-awareness off means a classic wall-clock Pomodoro.
- Three Desktop surfaces: draggable floating widget, status-bar chip (`🍅 mm:ss` / `☕ mm:ss`, click opens the page), and a full page (sidebar + command palette) embedding the loopback widget UI.
- Session log + statistics: today / this week totals and a day streak; state in local JSON, written atomically, survives gateway restarts.
- Slash command `/focus start | pause | resume | stop | status | stats | reset`; tool `focus_timer` with the same actions.
- Settings page: durations, sessions before a long break, agent-aware toggle, idle threshold, auto-start breaks/focus, sound on phase change.
- Phase-change notifications: in-app toast + native OS notification when away; WebAudio beep in the widget page.
- 100% local: no network calls, no credentials, no telemetry. Loopback-only server (default `127.0.0.1:18778`).
- Tests: 55 unit + contract + persistence + end-to-end tests, all passing.

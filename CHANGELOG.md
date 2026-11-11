# Changelog

## 1.0.0 — 2026-11-12

Initial release.

- Agent-aware Pomodoro timer: focus 25 / short break 5 / long break 15 minutes after 4 focus sessions (all configurable). In agent-aware mode the focus timer only counts while the agent is active (hooks on `pre_llm_call`, `pre_tool_call`, `transform_llm_output`, `post_api_request`, `on_session_start`); it waits automatically on agent idle and resumes on the next activity.
- Manual mode is first-class: agent-awareness off means a classic wall-clock Pomodoro.
- Three Desktop surfaces: draggable floating widget, status-bar chip (`🍅 mm:ss` / `☕ mm:ss`, click opens the page), and a full page (sidebar + command palette) embedding the loopback widget UI.
- Session log + statistics: today / this week totals and a day streak; state in local JSON, written atomically, survives gateway restarts.
- Slash command `/pomodoro start | pause | resume | stop | status | stats | reset`; tool `focus_timer` with the same actions.
- Settings page: durations, sessions before a long break, agent-aware toggle, idle threshold, auto-start breaks/pomodoro, sound on phase change.
- Phase-change notifications: in-app toast + native OS notification when away; WebAudio beep in the widget page.
- 100% local: no network calls, no credentials, no telemetry. Loopback-only server (default `127.0.0.1:18778`).
- Tests: 69 unit + contract + persistence + end-to-end tests, all passing.

### Fixes in 1.0.0 (same release, before catalog merge)

- Agent-activity signal: added the `post_api_request` hook. Live testing showed Desktop chat turns do not reach the `pre_llm_call` dispatcher, so the turn-preparation hooks alone never stamped activity from Desktop chat; the post-response hook fires inside `normalize_model_response` on every model API response and is the second activity signal.
- Cross-process activity sharing: the plugin runs in two processes at once — the gateway (which serves the widget) and the Desktop's own `hermes serve` backend (where Desktop chat turns execute) — each with its own in-memory timer but one shared state file. A stamp that stayed in memory was invisible to the other process, so the widget never saw Desktop turns even when the hook fired. Now every hook call persists the stamp to the shared file immediately, and each process folds the newest stamp back into its timer on every tick (max-merge, so a save can never drag the stamp backwards). Only the activity timestamp is shared; phase, elapsed time and settings stay local to each process.
- Chip popover clipped: the popover used to render inside the chip's React tree, positioned absolute above the chip — the status-bar host clips anything outside its own box, so only a sliver painted (full 232×254 rect in the DOM, invisible on screen). It now renders as a plain DOM node appended to `document.body` (`position:fixed`), outside the clipping ancestor, placed with the same measure-and-correct loop as the floating widget.

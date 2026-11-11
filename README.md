# Hermes Focus Widget

A Pomodoro widget for Hermes Desktop that only counts focus time **while the agent is actually working**.

Most Pomodoro timers count wall-clock minutes — including the minutes you spend waiting for the agent to finish a long task. This one does not. The agent-side plugin watches real agent activity (messages, tool calls, outputs) and the timer runs only while the agent is active. When the agent goes idle, the timer waits. When the agent works again, it resumes. Focus, breaks, session log, statistics and streak — in one widget, with zero setup and zero credentials.

- **Agent-aware Pomodoro** — 25 min focus / 5 min short break / 15 min long break after 4 sessions (all configurable). The focus timer only accumulates while the agent is active; it pauses automatically on agent idle and resumes on the next agent activity.
- **Manual mode is first-class** — switch agent-awareness off and it is a full classic wall-clock Pomodoro. Useful whenever the agent is idle or you work outside Hermes.
- **Two surfaces** — a draggable floating widget on the Desktop window and a status-bar chip (`🍅 12:34` while focusing, `☕ 04:59` on a break). Clicking either opens the full page.
- **Session log + statistics** — every focus session is recorded (date, duration, completed/abandoned, mode). Today / this week totals and a day streak, shown on the page.
- **Slash command** — `/pomodoro start | pause | resume | stop | status | stats | reset`, straight from chat.
- **100% local** — no network calls, no accounts, no API keys, no telemetry. State lives in one JSON file under the Hermes plugin-data directory and survives gateway restarts.

## Who is this for

| Persona | How it helps |
|---|---|
| **Coder** | Long agent sessions (refactors, test runs, reviews) get a rhythm: 25 minutes of real agent work, then a real break — instead of losing track of time across a 3-hour session. |
| **Writer** | Drafting with the agent in long turns; the timer measures the work, not the waiting between turns. |
| **Researcher** | Deep reading + agent synthesis sessions with a visible record of how much focused time the day actually had. |
| **Student** | Study sessions with the agent as a tutor, with a streak that makes showing up every day visible. |

## Why not just use a phone Pomodoro?

A phone Pomodoro counts wall-clock minutes even while you wait for the agent. This widget knows when the agent is working — that is the whole point, and only a Hermes plugin can know it. It also lives where the work happens (the Desktop window), needs no second device, and keeps the session log next to the sessions themselves.

## Focus Widget vs a plain Pomodoro

| | Plain Pomodoro (phone / web) | Hermes Focus Widget |
|---|---|---|
| Counts | Wall-clock minutes | Only minutes the agent is active (agent-aware mode) |
| Manual mode | The only mode | Full classic mode, one toggle |
| Knows agent activity | No | Yes — hooks on messages, tool calls, outputs |
| Session log + streak | Sometimes, in the cloud | Yes, local JSON, survives restarts |
| Lives where you work | Another device / tab | Floating widget + status-bar chip in Hermes Desktop |
| Setup | Account / app install | None — install the plugin, it runs |
| Credentials / network | Often required | None — 100% local |

## Install

1. Install the plugin into Hermes (`hermes plugins install hermes-focus-widget`, or copy this folder to `~/.hermes/plugins/hermes-focus-widget/`).
2. Restart the gateway (`systemctl --user restart hermes-gateway`) or start a session — the plugin loads on start.
3. The widget appears in Hermes Desktop: a floating widget (bottom-right, draggable) and a status-bar chip. The full page is in the sidebar under **Focus Widget**, or run `/pomodoro status` in chat.
4. The widget UI is served locally at `http://127.0.0.1:18778/` (loopback only; port configurable via `hud_port`).

No account, no API key, no extra install step.

## Usage

- **Start / Pause / Skip / Reset** — buttons on the widget, or `/pomodoro start`, `/pomodoro pause`, `/pomodoro stop`, `/pomodoro reset` in chat.
- **Settings** — on the widget page: focus / short break / long break durations, sessions before a long break, agent-aware on/off, idle threshold (seconds without agent activity before the agent counts as idle), auto-start breaks, auto-start next focus, sound on phase change.
- **Statistics** — today / this week totals and the day streak on the page; `/pomodoro stats` in chat.
- **Floating widget** — drag it anywhere; the position is saved. Close it with `×`; bring it back from the command palette (`Focus Widget: Toggle floating widget`).
- **Status-bar chip** — shows the phase and time left at a glance; click it to open the full page.

## How agent-awareness works

The agent-side plugin registers hooks (`pre_llm_call`, `pre_tool_call`, `transform_llm_output`, `on_session_start`). Every hook call stamps the last-activity time. The timer thread ticks once a second; in agent-aware focus mode a second only counts when the last activity is within the idle threshold (default 120 s). In manual mode every second counts. Breaks always count wall-clock time — a break is a break.

State (timer phase, elapsed seconds, settings, session log) is stored as JSON in `~/.hermes/plugin-data/hermes-focus-widget/pomodoro-state.json`, written atomically, and restored on gateway restart — a session in progress survives a restart.

## Limits (honest)

- **Agent activity is not human focus.** The timer knows when the *agent* is working, not whether *you* are focused. If you stare out the window while the agent runs, the timer still counts.
- **It cannot see you typing outside Hermes.** Activity is only what the agent observes through its hooks. Work you do in another app is invisible to it — use manual mode for that.
- **Idle threshold is a heuristic.** The default 120 s means a gap of up to two minutes between agent actions still counts as active. Shorten it if you want stricter accounting.
- **Local installs only.** The widget UI is served on loopback (`127.0.0.1`); if Desktop connects to a remote gateway, the floating widget and chip cannot reach it and will say so — the full page is served from the same loopback server and has the same limit.
- **One timer per gateway.** Sessions are recorded per gateway, not per chat session or profile.

## Disclosure

- Network calls: none. The widget UI and its JSON API are served on loopback only (`127.0.0.1:18778` by default); the Desktop surfaces poll that endpoint (floating widget and chip every 2 s, full page every 1 s).
- Credentials: none read, none stored, none required.
- Reads outside the plugin's own data: none (the plugin only reads its own state file).
- Shell commands / long-running background processes: one daemon thread for the 1-second timer tick and one loopback HTTP server thread, both inside the gateway process; both stop with the gateway.
- Data stored: `~/.hermes/plugin-data/hermes-focus-widget/pomodoro-state.json` (timer state, settings, session log). No telemetry leaves the machine.
- No self-updating code. Updates reach users only through a new release.

## Development

```bash
python3 -m unittest discover -s tests -t .   # 55 tests, no dependencies
node --check desktop/plugin.js               # desktop surface syntax
```

## License

MIT — see [LICENSE](LICENSE).

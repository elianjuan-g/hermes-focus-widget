"""Hermes Focus Widget — a Pomodoro timer that knows when the agent works.

Agent side (this module):
- hooks (on_session_start, pre_llm_call, pre_tool_call,
  transform_llm_output, post_api_request) record agent activity
  timestamps;
- activity is shared across the two processes that run this plugin
  (the gateway, which serves /api/state to the widget, and the
  Desktop's own ``hermes serve`` backend, where Desktop chat turns
  execute): each hook stamps the shared state file immediately, and
  each process folds the newest stamp back in on every tick, so the
  widget sees turns from either process within a second;
- a loopback-only HTTP server (default 127.0.0.1:18778) serves the widget
  page and a small JSON API (/api/state, /api/control, /api/settings);
- one tool (focus_timer) exposes status/start/pause/resume/skip/reset/
  stats to the agent;
- all state is local JSON under the Hermes plugin-data directory.

The focus timer only accumulates time while the agent is active
(agent-aware mode, on by default). Switch it off in settings for a
classic wall-clock Pomodoro. 100% local: no network calls, no
credentials, no telemetry.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .store import load_state, save_state
from .timer import FocusTimer, compute_stats
from .ui import build_widget_html

__all__ = [
    "register",
    "get_timer",
    "ensure_widget_server_running",
    "on_session_start_hook",
    "pre_llm_call_hook",
    "pre_tool_call_hook",
    "transform_llm_output_hook",
    "post_api_request_hook",
    "tool_focus_timer",
]

PLUGIN_KEY = "hermes-focus-widget"
DEFAULT_PORT = 18778

_TIMER: Optional[FocusTimer] = None
# RLock (not Lock): request handlers acquire it and then call helpers
# (_state_payload) that acquire it again on the same thread. A plain
# Lock deadlocked every POST and every /api/state read after one.
_TIMER_LOCK = threading.RLock()
_SERVER_STARTED = False
_SERVER_OK = False
_TICK_THREAD_STARTED = False


def _default_plugin_config() -> Dict[str, Any]:
    """config_schema defaults from plugin.yaml (fresh dict per call)."""
    return {
        "focus_minutes": 25,
        "short_break_minutes": 5,
        "long_break_minutes": 15,
        "sessions_before_long_break": 4,
        "agent_aware": True,
        "idle_threshold_seconds": 120,
        "auto_start_breaks": False,
        "auto_start_focus": False,
        "sound_enabled": True,
        "hud_port": 18778,
        "auto_launch_server": True,
    }


def _read_plugin_config(ctx: Any) -> Dict[str, Any]:
    """Read every config_schema key through the public reader.

    The plugin context has no ``config`` attribute; the public reader is
    ``ctx.get_config("<key>", default)`` (Hermes 0.21.5). Defensive:
    a context without get_config falls back to a legacy ``config``
    dict, then to the plugin.yaml defaults, so the validator's mock
    context never crashes register().
    """
    defaults = _default_plugin_config()
    get_config = getattr(ctx, "get_config", None)
    if callable(get_config):
        try:
            return {k: get_config(k, v) for k, v in defaults.items()}
        except Exception:
            pass
    legacy = getattr(ctx, "config", None)
    if isinstance(legacy, dict):
        return {k: legacy.get(k, v) for k, v in defaults.items()}
    return defaults


def get_timer(config: Optional[Dict[str, Any]] = None) -> FocusTimer:
    """Process-wide singleton timer (restored from disk on first use).

    Precedence is deliberate: disk state is the source of truth for
    settings the user changed in the widget settings page (persisted
    by /api/settings). Config (plugin.yaml defaults / config.yaml)
    only seeds a FIRST run with no disk state. The previous code
    applied config over disk state on every reload, so each gateway
    or plugin reload silently reverted the user's choices to the
    schema defaults — e.g. agent_aware back to True, freezing a
    manual-mode timer in "waiting for agent" (seen live 12 Nov 2026).
    """
    global _TIMER
    if _TIMER is None:
        saved = load_state()
        if saved:
            timer = FocusTimer.from_dict(saved)
        else:
            timer = FocusTimer(config)
        _TIMER = timer
    return _TIMER


def _merge_disk_activity(timer: FocusTimer) -> None:
    """Fold the newest activity stamp from the shared state file in.

    The plugin runs in TWO processes at once: the gateway (which serves
    /api/state to the widget) and the Desktop's own ``hermes serve``
    backend (where Desktop chat turns execute). Each process has its own
    in-memory timer, but both share one state file. A stamp that lands
    in one process must reach the other, or the widget never sees
    Desktop turns. Only ``last_activity_at`` is merged — never phase,
    elapsed, running or settings, which stay local to each process so
    the two tick loops cannot fight over control state. The merge is a
    max: activity timestamps only move forward, so it is idempotent
    and can never regress the timer. Best effort, never raises.
    """
    try:
        disk_last = load_state().get("last_activity_at")
        if isinstance(disk_last, (int, float)):
            if timer.last_activity_at is None or disk_last > timer.last_activity_at:
                timer.last_activity_at = disk_last
    except Exception:
        pass


def _persist() -> None:
    """Save the timer; merge the disk stamp first so a save can never
    regress ``last_activity_at`` to an older value (two processes save
    the same file on their own cadences)."""
    try:
        with _TIMER_LOCK:
            if _TIMER is not None:
                _merge_disk_activity(_TIMER)
                save_state(_TIMER.to_dict())
    except Exception:
        pass


def _tick_loop() -> None:
    """Advance the timer once a second; persist every 30 ticks.

    Each tick first folds in activity stamped by the other process
    (see _merge_disk_activity), so a Desktop turn that stamped the
    serve backend's timer shows up in this process within a second.
    The save happens after releasing the lock: _persist() takes it.
    """
    count = 0
    while True:
        try:
            time.sleep(1)
            with _TIMER_LOCK:
                timer = get_timer()
                _merge_disk_activity(timer)
                before = (timer.phase, timer.elapsed_s)
                timer.tick()
                count += 1
                need_save = before != (timer.phase, timer.elapsed_s) or count % 30 == 0
            if need_save:
                _persist()
        except Exception:
            time.sleep(1)


class _FocusWidgetHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep the gateway log clean
        pass

    # -- helpers ------------------------------------------------------
    def _loopback_only(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _state_payload(self) -> Dict[str, Any]:
        with _TIMER_LOCK:
            timer = get_timer()
            now = time.time()
            return {
                "status": "ok",
                "timer": timer.state_dict(now),
                "stats": compute_stats(timer.sessions, now),
                "sessions": timer.sessions[-20:],
                "settings": dict(timer.settings),
            }

    # -- routes --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        if not self._loopback_only():
            self.send_response(403)
            self.end_headers()
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/settings"):
            # Same page; the client shows the settings view when the
            # path is /settings (separate view, same server).
            self._send_html(build_widget_html())
            return
        if path == "/api/state":
            self._send_json(self._state_payload())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        if not self._loopback_only():
            self.send_response(403)
            self.end_headers()
            return
        path = urlparse(self.path).path
        body = self._read_json_body()
        with _TIMER_LOCK:
            timer = get_timer()
            now = time.time()
            if path == "/api/control":
                action = str(body.get("action", "")).lower()
                if action == "start":
                    timer.start(now)
                elif action == "pause":
                    timer.pause(now)
                elif action == "resume":
                    timer.resume(now)
                elif action == "skip":
                    timer.skip(now)
                elif action == "reset":
                    timer.reset(now)
                elif action == "reset_stats":
                    timer.reset_stats()
                else:
                    self._send_json({"status": "error", "error": "unknown action"}, 400)
                    return
                save_state(timer.to_dict())
                self._send_json(self._state_payload())
                return
            if path == "/api/settings":
                allowed = {
                    "focus_minutes": (1, 180),
                    "short_break_minutes": (1, 60),
                    "long_break_minutes": (1, 60),
                    "sessions_before_long_break": (2, 8),
                    "idle_threshold_seconds": (10, 1800),
                }
                patch: Dict[str, Any] = {}
                for key, (low, high) in allowed.items():
                    if key in body:
                        try:
                            patch[key] = max(low, min(high, int(body[key])))
                        except (TypeError, ValueError):
                            pass
                for key in (
                    "agent_aware",
                    "auto_start_breaks",
                    "auto_start_focus",
                    "sound_enabled",
                ):
                    if key in body:
                        patch[key] = bool(body[key])
                timer.update_settings(patch)
                save_state(timer.to_dict())
                self._send_json(self._state_payload())
                return
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()


def ensure_widget_server_running(port: int = DEFAULT_PORT) -> str:
    """Start the loopback widget server once (daemon thread)."""
    global _SERVER_STARTED, _SERVER_OK, _TICK_THREAD_STARTED
    if not _SERVER_STARTED:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _FocusWidgetHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            _SERVER_STARTED = True
            _SERVER_OK = True
        except Exception:
            _SERVER_OK = False
    if not _TICK_THREAD_STARTED:
        threading.Thread(target=_tick_loop, daemon=True).start()
        _TICK_THREAD_STARTED = True
    return f"http://127.0.0.1:{port}"


# -- hooks (agent activity) ------------------------------------------
def _note_activity() -> None:
    """Stamp activity in memory AND on disk, immediately.

    The disk write is the cross-process signal: the Desktop's serve
    backend and the gateway are separate processes sharing one state
    file, so a stamp that only lives in memory is invisible to the
    other side. Persisting here (instead of waiting for the tick
    loop's 30 s cadence) keeps the delay under a second.
    """
    try:
        get_timer().mark_activity()
        _persist()
    except Exception:
        pass


def on_session_start_hook(
    session_id: str = "default", platform: str = "cli", **kwargs: Any
) -> Dict[str, Any]:
    _note_activity()
    timer = get_timer()
    url = ""
    if timer.settings.get("auto_launch_server", True):
        url = ensure_widget_server_running(int(timer.settings.get("hud_port", DEFAULT_PORT)))
    return {"status": "FOCUS_WIDGET_READY", "widget_url": url, "widget_ok": bool(_SERVER_OK)}


def pre_llm_call_hook(
    user_message: Any = None, conversation_history: Any = None, **kwargs: Any
) -> Optional[Dict[str, Any]]:
    _note_activity()
    return None


def pre_tool_call_hook(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    session_id: str = "default",
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    _note_activity()
    return None


def transform_llm_output_hook(output: Any = "", **kwargs: Any) -> Any:
    _note_activity()
    return output


def post_api_request_hook(**kwargs: Any) -> None:
    """Stamp activity on every completed model API response.

    ``pre_llm_call`` fires in the turn-preparation path, but Desktop
    chat turns do not reach that dispatcher (verified live 12 Nov
    2026: three real Desktop turns left ``last_activity_at`` frozen
    while the gateway's own background review stamped it). The
    post-response hook fires inside ``normalize_model_response`` on
    every model API response (agent/turn_response_intake.py
    ``_fire_post_api_request_hook`` — the same hook budget-leash uses
    for metering), so it is the second activity signal. Desktop chat
    turns execute in the Desktop's own ``hermes serve`` backend, not
    in the gateway process, so this hook's stamp lands in the serve
    process's timer; it reaches the gateway (and the widget) through
    the shared state file (see _merge_disk_activity). The handler
    takes **kwargs only: the dispatcher passes ~20 keyword arguments
    (task_id, turn_id, model, provider, usage, ...) and none of them
    is needed to stamp activity.
    """
    _note_activity()


# -- tool -------------------------------------------------------------
def _focus_timer_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    action = str((args or {}).get("action", "status")).lower()
    with _TIMER_LOCK:
        timer = get_timer()
        now = time.time()
        if action == "status":
            result: Dict[str, Any] = {
                "timer": timer.state_dict(now),
                "stats": compute_stats(timer.sessions, now),
                "widget_url": ensure_widget_server_running(
                    int(timer.settings.get("hud_port", DEFAULT_PORT))
                ),
            }
        elif action == "start":
            result = {"timer": timer.start(now)}
        elif action == "pause":
            result = {"timer": timer.pause(now)}
        elif action == "resume":
            result = {"timer": timer.resume(now)}
        elif action == "skip":
            result = {"timer": timer.skip(now)}
        elif action == "reset":
            result = {"timer": timer.reset(now)}
        elif action == "stats":
            result = {
                "stats": compute_stats(timer.sessions, now),
                "sessions": timer.sessions[-20:],
            }
        else:
            return json.dumps({"status": "error", "error": f"unknown action: {action}"}, indent=2)
        save_state(timer.to_dict())
    return json.dumps(result, indent=2)


def tool_focus_timer(action: str = "status") -> str:
    """Direct-call wrapper (tests / scripts); the loader uses the handler."""
    return _focus_timer_handler({"action": action})


# -- slash command -----------------------------------------------------
_SUBCOMMANDS = {
    "start": "start",
    "pause": "pause",
    "resume": "resume",
    "stop": "reset",
    "reset": "reset",
    "status": "status",
    "stats": "stats",
}


def _focus_command_handler(*args: Any, **kwargs: Any) -> str:
    """``/pomodoro <subcommand>`` — start | pause | resume | stop | status
    | stats | reset. The handler is defensive about how Hermes passes
    the command text: first positional string, or ``text``/``args``/
    ``input`` keyword. No text (or an unknown word) shows status plus
    the usage line.
    """
    text = ""
    if args and isinstance(args[0], str):
        text = args[0]
    for key in ("text", "args", "input", "command"):
        if isinstance(kwargs.get(key), str):
            text = kwargs[key]
            break
    words = text.replace("/pomodoro", " ", 1).strip().split()
    sub = words[0].lower() if words else "status"
    action = _SUBCOMMANDS.get(sub)
    if action is None:
        return (
            "Unknown /pomodoro subcommand: "
            + sub
            + ". Usage: /pomodoro start | pause | resume | stop | status | stats | reset\n"
            + tool_focus_timer(action="status")
        )
    if action == "reset":
        return "Focus timer stopped and reset to idle.\n" + tool_focus_timer(
            action="reset"
        )
    if action == "start":
        return "Focus session started.\n" + tool_focus_timer(action="start")
    if action == "pause":
        return "Focus timer paused.\n" + tool_focus_timer(action="pause")
    if action == "resume":
        return "Focus timer resumed.\n" + tool_focus_timer(action="resume")
    return tool_focus_timer(action=action)


# -- entrypoint --------------------------------------------------------
def register(ctx: Any) -> None:
    """Official Hermes Plugin Entrypoint (`hermes_focus_widget:register`)."""
    cfg = _read_plugin_config(ctx)
    timer = get_timer(cfg)
    if timer.settings.get("auto_launch_server", True):
        ensure_widget_server_running(int(timer.settings.get("hud_port", DEFAULT_PORT)))

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("on_session_start", on_session_start_hook)
        ctx.register_hook("pre_llm_call", pre_llm_call_hook)
        ctx.register_hook("pre_tool_call", pre_tool_call_hook)
        ctx.register_hook("transform_llm_output", transform_llm_output_hook)
        ctx.register_hook("post_api_request", post_api_request_hook)

    if hasattr(ctx, "register_tool"):
        # One registration in the single form matching the 0.21.5
        # signature (name, toolset, schema, handler).
        ctx.register_tool(
            name="focus_timer",
            toolset="hermes_focus_widget",
            schema={
                "name": "focus_timer",
                "description": "Control the Focus Widget Pomodoro timer: status shows the current phase and stats; start/pause/resume/skip/reset control the timer; stats shows today/week totals and the focus streak.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "status",
                                "start",
                                "pause",
                                "resume",
                                "skip",
                                "reset",
                                "stats",
                            ],
                            "description": "Timer action to run.",
                        }
                    },
                    "required": ["action"],
                },
            },
            handler=_focus_timer_handler,
        )

    if hasattr(ctx, "register_command"):
        ctx.register_command(
            "/pomodoro",
            _focus_command_handler,
            description="Focus Widget Pomodoro: /pomodoro start | pause | resume | stop | status | stats | reset (widget: http://127.0.0.1:18778).",
        )

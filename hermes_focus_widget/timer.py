"""Pure Pomodoro state machine for Hermes Focus Widget.

The timer is intentionally clock-injectable: every time-dependent method
takes ``now`` (epoch seconds) so unit tests can drive it deterministically
without sleeping. No I/O, no threads, no Hermes imports here.

Phases: ``idle`` -> ``focus`` -> ``short_break`` / ``long_break`` -> ``idle``.
Agent-awareness: in ``focus`` with ``agent_aware`` on, elapsed time only
accumulates while the agent is active (last activity within
``idle_threshold_seconds``). When the agent goes idle the timer waits
(``waiting_for_agent``) and resumes automatically on the next activity.
In manual mode the timer is a plain wall-clock Pomodoro.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

PHASE_IDLE = "idle"
PHASE_FOCUS = "focus"
PHASE_SHORT_BREAK = "short_break"
PHASE_LONG_BREAK = "long_break"

MIN_ABANDONED_LOG_SECONDS = 60


class FocusTimer:
    """A Pomodoro timer that only counts focus time while the agent works."""

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        if settings:
            for key, value in settings.items():
                if key in self.settings:
                    self.settings[key] = value
        self.phase: str = PHASE_IDLE
        self.running: bool = False
        self.elapsed_s: int = 0
        self.focus_completed: int = 0  # focus sessions in the current cycle
        self.last_activity_at: Optional[float] = None
        self.phase_started_at: Optional[float] = None
        self.sessions: List[Dict[str, Any]] = []

    # -- configuration -------------------------------------------------
    #: Hard bounds for numeric settings. Out-of-range values (including
    #: 0 and negatives) are clamped to the nearest bound, never stored
    #: raw — a 0-minute focus would divide the timer by nothing and a
    #: negative idle threshold would mark the agent idle forever.
    SETTING_BOUNDS: Dict[str, Any] = {
        "focus_minutes": (1, 180),
        "short_break_minutes": (1, 60),
        "long_break_minutes": (1, 60),
        "sessions_before_long_break": (2, 8),
        "idle_threshold_seconds": (10, 1800),
    }
    SETTING_FLAGS = (
        "agent_aware",
        "auto_start_breaks",
        "auto_start_focus",
        "sound_enabled",
    )

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in patch.items():
            if key in self.SETTING_BOUNDS:
                try:
                    low, high = self.SETTING_BOUNDS[key]
                    self.settings[key] = max(low, min(high, int(value)))
                except (TypeError, ValueError):
                    pass  # unparseable value: keep the current setting
            elif key in self.SETTING_FLAGS:
                self.settings[key] = bool(value)
            elif key in ("hud_port", "auto_launch_server"):
                # Server settings: stored, applied on next gateway start.
                self.settings[key] = value
        return dict(self.settings)

    def phase_duration_s(self, phase: Optional[str] = None) -> int:
        phase = phase or self.phase
        minutes = {
            PHASE_FOCUS: self.settings["focus_minutes"],
            PHASE_SHORT_BREAK: self.settings["short_break_minutes"],
            PHASE_LONG_BREAK: self.settings["long_break_minutes"],
        }.get(phase, 0)
        return int(minutes) * 60

    # -- agent activity ------------------------------------------------
    def mark_activity(self, now: Optional[float] = None) -> None:
        """Record that the agent did something (hook call, tool result)."""
        self.last_activity_at = now if now is not None else time.time()

    def agent_active(self, now: Optional[float] = None) -> bool:
        if self.last_activity_at is None:
            return False
        now = now if now is not None else time.time()
        return (now - self.last_activity_at) <= float(
            self.settings["idle_threshold_seconds"]
        )

    def is_waiting_for_agent(self, now: Optional[float] = None) -> bool:
        """True when the user wants focus time but the agent is idle.

        Takes the same injectable clock as everything else in this
        class; ``now=None`` means wall-clock time.
        """
        if self.phase != PHASE_FOCUS or not self.running:
            return False
        if not self.settings["agent_aware"]:
            return False
        return not self.agent_active(now)

    @property
    def waiting_for_agent(self) -> bool:
        """Wall-clock view of :meth:`is_waiting_for_agent`."""
        return self.is_waiting_for_agent()

    # -- control -------------------------------------------------------
    def start(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = now if now is not None else time.time()
        if self.phase == PHASE_IDLE:
            self.phase = PHASE_FOCUS
            self.elapsed_s = 0
            self.phase_started_at = now
        self.running = True
        return self.state_dict(now)

    def pause(self, now: Optional[float] = None) -> Dict[str, Any]:
        self.running = False
        return self.state_dict(now)

    def resume(self, now: Optional[float] = None) -> Dict[str, Any]:
        if self.phase != PHASE_IDLE:
            self.running = True
        return self.state_dict(now)

    def skip(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Skip the current phase (abandoning an in-progress focus logs it)."""
        now = now if now is not None else time.time()
        if self.phase == PHASE_FOCUS and self.elapsed_s >= MIN_ABANDONED_LOG_SECONDS:
            self._log_session(now, completed=False)
        self._advance_after_break_or_skip(now)
        return self.state_dict(now)

    def reset(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Back to idle; keeps the session log and stats."""
        now = now if now is not None else time.time()
        if self.phase == PHASE_FOCUS and self.elapsed_s >= MIN_ABANDONED_LOG_SECONDS:
            self._log_session(now, completed=False)
        self.phase = PHASE_IDLE
        self.running = False
        self.elapsed_s = 0
        self.phase_started_at = None
        return self.state_dict(now)

    def reset_stats(self) -> None:
        self.sessions = []
        self.focus_completed = 0

    # -- tick ----------------------------------------------------------
    def tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Advance one second of wall-clock time. Called by a 1s loop."""
        now = now if now is not None else time.time()
        if not self.running or self.phase == PHASE_IDLE:
            return self.state_dict(now)
        if self.phase == PHASE_FOCUS and self.settings["agent_aware"]:
            if not self.agent_active(now):
                # Agent idle: wait, do not count this second.
                return self.state_dict(now)
        self.elapsed_s += 1
        if self.elapsed_s >= self.phase_duration_s():
            self._complete_phase(now)
        return self.state_dict(now)

    # -- transitions ---------------------------------------------------
    def _complete_phase(self, now: float) -> None:
        if self.phase == PHASE_FOCUS:
            self._log_session(now, completed=True)
            self.focus_completed += 1
            if self.focus_completed % int(self.settings["sessions_before_long_break"]) == 0:
                self.phase = PHASE_LONG_BREAK
            else:
                self.phase = PHASE_SHORT_BREAK
            self.elapsed_s = 0
            self.phase_started_at = now
            self.running = bool(self.settings["auto_start_breaks"])
        else:  # a break completed
            self.phase = PHASE_IDLE
            self.elapsed_s = 0
            self.phase_started_at = None
            self.running = bool(self.settings["auto_start_focus"])
            if self.running:
                self.phase = PHASE_FOCUS

    def _advance_after_break_or_skip(self, now: float) -> None:
        if self.phase == PHASE_FOCUS:
            self.phase = PHASE_SHORT_BREAK
            self.elapsed_s = 0
            self.phase_started_at = now
            self.running = bool(self.settings["auto_start_breaks"])
        elif self.phase in (PHASE_SHORT_BREAK, PHASE_LONG_BREAK):
            self.phase = PHASE_IDLE
            self.elapsed_s = 0
            self.phase_started_at = None
            self.running = False
        else:
            self.phase = PHASE_IDLE
            self.running = False
            self.elapsed_s = 0
            self.phase_started_at = None

    def _log_session(self, now: float, completed: bool) -> None:
        started = self.phase_started_at if self.phase_started_at is not None else now
        self.sessions.append(
            {
                "started_at": started,
                "ended_at": now,
                "duration_s": int(self.elapsed_s),
                "completed": bool(completed),
                "mode": "agent-aware"
                if self.settings["agent_aware"]
                else "manual",
            }
        )

    # -- views ---------------------------------------------------------
    def remaining_s(self) -> int:
        return max(0, self.phase_duration_s() - self.elapsed_s)

    def state_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = now if now is not None else time.time()
        return {
            "phase": self.phase,
            "running": self.running,
            "waiting_for_agent": self.is_waiting_for_agent(now),
            "agent_active": self.agent_active(now),
            "agent_aware": bool(self.settings["agent_aware"]),
            "elapsed_s": int(self.elapsed_s),
            "remaining_s": int(self.remaining_s()),
            "duration_s": int(self.phase_duration_s()),
            "focus_completed": int(self.focus_completed),
            "sessions_before_long_break": int(
                self.settings["sessions_before_long_break"]
            ),
            "settings": dict(self.settings),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "running": self.running,
            "elapsed_s": int(self.elapsed_s),
            "focus_completed": int(self.focus_completed),
            "last_activity_at": self.last_activity_at,
            "phase_started_at": self.phase_started_at,
            "settings": dict(self.settings),
            "sessions": list(self.sessions),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FocusTimer":
        timer = cls(data.get("settings"))
        timer.phase = str(data.get("phase", PHASE_IDLE))
        timer.running = bool(data.get("running", False))
        timer.elapsed_s = int(data.get("elapsed_s", 0))
        timer.focus_completed = int(data.get("focus_completed", 0))
        timer.last_activity_at = data.get("last_activity_at")
        timer.phase_started_at = data.get("phase_started_at")
        timer.sessions = list(data.get("sessions", []))
        return timer


def compute_stats(
    sessions: List[Dict[str, Any]], now: Optional[float] = None
) -> Dict[str, Any]:
    """Today / this-week totals and the current focus streak (in days).

    Only completed focus sessions count. The streak counts consecutive
    days with at least one completed session, looking back from today;
    a missing today still counts if yesterday had one (the day is not
    over yet). All dates are local dates.
    """
    now = now if now is not None else time.time()
    local_now = time.localtime(now)
    today = time.strftime("%Y-%m-%d", local_now)
    week_ago = now - 7 * 24 * 3600

    completed = [s for s in sessions if s.get("completed")]
    by_day: Dict[str, int] = {}
    for session in completed:
        day = time.strftime("%Y-%m-%d", time.localtime(session["ended_at"]))
        by_day[day] = by_day.get(day, 0) + 1

    today_sessions = by_day.get(today, 0)
    today_minutes = sum(
        int(s["duration_s"]) // 60
        for s in completed
        if time.strftime("%Y-%m-%d", time.localtime(s["ended_at"])) == today
    )
    week_sessions = sum(1 for s in completed if s["ended_at"] >= week_ago)
    week_minutes = sum(
        int(s["duration_s"]) // 60 for s in completed if s["ended_at"] >= week_ago
    )

    streak = 0
    cursor = now
    # If today has no session yet, the streak may still run through
    # yesterday — start counting from yesterday in that case.
    if by_day.get(today, 0) == 0:
        cursor -= 24 * 3600
    while True:
        day = time.strftime("%Y-%m-%d", time.localtime(cursor))
        if by_day.get(day, 0) > 0:
            streak += 1
            cursor -= 24 * 3600
        else:
            break

    return {
        "today_sessions": today_sessions,
        "today_minutes": today_minutes,
        "week_sessions": week_sessions,
        "week_minutes": week_minutes,
        "streak_days": streak,
        "total_sessions": len(completed),
    }


DEFAULT_SETTINGS: Dict[str, Any] = {
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

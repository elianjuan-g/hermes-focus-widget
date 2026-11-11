"""Unit tests for the FocusTimer state machine.

The timer is clock-injectable: every test drives a fake ``now`` forward
explicitly, so no test ever sleeps.
"""

import unittest

from hermes_focus_widget.timer import FocusTimer, compute_stats


def settings(**overrides):
    base = {
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
    base.update(overrides)
    return base


class TestPhases(unittest.TestCase):
    def test_focus_completes_into_short_break(self):
        t = FocusTimer(settings(focus_minutes=1, agent_aware=False))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        self.assertEqual(t.focus_completed, 1)
        self.assertEqual(len(t.sessions), 1)
        self.assertTrue(t.sessions[0]["completed"])
        self.assertEqual(t.sessions[0]["duration_s"], 60)

    def test_fourth_focus_goes_to_long_break(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                agent_aware=False))
        now = 1000
        for cycle in range(4):
            t.start(now=now)
            for i in range(60):
                t.tick(now=now + i + 1)
            now += 61
            expected = "long_break" if cycle == 3 else "short_break"
            self.assertEqual(t.phase, expected, f"cycle {cycle}")
            t.skip(now=now)
            now += 1
        self.assertEqual(t.focus_completed, 4)

    def test_break_completes_back_to_idle(self):
        # auto_start_breaks must be on: a finished focus leaves the
        # timer stopped, so the break would never tick otherwise.
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                agent_aware=False, auto_start_breaks=True))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        self.assertTrue(t.running)
        for i in range(60):
            t.tick(now=1061 + i + 1)
        self.assertEqual(t.phase, "idle")
        self.assertFalse(t.running)

    def test_pause_and_resume(self):
        t = FocusTimer(settings(focus_minutes=25, agent_aware=False))
        t.start(now=1000)
        for i in range(10):
            t.tick(now=1000 + i + 1)
        t.pause(now=1011)
        for i in range(10):
            t.tick(now=1011 + i + 1)
        self.assertEqual(t.elapsed_s, 10)  # paused: no progress
        t.resume(now=1021)
        for i in range(5):
            t.tick(now=1021 + i + 1)
        self.assertEqual(t.elapsed_s, 15)

    def test_skip_focus_logs_abandoned_session(self):
        t = FocusTimer(settings(agent_aware=False))
        t.start(now=1000)
        for i in range(90):
            t.tick(now=1000 + i + 1)
        t.skip(now=1091)
        self.assertEqual(t.phase, "short_break")
        self.assertEqual(len(t.sessions), 1)
        self.assertFalse(t.sessions[0]["completed"])
        self.assertEqual(t.sessions[0]["duration_s"], 90)

    def test_skip_short_focus_logs_nothing(self):
        t = FocusTimer(settings(agent_aware=False))
        t.start(now=1000)
        for i in range(30):
            t.tick(now=1000 + i + 1)
        t.skip(now=1031)
        self.assertEqual(len(t.sessions), 0)

    def test_reset_keeps_log(self):
        t = FocusTimer(settings(focus_minutes=1, agent_aware=False))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(len(t.sessions), 1)
        t.reset(now=2000)
        self.assertEqual(t.phase, "idle")
        self.assertEqual(len(t.sessions), 1)


class TestAgentAware(unittest.TestCase):
    def test_timer_only_counts_while_agent_active(self):
        t = FocusTimer(settings(focus_minutes=5, idle_threshold_seconds=120))
        t.start(now=1000)
        t.mark_activity(now=1000)
        for i in range(30):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.elapsed_s, 30)
        # Agent goes silent: 120s threshold passes at now=1150.
        for i in range(200):
            t.tick(now=1030 + i + 1)
        # Last counted second: activity at 1000 + 120s threshold -> 1120.
        self.assertEqual(t.elapsed_s, 120)
        self.assertTrue(t.is_waiting_for_agent(now=1230))
        # Agent returns: timer resumes automatically.
        t.mark_activity(now=1300)
        for i in range(10):
            t.tick(now=1300 + i + 1)
        self.assertEqual(t.elapsed_s, 130)

    def test_no_activity_means_no_progress(self):
        t = FocusTimer(settings(focus_minutes=1, idle_threshold_seconds=60))
        t.start(now=1000)
        for i in range(300):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.elapsed_s, 0)
        self.assertTrue(t.is_waiting_for_agent(now=1300))

    def test_manual_mode_ignores_agent(self):
        t = FocusTimer(settings(focus_minutes=1, agent_aware=False))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        self.assertFalse(t.is_waiting_for_agent(now=1060))

    def test_long_idle_then_activity_still_completes(self):
        t = FocusTimer(settings(focus_minutes=2, idle_threshold_seconds=60))
        t.start(now=1000)
        t.mark_activity(now=1000)
        for i in range(50):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.elapsed_s, 50)
        # 10 minutes of silence: only the 10 seconds up to the
        # 60s activity threshold count (1051..1060).
        for i in range(600):
            t.tick(now=1050 + i + 1)
        self.assertEqual(t.elapsed_s, 60)
        self.assertEqual(t.phase, "focus")
        self.assertTrue(t.is_waiting_for_agent(now=1650))
        t.mark_activity(now=2000)
        for i in range(60):
            t.tick(now=2000 + i + 1)
        self.assertEqual(t.phase, "short_break")

    def test_agent_active_window(self):
        t = FocusTimer(settings(idle_threshold_seconds=120))
        self.assertFalse(t.agent_active(now=1000))
        t.mark_activity(now=1000)
        self.assertTrue(t.agent_active(now=1120))
        self.assertFalse(t.agent_active(now=1121))


class TestStats(unittest.TestCase):
    def test_today_week_streak(self):
        # now = a fixed local noon.
        import time as _time
        now = _time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))
        day = 24 * 3600
        sessions = [
            {"started_at": now - 3600, "ended_at": now - 1800,
             "duration_s": 1500, "completed": True, "mode": "agent-aware"},
            {"started_at": now - day - 3600, "ended_at": now - day - 1800,
             "duration_s": 1500, "completed": True, "mode": "agent-aware"},
            {"started_at": now - 2 * day - 3600, "ended_at": now - 2 * day - 1800,
             "duration_s": 1500, "completed": True, "mode": "agent-aware"},
            {"started_at": now - 3 * day, "ended_at": now - 3 * day + 600,
             "duration_s": 600, "completed": False, "mode": "manual"},
            {"started_at": now - 6 * day, "ended_at": now - 6 * day + 1500,
             "duration_s": 1500, "completed": True, "mode": "manual"},
        ]
        stats = compute_stats(sessions, now)
        self.assertEqual(stats["today_sessions"], 1)
        self.assertEqual(stats["today_minutes"], 25)
        self.assertEqual(stats["week_sessions"], 4)
        self.assertEqual(stats["week_minutes"], 100)
        self.assertEqual(stats["streak_days"], 3)
        self.assertEqual(stats["total_sessions"], 4)

    def test_streak_survives_missing_today(self):
        import time as _time
        now = _time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))
        day = 24 * 3600
        sessions = [
            {"started_at": now - day - 3600, "ended_at": now - day - 1800,
             "duration_s": 1500, "completed": True, "mode": "agent-aware"},
            {"started_at": now - 2 * day - 3600, "ended_at": now - 2 * day - 1800,
             "duration_s": 1500, "completed": True, "mode": "agent-aware"},
        ]
        stats = compute_stats(sessions, now)
        self.assertEqual(stats["streak_days"], 2)
        self.assertEqual(stats["today_sessions"], 0)

    def test_empty_stats(self):
        stats = compute_stats([], 1700000000)
        self.assertEqual(stats["today_sessions"], 0)
        self.assertEqual(stats["streak_days"], 0)


class TestEdges(unittest.TestCase):
    def test_repeated_pause_resume_cycles(self):
        t = FocusTimer(settings(focus_minutes=25, agent_aware=False))
        t.start(now=1000)
        now = 1000
        for _ in range(3):
            for i in range(10):
                t.tick(now=now + i + 1)
            now += 10
            t.pause(now=now)
            for i in range(10):
                t.tick(now=now + i + 1)
            now += 10
            t.resume(now=now)
        self.assertEqual(t.elapsed_s, 30)  # only running seconds counted

    def test_reset_mid_focus_logs_abandoned(self):
        t = FocusTimer(settings(agent_aware=False))
        t.start(now=1000)
        for i in range(120):
            t.tick(now=1000 + i + 1)
        t.reset(now=1121)
        self.assertEqual(t.phase, "idle")
        self.assertEqual(len(t.sessions), 1)
        self.assertFalse(t.sessions[0]["completed"])

    def test_reset_mid_break_logs_nothing(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=5,
                                agent_aware=False, auto_start_breaks=True))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        t.reset(now=1100)
        self.assertEqual(t.phase, "idle")
        self.assertEqual(len(t.sessions), 1)  # only the completed focus

    def test_skip_break_goes_idle(self):
        t = FocusTimer(settings(focus_minutes=1, agent_aware=False))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        t.skip(now=1061)
        self.assertEqual(t.phase, "idle")
        self.assertFalse(t.running)

    def test_auto_start_breaks(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                agent_aware=False, auto_start_breaks=True))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        self.assertTrue(t.running)
        for i in range(60):
            t.tick(now=1061 + i + 1)
        self.assertEqual(t.phase, "idle")

    def test_auto_start_focus_after_break(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                agent_aware=False, auto_start_breaks=True,
                                auto_start_focus=True))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.phase, "short_break")
        for i in range(60):
            t.tick(now=1061 + i + 1)
        self.assertEqual(t.phase, "focus")
        self.assertTrue(t.running)

    def test_zero_and_negative_durations_rejected(self):
        t = FocusTimer(settings())
        t.update_settings({"focus_minutes": 0, "short_break_minutes": -5,
                           "long_break_minutes": 0,
                           "sessions_before_long_break": 0,
                           "idle_threshold_seconds": -30})
        self.assertEqual(t.settings["focus_minutes"], 1)
        self.assertEqual(t.settings["short_break_minutes"], 1)
        self.assertEqual(t.settings["long_break_minutes"], 1)
        self.assertEqual(t.settings["sessions_before_long_break"], 2)
        self.assertEqual(t.settings["idle_threshold_seconds"], 10)

    def test_oversized_durations_clamped(self):
        t = FocusTimer(settings())
        t.update_settings({"focus_minutes": 9999, "idle_threshold_seconds": 99999})
        self.assertEqual(t.settings["focus_minutes"], 180)
        self.assertEqual(t.settings["idle_threshold_seconds"], 1800)

    def test_unparseable_settings_ignored(self):
        t = FocusTimer(settings())
        t.update_settings({"focus_minutes": "not a number", "agent_aware": "yes"})
        self.assertEqual(t.settings["focus_minutes"], 25)
        self.assertTrue(t.settings["agent_aware"])  # bool("yes") is True

    def test_idle_threshold_boundary(self):
        t = FocusTimer(settings(idle_threshold_seconds=120))
        t.mark_activity(now=1000)
        self.assertTrue(t.agent_active(now=1120))   # exactly at threshold
        self.assertFalse(t.agent_active(now=1121))  # one second past

    def test_start_is_idempotent(self):
        t = FocusTimer(settings(agent_aware=False))
        t.start(now=1000)
        for i in range(10):
            t.tick(now=1000 + i + 1)
        t.start(now=1011)  # must not reset elapsed
        self.assertEqual(t.elapsed_s, 10)
        self.assertEqual(t.phase, "focus")

    def test_controls_are_noops_when_idle(self):
        t = FocusTimer(settings())
        t.pause(now=1000)
        t.resume(now=1000)
        t.skip(now=1000)
        self.assertEqual(t.phase, "idle")
        self.assertFalse(t.running)
        self.assertEqual(t.elapsed_s, 0)

    def test_duration_change_mid_phase(self):
        t = FocusTimer(settings(focus_minutes=25, agent_aware=False))
        t.start(now=1000)
        for i in range(60):
            t.tick(now=1000 + i + 1)
        self.assertEqual(t.remaining_s(), 24 * 60)
        t.update_settings({"focus_minutes": 2})
        self.assertEqual(t.remaining_s(), 60)  # 120s total - 60s elapsed

    def test_waiting_flag_transitions(self):
        t = FocusTimer(settings(focus_minutes=5, idle_threshold_seconds=60))
        t.start(now=1000)
        self.assertTrue(t.is_waiting_for_agent(now=1000))  # no activity yet
        t.mark_activity(now=1000)
        self.assertFalse(t.is_waiting_for_agent(now=1000))
        for i in range(61):
            t.tick(now=1000 + i + 1)
        self.assertTrue(t.is_waiting_for_agent(now=1061))
        t.mark_activity(now=1100)
        self.assertFalse(t.is_waiting_for_agent(now=1100))

    def test_fifth_focus_returns_to_short_break(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                agent_aware=False))
        now = 1000
        for cycle in range(5):
            t.start(now=now)
            for i in range(60):
                t.tick(now=now + i + 1)
            now += 61
            expected = "long_break" if cycle == 3 else "short_break"
            self.assertEqual(t.phase, expected, f"cycle {cycle}")
            t.skip(now=now)
            now += 1
        self.assertEqual(t.focus_completed, 5)

    def test_long_break_duration_respected(self):
        t = FocusTimer(settings(focus_minutes=1, short_break_minutes=1,
                                long_break_minutes=2, agent_aware=False,
                                auto_start_breaks=True))
        now = 1000
        for cycle in range(4):
            t.start(now=now)
            for i in range(60):
                t.tick(now=now + i + 1)
            now += 61
            if cycle < 3:
                t.skip(now=now)
                now += 1
        self.assertEqual(t.phase, "long_break")
        self.assertEqual(t.phase_duration_s(), 120)

    def test_restart_mid_session_restores_running_timer(self):
        t = FocusTimer(settings(focus_minutes=25, agent_aware=False))
        t.start(now=1000)
        for i in range(300):
            t.tick(now=1000 + i + 1)
        t.mark_activity(now=1300)
        data = t.to_dict()
        t2 = FocusTimer.from_dict(data)
        self.assertEqual(t2.phase, "focus")
        self.assertTrue(t2.running)
        self.assertEqual(t2.elapsed_s, 300)
        self.assertEqual(t2.last_activity_at, 1300)
        # The restored timer keeps counting from where it stopped.
        for i in range(60):
            t2.tick(now=1300 + i + 1)
        self.assertEqual(t2.elapsed_s, 360)


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        t = FocusTimer(settings(focus_minutes=1, agent_aware=False))
        t.start(now=1000)
        for i in range(30):
            t.tick(now=1000 + i + 1)
        t.mark_activity(now=1030)
        data = t.to_dict()
        t2 = FocusTimer.from_dict(data)
        self.assertEqual(t2.phase, "focus")
        self.assertEqual(t2.elapsed_s, 30)
        self.assertEqual(t2.last_activity_at, 1030)
        self.assertEqual(t2.settings["focus_minutes"], 1)

    def test_settings_update_ignores_unknown_keys(self):
        t = FocusTimer(settings())
        t.update_settings({"focus_minutes": 50, "not_a_setting": 1})
        self.assertEqual(t.settings["focus_minutes"], 50)
        self.assertNotIn("not_a_setting", t.settings)


if __name__ == "__main__":
    unittest.main()

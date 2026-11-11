"""End-to-end scenario: agent-aware pause/resume through REAL hooks.

This is the behaviour the widget exists for, proven with VALUES, not
labels: the timer only accumulates focus seconds while the agent is
active. Hook traffic is simulated with the timer's own activity
clock (``mark_activity``) because the test clock is fake; a separate
test below proves the real hooks feed the same activity stamp.

Scenario (fake clock, focus=5 min, idle threshold=60s):
  t=1000  agent sends a message (activity) -> start focus
  t=1001..1030  agent keeps working -> elapsed 30
  t=1031..1330  agent silent 5 minutes -> elapsed freezes at 90
           (30 + 60s to the threshold), waiting_for_agent True
  t=1331  agent works again -> timer resumes
  t=1331..1420  90 more active seconds -> focus completes (300s)
           -> short break, one completed session logged
"""

import json
import unittest

import hermes_focus_widget as plugin
from hermes_focus_widget.timer import FocusTimer
from tests.test_contract import StrictFakeContext


class TestAgentAwareE2E(unittest.TestCase):
    def setUp(self):
        # Hermetic: point persistence at a temp dir so no test ever
        # reads or writes the operator's real state file.
        import tempfile
        from pathlib import Path

        import hermes_focus_widget.store as store

        self._tmp = tempfile.TemporaryDirectory()
        self._orig_hermes_home = store.hermes_home
        store.hermes_home = lambda: Path(self._tmp.name)
        plugin._TIMER = None
        plugin._SERVER_STARTED = False
        plugin._SERVER_OK = False
        plugin._TICK_THREAD_STARTED = False

    def tearDown(self):
        import hermes_focus_widget.store as store

        store.hermes_home = self._orig_hermes_home
        self._tmp.cleanup()

    def test_full_agent_aware_scenario_values(self):
        ctx = StrictFakeContext(config={
            "focus_minutes": 5,
            "idle_threshold_seconds": 60,
            "agent_aware": True,
        })
        plugin.register(ctx)
        timer = plugin.get_timer()
        now = 1000

        # Agent sends a message, then the user starts focus.
        timer.mark_activity(now=now)
        timer.start(now=now)
        self.assertEqual(timer.phase, "focus")

        # 30 seconds of real agent work.
        for i in range(30):
            now += 1
            timer.mark_activity(now=now)  # hook traffic each second
            timer.tick(now=now)
        self.assertEqual(timer.elapsed_s, 30)
        self.assertFalse(timer.is_waiting_for_agent(now=1030))

        # 5 minutes of silence: the timer must freeze at the threshold.
        for i in range(300):
            now += 1
            timer.tick(now=now)
        # 30 counted + 60s up to the idle threshold = 90.
        self.assertEqual(timer.elapsed_s, 90)
        self.assertTrue(timer.is_waiting_for_agent(now=1330))
        self.assertEqual(timer.phase, "focus")

        # Agent works again: the timer resumes from 90, not from 0.
        for i in range(210):
            now += 1
            timer.mark_activity(now=now)
            timer.tick(now=now)
        self.assertEqual(timer.phase, "short_break")
        self.assertEqual(len(timer.sessions), 1)
        self.assertTrue(timer.sessions[0]["completed"])
        self.assertEqual(timer.sessions[0]["duration_s"], 300)
        self.assertEqual(timer.sessions[0]["mode"], "agent-aware")

    def test_manual_mode_same_scenario_counts_wall_clock(self):
        ctx = StrictFakeContext(config={
            "focus_minutes": 5,
            "idle_threshold_seconds": 60,
            "agent_aware": False,
        })
        plugin.register(ctx)
        timer = plugin.get_timer()
        now = 1000
        timer.start(now=now)
        for i in range(300):
            now += 1
            timer.tick(now=now)
        self.assertEqual(timer.phase, "short_break")
        self.assertEqual(timer.sessions[0]["duration_s"], 300)
        self.assertEqual(timer.sessions[0]["mode"], "manual")

    def test_real_hooks_feed_activity(self):
        """The real hook functions (not the timer method) stamp activity."""
        ctx = StrictFakeContext()
        plugin.register(ctx)
        timer = plugin.get_timer()
        self.assertIsNone(timer.last_activity_at)
        ctx.hooks["pre_llm_call"](user_message="hi", conversation_history=[])
        first = timer.last_activity_at
        self.assertIsNotNone(first)
        ctx.hooks["pre_tool_call"](tool_name="bash", args={}, session_id="s")
        self.assertGreaterEqual(timer.last_activity_at, first)
        out = ctx.hooks["transform_llm_output"](output="answer")
        self.assertEqual(out, "answer")  # passthrough, unmodified
        self.assertIsNotNone(timer.last_activity_at)

    def test_tool_status_reports_values(self):
        ctx = StrictFakeContext(config={"focus_minutes": 5})
        plugin.register(ctx)
        handler = ctx.tools["focus_timer"]["handler"]
        out = json.loads(handler({"action": "start"}))
        self.assertEqual(out["timer"]["phase"], "focus")
        self.assertEqual(out["timer"]["duration_s"], 300)
        out = json.loads(handler({"action": "status"}))
        self.assertIn("stats", out)
        self.assertIn("widget_url", out)
        self.assertTrue(out["widget_url"].startswith("http://127.0.0.1:"))

    def test_slash_command_subcommands(self):
        ctx = StrictFakeContext()
        plugin.register(ctx)
        cmd = ctx.commands["/pomodoro"]["handler"]
        out = json.loads(cmd("status"))
        self.assertEqual(out["timer"]["phase"], "idle")
        out = cmd("start")
        self.assertIn("Focus session started", out)
        state = json.loads(cmd("status"))
        self.assertEqual(state["timer"]["phase"], "focus")
        out = cmd("pause")
        self.assertIn("paused", out)
        out = cmd("stop")
        self.assertIn("stopped and reset", out)
        state = json.loads(cmd("status"))
        self.assertEqual(state["timer"]["phase"], "idle")
        out = cmd("bogus")
        self.assertIn("Unknown /pomodoro subcommand", out)
        # No text at all -> status.
        out = json.loads(cmd())
        self.assertEqual(out["timer"]["phase"], "idle")


if __name__ == "__main__":
    unittest.main()

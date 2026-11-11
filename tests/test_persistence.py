"""Persistence tests: state survives a gateway restart.

The store writes atomically (temp file + rename) under the Hermes
plugin-data directory. These tests point the store at a temp dir,
save a mid-session timer, then load it back exactly as a fresh
gateway process would — and assert every value survives.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import hermes_focus_widget.store as store
from hermes_focus_widget.timer import FocusTimer


class TestStoreRestart(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = store.hermes_home
        store.hermes_home = lambda: Path(self._tmp.name)

    def tearDown(self):
        store.hermes_home = self._orig
        self._tmp.cleanup()

    def test_save_then_load_mid_session(self):
        # Manual mode here: with agent-aware on, ticks without hook
        # traffic would not count (that behaviour is covered in
        # test_timer.TestAgentAware).
        timer = FocusTimer({"focus_minutes": 25, "agent_aware": False})
        timer.start(now=1000)
        for i in range(300):
            timer.tick(now=1000 + i + 1)
        timer.mark_activity(now=1300)
        timer.sessions.append({
            "started_at": 500, "ended_at": 900, "duration_s": 400,
            "completed": True, "mode": "agent-aware",
        })
        store.save_state(timer.to_dict())

        # Simulate a gateway restart: fresh process, fresh timer.
        loaded = store.load_state()
        timer2 = FocusTimer.from_dict(loaded)
        self.assertEqual(timer2.phase, "focus")
        self.assertTrue(timer2.running)
        self.assertEqual(timer2.elapsed_s, 300)
        self.assertEqual(timer2.last_activity_at, 1300)
        self.assertEqual(len(timer2.sessions), 1)
        self.assertEqual(timer2.sessions[0]["duration_s"], 400)
        self.assertEqual(timer2.settings["focus_minutes"], 25)

    def test_write_is_atomic_no_tmp_left(self):
        timer = FocusTimer({"focus_minutes": 25})
        store.save_state(timer.to_dict())
        directory = store.plugin_data_dir()
        leftovers = [p for p in directory.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        raw = (directory / "focus-state.json").read_text(encoding="utf-8")
        json.loads(raw)  # must be valid JSON, never a torn write

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(store.load_state(), {})

    def test_load_corrupt_file_returns_empty(self):
        directory = store.plugin_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "focus-state.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(store.load_state(), {})

    def test_settings_survive_restart(self):
        timer = FocusTimer({"focus_minutes": 50, "agent_aware": False,
                            "short_break_minutes": 10})
        store.save_state(timer.to_dict())
        timer2 = FocusTimer.from_dict(store.load_state())
        self.assertEqual(timer2.settings["focus_minutes"], 50)
        self.assertFalse(timer2.settings["agent_aware"])
        self.assertEqual(timer2.settings["short_break_minutes"], 10)


if __name__ == "__main__":
    unittest.main()

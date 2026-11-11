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

import hermes_focus_widget as plugin_module
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

class TestCrossProcessActivity(unittest.TestCase):
    """Two plugin processes (gateway + Desktop serve backend) share one
    state file. A stamp in one must reach the other within a second,
    and a save must never drag the file's stamp backwards.

    Simulates the second process with a separate FocusTimer writing
    to the same temp-dir file, exactly as the serve backend does.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = store.hermes_home
        store.hermes_home = lambda: Path(self._tmp.name)
        self._saved_timer = plugin_module._TIMER
        plugin_module._TIMER = None

    def tearDown(self):
        plugin_module._TIMER = self._saved_timer
        store.hermes_home = self._orig
        self._tmp.cleanup()

    def test_note_activity_persists_to_disk_immediately(self):
        timer = FocusTimer({"focus_minutes": 25})
        plugin_module._TIMER = timer
        self.assertIsNone(store.load_state().get("last_activity_at"))
        plugin_module._note_activity()
        disk_last = store.load_state().get("last_activity_at")
        self.assertIsNotNone(disk_last)
        self.assertEqual(disk_last, timer.last_activity_at)

    def test_merge_picks_up_other_process_stamp(self):
        # "Serve backend" process stamps and persists.
        other = FocusTimer({"focus_minutes": 25})
        other.mark_activity(now=2000)
        store.save_state(other.to_dict())
        # "Gateway" process has an older stamp in memory.
        timer = FocusTimer({"focus_minutes": 25})
        timer.mark_activity(now=1000)
        self.assertFalse(timer.agent_active(now=2000 + 10))
        plugin_module._merge_disk_activity(timer)
        self.assertEqual(timer.last_activity_at, 2000)
        self.assertTrue(timer.agent_active(now=2000 + 10))

    def test_merge_never_regresses(self):
        other = FocusTimer({"focus_minutes": 25})
        other.mark_activity(now=1500)
        store.save_state(other.to_dict())
        timer = FocusTimer({"focus_minutes": 25})
        timer.mark_activity(now=2000)
        plugin_module._merge_disk_activity(timer)
        self.assertEqual(timer.last_activity_at, 2000)

    def test_merge_ignores_missing_or_bad_disk_value(self):
        timer = FocusTimer({"focus_minutes": 25})
        timer.mark_activity(now=1000)
        plugin_module._merge_disk_activity(timer)  # no file at all
        self.assertEqual(timer.last_activity_at, 1000)
        store.save_state({"last_activity_at": "not-a-number"})
        plugin_module._merge_disk_activity(timer)
        self.assertEqual(timer.last_activity_at, 1000)

    def test_persist_never_regresses_file(self):
        # The other process stamped 3000 to disk; this process only
        # knows 1000. Saving must keep 3000, not drag it back.
        other = FocusTimer({"focus_minutes": 25})
        other.mark_activity(now=3000)
        store.save_state(other.to_dict())
        timer = FocusTimer({"focus_minutes": 25})
        timer.mark_activity(now=1000)
        plugin_module._TIMER = timer
        plugin_module._persist()
        self.assertEqual(store.load_state()["last_activity_at"], 3000)
        self.assertEqual(timer.last_activity_at, 3000)


if __name__ == "__main__":
    unittest.main()

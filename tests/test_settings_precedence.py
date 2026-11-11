"""Regression tests for the settings-precedence bug (found live 12 Nov 2026).

A plugin reload used to apply the plugin.yaml config defaults OVER the
disk state, silently reverting the user's settings-page choices — e.g.
agent_aware back to True, freezing a manual-mode timer in "waiting for
agent" with the elapsed time stuck. Disk state is now the source of
truth; config only seeds a first run with no disk state.

Also guards the minimal cloud UI: no emoji icons, one calm accent,
a progress ring (not a bar), icon-only controls with labels.
"""

import unittest

import hermes_focus_widget as plugin_module
from hermes_focus_widget.ui import HTML_PAGE


class TestSettingsPrecedence(unittest.TestCase):
    def setUp(self):
        self._saved_timer = plugin_module._TIMER
        self._saved_load = plugin_module.load_state
        plugin_module._TIMER = None

    def tearDown(self):
        plugin_module._TIMER = self._saved_timer
        plugin_module.load_state = self._saved_load

    def test_disk_settings_survive_reload_with_defaults(self):
        """Reload with config defaults must NOT revert disk settings."""
        disk = {
            "phase": "focus",
            "running": True,
            "elapsed_s": 20,
            "focus_completed": 0,
            "settings": {
                "focus_minutes": 25,
                "short_break_minutes": 5,
                "long_break_minutes": 15,
                "sessions_before_long_break": 4,
                "agent_aware": False,  # user chose manual mode
                "idle_threshold_seconds": 120,
                "auto_start_breaks": False,
                "auto_start_focus": False,
                "sound_enabled": True,
                "hud_port": 18778,
                "auto_launch_server": True,
            },
            "sessions": [],
        }
        plugin_module.load_state = lambda: disk
        timer = plugin_module.get_timer(plugin_module._default_plugin_config())
        self.assertIs(False, timer.settings["agent_aware"])
        self.assertEqual("focus", timer.phase)
        self.assertEqual(20, timer.elapsed_s)
        self.assertIs(True, timer.running)

    def test_first_run_applies_config_defaults(self):
        """With no disk state, config seeds the timer (agent-aware on)."""
        plugin_module.load_state = lambda: {}
        timer = plugin_module.get_timer(plugin_module._default_plugin_config())
        self.assertIs(True, timer.settings["agent_aware"])
        self.assertEqual(25, timer.settings["focus_minutes"])

    def test_second_get_timer_call_keeps_singleton(self):
        plugin_module.load_state = lambda: {}
        first = plugin_module.get_timer({"agent_aware": False})
        second = plugin_module.get_timer({"agent_aware": True})
        self.assertIs(first, second)
        self.assertIs(False, second.settings["agent_aware"])


class TestMinimalCloudUI(unittest.TestCase):
    def test_no_emoji_icons(self):
        for emoji in ("🍅", "☕", "✨", "⏰", "🔔"):
            self.assertNotIn(emoji, HTML_PAGE, "emoji must not be an icon: " + emoji)

    def test_one_calm_accent(self):
        self.assertIn("#0F766E", HTML_PAGE)  # muted teal, the single accent
        for banned in ("linear-gradient", "#8B5CF6", "#6366F1", "glass"):
            self.assertNotIn(banned, HTML_PAGE)

    def test_ring_not_bar(self):
        self.assertIn("<svg", HTML_PAGE)
        self.assertIn('id="ring"', HTML_PAGE)
        self.assertNotIn('id="bar"', HTML_PAGE)

    def test_icon_only_controls_with_labels(self):
        for label in ('aria-label="Start focus"', 'aria-label="Skip phase"',
                      'aria-label="Reset timer"'):
            self.assertIn(label, HTML_PAGE)
        self.assertNotIn(">Start focus<", HTML_PAGE)
        self.assertNotIn(">Pause<", HTML_PAGE)

    def test_real_empty_microcopy(self):
        self.assertIn("No focus sessions yet today", HTML_PAGE)
        self.assertNotIn("No data", HTML_PAGE)
        self.assertNotIn("Boost your productivity", HTML_PAGE)

    def test_light_cloud_tokens(self):
        for token in ("#FFFFFF", "#F5F6F7", "#1F2937", "#6B7280", "#E5E7EB"):
            self.assertIn(token, HTML_PAGE)


if __name__ == "__main__":
    unittest.main()

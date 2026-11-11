"""Contract tests: register() against a strict fake 0.21.5 context.

The fake context mimics the real Hermes 0.21.5 signatures exactly:
register_tool(name, toolset, schema, handler) — positional-or-keyword,
no ``parameters=`` keyword. A wrong call must raise TypeError here, the
way real discovery would discard the plugin (Aegis v2.6.4 lesson).

Also covers: plugin.yaml validity, the loopback HTTP API end to end,
and the tool handler through the args-dict call shape the core uses
(handler(args, **kwargs)).
"""

import json
import os
import tempfile
import unittest
import urllib.request

import yaml  # type: ignore

import hermes_focus_widget as plugin
from hermes_focus_widget import timer as timer_mod


class StrictFakeContext:
    """Mimics the real 0.21.5 PluginContext surface used by register()."""

    def __init__(self, config=None):
        self._config = dict(config or {})
        self.hooks = {}
        self.tools = {}
        self.commands = {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_tool(self, name, toolset, schema, handler):
        # Exact 0.21.5 positional signature; a keyword `parameters=`
        # call would raise TypeError here, as on real discovery.
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
        }

    def register_command(self, name, handler, description=""):
        self.commands[name] = {"handler": handler, "description": description}


class TestRegisterContract(unittest.TestCase):
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

    def test_register_with_strict_context(self):
        ctx = StrictFakeContext()
        plugin.register(ctx)
        self.assertIn("focus_timer", ctx.tools)
        self.assertEqual(ctx.tools["focus_timer"]["toolset"], "hermes_focus_widget")
        schema = ctx.tools["focus_timer"]["schema"]
        self.assertEqual(schema["name"], "focus_timer")
        self.assertIn("status", schema["parameters"]["properties"]["action"]["enum"])
        for hook in ("on_session_start", "pre_llm_call", "pre_tool_call",
                     "transform_llm_output"):
            self.assertIn(hook, ctx.hooks)
        self.assertIn("/focus", ctx.commands)

    def test_register_reads_config_via_get_config(self):
        ctx = StrictFakeContext(config={"focus_minutes": 50, "agent_aware": False})
        plugin.register(ctx)
        timer = plugin.get_timer()
        self.assertEqual(timer.settings["focus_minutes"], 50)
        self.assertFalse(timer.settings["agent_aware"])

    def test_register_survives_mock_context_without_get_config(self):
        class BareContext:
            def register_hook(self, *a, **k):
                pass

            def register_tool(self, *a, **k):
                pass

            def register_command(self, *a, **k):
                pass

        plugin.register(BareContext())  # must not raise

    def test_tool_handler_args_dict_shape(self):
        ctx = StrictFakeContext()
        plugin.register(ctx)
        handler = ctx.tools["focus_timer"]["handler"]
        # Core calls handlers as handler(args_dict, **kwargs).
        out = json.loads(handler({"action": "status"}))
        self.assertEqual(out["timer"]["phase"], "idle")
        out = json.loads(handler({"action": "start"}))
        self.assertEqual(out["timer"]["phase"], "focus")
        out = json.loads(handler({"action": "stats"}))
        self.assertIn("stats", out)
        out = json.loads(handler({"action": "bogus"}))
        self.assertEqual(out["status"], "error")

    def test_hooks_record_activity(self):
        ctx = StrictFakeContext()
        plugin.register(ctx)
        timer = plugin.get_timer()
        self.assertIsNone(timer.last_activity_at)
        ctx.hooks["pre_llm_call"](user_message="hello", conversation_history=[])
        self.assertIsNotNone(timer.last_activity_at)
        ctx.hooks["transform_llm_output"](output="done")
        self.assertIsNotNone(timer.last_activity_at)


class TestPluginYaml(unittest.TestCase):
    def test_manifest_matches_code(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "plugin.yaml"), encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh)
        self.assertEqual(manifest["name"], "hermes-focus-widget")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["entrypoint"], "hermes_focus_widget:register")
        self.assertEqual(manifest["provides_tools"], ["focus_timer"])
        self.assertIn("pre_llm_call", manifest["provides_hooks"])
        for key in ("focus_minutes", "agent_aware", "hud_port",
                    "auto_launch_server"):
            self.assertIn(key, manifest["config_schema"])
        self.assertEqual(manifest["config_schema"]["hud_port"]["default"], 18778)


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Point persistence at a temp dir so tests never touch real data.
        cls._tmp = tempfile.TemporaryDirectory()
        timer_mod_home = os.environ.get("HOME", "")
        cls._orig_home = timer_mod_home
        import hermes_focus_widget.store as store

        cls._orig_hermes_home = store.hermes_home
        store.hermes_home = lambda: __import__("pathlib").Path(cls._tmp.name)
        plugin._TIMER = None
        plugin._SERVER_STARTED = False
        plugin._SERVER_OK = False
        plugin._TICK_THREAD_STARTED = False
        cls.port = 18799
        cls.url = plugin.ensure_widget_server_running(cls.port)
        import time

        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.url + "/api/state", timeout=1) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("widget server did not come up")

    @classmethod
    def tearDownClass(cls):
        import hermes_focus_widget.store as store

        store.hermes_home = cls._orig_hermes_home
        cls._tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def _post(self, path, payload):
        import urllib.error

        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:  # 4xx/5xx still carry a body
            return err.code, json.loads(err.read().decode("utf-8"))

    def test_state_endpoint_values(self):
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")
        self.assertIn("timer", data)
        self.assertIn("stats", data)
        self.assertIn("settings", data)
        self.assertEqual(data["timer"]["phase"], "idle")

    def test_page_serves_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Focus Widget", body)
        self.assertIn("/api/state", body)

    def test_control_start_pause_flow(self):
        _, data = self._post("/api/control", {"action": "start"})
        self.assertEqual(data["timer"]["phase"], "focus")
        self.assertTrue(data["timer"]["running"])
        _, data = self._post("/api/control", {"action": "pause"})
        self.assertFalse(data["timer"]["running"])
        _, data = self._post("/api/control", {"action": "reset"})
        self.assertEqual(data["timer"]["phase"], "idle")

    def test_settings_roundtrip_and_clamping(self):
        _, data = self._post("/api/settings", {"focus_minutes": 999})
        self.assertEqual(data["settings"]["focus_minutes"], 180)
        _, data = self._post("/api/settings", {"agent_aware": False})
        self.assertFalse(data["settings"]["agent_aware"])
        _, data = self._post("/api/settings", {"agent_aware": True})
        self.assertTrue(data["settings"]["agent_aware"])

    def test_unknown_action_is_400(self):
        status, _ = self._post("/api/control", {"action": "explode"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

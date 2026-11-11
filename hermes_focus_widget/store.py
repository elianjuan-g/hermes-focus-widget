"""Local JSON persistence for Hermes Focus Widget.

All state lives under the Hermes home directory (profile-aware when the
``hermes_constants`` module is importable, plain ``~/.hermes`` otherwise),
in ``plugin-data/hermes-focus-widget/``. Writes are atomic (temp file +
rename). No network, no credentials, no data leaves the machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

PLUGIN_KEY = "hermes-focus-widget"
STATE_FILENAME = "focus-state.json"


def hermes_home() -> Path:
    """Profile-aware Hermes home, with a plain-home fallback."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def plugin_data_dir() -> Path:
    return hermes_home() / "plugin-data" / PLUGIN_KEY


def state_path() -> Path:
    return plugin_data_dir() / STATE_FILENAME


def load_state() -> Dict[str, Any]:
    """Load saved timer state; {} when nothing was saved yet."""
    try:
        raw = state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_state(data: Dict[str, Any]) -> None:
    """Atomically persist timer state (best effort, never raises)."""
    try:
        directory = plugin_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / (STATE_FILENAME + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, state_path())
    except Exception:
        pass

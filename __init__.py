"""
Root-level plugin shim for Hermes 0.21.x & 0.23.x dual compatibility.
Ensures `hermes plugins validate` finds `__init__.py` at the plugin root
while also supporting `hermes_focus_widget:register` package entrypoint.
"""

from __future__ import annotations

from .hermes_focus_widget import (
    ensure_widget_server_running,
    get_timer,
    on_session_start_hook,
    post_api_request_hook,
    pre_llm_call_hook,
    pre_tool_call_hook,
    register,
    tool_focus_timer,
    transform_llm_output_hook,
)

__all__ = [
    "register",
    "get_timer",
    "ensure_widget_server_running",
    "on_session_start_hook",
    "post_api_request_hook",
    "pre_llm_call_hook",
    "pre_tool_call_hook",
    "transform_llm_output_hook",
    "tool_focus_timer",
]

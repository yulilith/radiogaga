"""Tests for hardware/input_controller.py — specifically the terminal raw mode fix."""

import tty
import inspect


def test_get_key_uses_setcbreak_not_setraw():
    """Verify _get_key uses tty.setcbreak (preserves output processing)
    instead of tty.setraw (breaks terminal log output formatting)."""
    from hardware.input_controller import InputController
    source = inspect.getsource(InputController._get_key)
    assert "setcbreak" in source, (
        "_get_key should use tty.setcbreak to preserve terminal output processing"
    )
    assert "setraw" not in source, (
        "_get_key must NOT use tty.setraw — it disables \\n -> \\r\\n mapping, "
        "causing log lines to drift rightward"
    )

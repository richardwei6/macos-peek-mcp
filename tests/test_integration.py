"""Integration tests against the real macOS AX API.

Skipped by default. Run with:

    uv run pytest -m integration tests/test_integration.py

Requirements:
- macOS host
- AX trust granted to the running interpreter (run `peek doctor` to set up)
"""

from __future__ import annotations

import pytest

from peek import ax, permissions, windows


pytestmark = pytest.mark.integration


def test_ax_get_window_symbol_binds():
    """The private SPI loaded at import time."""
    assert ax._GET_WINDOW is not None, "_AXUIElementGetWindow failed to bind"


def test_doctor_reports_trust_state():
    # Just that calling it doesn't crash; value depends on the host.
    _ = permissions.is_trusted()


def test_list_windows_returns_at_least_one_real_window():
    ws = windows.list_windows()
    assert len(ws) > 0
    # Sanity: every window has an int pid > 0
    assert all(w["pid"] > 0 for w in ws)


def test_read_focused_returns_text_when_trusted():
    if not permissions.is_trusted():
        pytest.skip("AX trust required")
    pid, win = ax.focused_window_ax()
    if win is None:
        pytest.skip("no focused window detectable")
    out = ax.walk_ax_tree(win, max_time_seconds=2.0)
    # We don't assert the body — accessibility trees vary — but the walk
    # must complete without raising.
    assert isinstance(out["text"], str)

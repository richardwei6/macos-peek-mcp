"""Tests for `peek.ax._select_window` (the pure-helper entry point that
`find_window_ax` delegates to)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from peek import ax


class FakeAXWindow:
    """Stand-in for an AXUIElementRef pointing at a window."""

    def __init__(self, title: str, win_id: int | None = None):
        self.title = title
        self.win_id = win_id

    def __repr__(self):
        return f"FakeAXWindow({self.title!r}, id={self.win_id})"


def _fake_ax_copy(element: Any, attr: str) -> Any:
    if attr == ax.AX_TITLE:
        return getattr(element, "title", None)
    return None


def _fake_get_window_id(element: Any) -> int | None:
    return getattr(element, "win_id", None)


def test_window_id_match_when_symbol_bound():
    a = FakeAXWindow("First", win_id=100)
    b = FakeAXWindow("Second", win_id=200)
    with patch.object(ax, "_GET_WINDOW", object()):  # truthy sentinel
        with patch.object(ax, "get_window_id_for_ax_window", _fake_get_window_id):
            with patch.object(ax, "_ax_copy", _fake_ax_copy):
                result = ax._select_window([a, b], window_id=200, title_match=None, case_sensitive=False)
    assert result.element is b
    assert result.error is None


def test_title_exact_wins_over_substring_when_symbol_unbound():
    a = FakeAXWindow("Console")
    b = FakeAXWindow("Console (system.log)")
    with patch.object(ax, "_GET_WINDOW", None):
        with patch.object(ax, "_ax_copy", _fake_ax_copy):
            result = ax._select_window([a, b], window_id=None, title_match="Console", case_sensitive=False)
    # Exact match on "Console" wins; substring match on "Console (system.log)" loses.
    assert result.element is a


def test_title_substring_case_insensitive_default():
    a = FakeAXWindow("My Terminal Window")
    with patch.object(ax, "_GET_WINDOW", None):
        with patch.object(ax, "_ax_copy", _fake_ax_copy):
            result = ax._select_window([a], window_id=None, title_match="terminal", case_sensitive=False)
    assert result.element is a


def test_no_match_returns_not_found():
    a = FakeAXWindow("Foo")
    with patch.object(ax, "_GET_WINDOW", None):
        with patch.object(ax, "_ax_copy", _fake_ax_copy):
            result = ax._select_window([a], window_id=None, title_match="Bar", case_sensitive=False)
    assert result.element is None
    assert result.error == "not_found"


def test_ambiguous_returns_candidates():
    a = FakeAXWindow("Same Title")
    b = FakeAXWindow("Same Title")
    with patch.object(ax, "_GET_WINDOW", None):
        with patch.object(ax, "_ax_copy", _fake_ax_copy):
            result = ax._select_window([a, b], window_id=None, title_match="Same Title", case_sensitive=False)
    assert result.element is None
    assert result.error == "ambiguous_window"
    assert len(result.candidates) == 2

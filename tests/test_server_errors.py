"""Tests for `peek.server.read_window_impl` error envelopes."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from peek import ax, denylist, server
from peek.windows import Window


def _make_window(
    *,
    window_id: int = 1,
    pid: int = 100,
    app: str = "TestApp",
    bundle_id: str | None = "com.test.app",
    title: str = "Test Window",
    sensitive: bool = False,
    focused: bool = False,
) -> Window:
    return {
        "window_id": window_id,
        "pid": pid,
        "app": app,
        "bundle_id": bundle_id,
        "title": title,
        "on_screen": True,
        "focused": focused,
        "bounds": (0, 0, 100, 100),
        "sensitive": sensitive,
    }


@pytest.fixture(autouse=True)
def _reset_denylist(monkeypatch):
    """Force a known empty denylist for each test (no real ~/.config touch)."""
    monkeypatch.setattr(server, "_DENYLIST", denylist.Denylist.empty())


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setattr(server.permissions, "is_trusted", lambda: True)


async def test_missing_selector_returns_error():
    out = await server.read_window_impl(
        window_id=None,
        app=None,
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out["error"] == server.ERR_MISSING_SELECTOR


async def test_ambiguous_selector_returns_error():
    out = await server.read_window_impl(
        window_id=1,
        app="App",
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out["error"] == server.ERR_AMBIGUOUS_SELECTOR


async def test_invalid_regex_returns_error(trusted):
    out = await server.read_window_impl(
        window_id=None,
        app="App",
        title_match=None,
        focused=False,
        contains=None,
        grep="(unclosed",
        regex=True,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out["error"] == server.ERR_INVALID_REGEX
    assert "pattern" in out


async def test_ax_permission_denied(monkeypatch):
    monkeypatch.setattr(server.permissions, "is_trusted", lambda: False)
    out = await server.read_window_impl(
        window_id=None,
        app="App",
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out["error"] == server.ERR_AX_PERMISSION_DENIED
    assert "instructions" in out


async def test_ambiguous_window_when_multiple_app_matches(trusted, monkeypatch):
    win_a = _make_window(window_id=1, app="Console", title="A")
    win_b = _make_window(window_id=2, app="Console", title="B")
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [win_a, win_b])

    out = await server.read_window_impl(
        window_id=None,
        app="Console",
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out["error"] == server.ERR_AMBIGUOUS_WINDOW
    assert len(out["candidates"]) == 2


async def test_redacted_app(trusted, monkeypatch):
    sensitive_dl = denylist.Denylist(
        bundle_ids=frozenset({"com.1password.1password"}),
        app_name_patterns=("1Password",),
    )
    monkeypatch.setattr(server, "_DENYLIST", sensitive_dl)
    win = _make_window(
        bundle_id="com.1password.1password",
        app="1Password",
        sensitive=True,
    )
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [win])

    out = await server.read_window_impl(
        window_id=None,
        app="1Password",
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=False,
    )
    assert out.get("redacted_app") == "com.1password.1password"
    assert out.get("reason") == server.REDACTED_REASON


async def test_redacted_overrides_with_allow_sensitive(trusted, monkeypatch):
    sensitive_dl = denylist.Denylist(
        bundle_ids=frozenset({"com.1password.1password"}),
        app_name_patterns=("1Password",),
    )
    monkeypatch.setattr(server, "_DENYLIST", sensitive_dl)
    win = _make_window(
        bundle_id="com.1password.1password",
        app="1Password",
        sensitive=True,
    )
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [win])

    # When allow_sensitive=True, denylist gate is bypassed. We don't have a
    # real AX element to walk, so we mock the read step too.
    fake_walk = ax.WalkResult(text="secret", flags={}, elements_visited=1, errors=[])  # type: ignore[typeddict-item]
    monkeypatch.setattr(
        server,
        "_read_one_window_sync",
        lambda w, **kw: {
            "window": w,
            "text": f"<window_text>{fake_walk['text']}</window_text>",
            "raw_text": fake_walk["text"],
            "truncation": {},
            "errors": [],
        },
    )

    out = await server.read_window_impl(
        window_id=None,
        app="1Password",
        title_match=None,
        focused=False,
        contains=None,
        grep=None,
        regex=False,
        case_sensitive=False,
        context_lines=2,
        max_matches=50,
        max_chars=10_000,
        max_depth=10,
        max_elements=100,
        max_time_seconds=1.0,
        allow_sensitive=True,
    )
    assert "redacted_app" not in out
    assert "text" in out

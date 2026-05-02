"""Tests for the server-side grep filter and the `contains` selector."""

from __future__ import annotations

from typing import Any

import pytest

from peek import ax, denylist, server
from peek.windows import Window


def test_grep_literal_substring():
    text = "first line\nERROR: something broke\nthird line\n"
    matches, hit_cap = server._grep_lines(
        text, "ERROR", regex=False, case_sensitive=False, context_lines=0, max_matches=10
    )
    assert hit_cap is False
    assert len(matches) == 1
    assert matches[0]["line_number"] == 2
    assert matches[0]["line"] == "ERROR: something broke"


def test_grep_with_regex_capturing_group():
    text = "code 200 OK\ncode 500 ERR\ncode 404 NF\n"
    matches, _ = server._grep_lines(
        text, r"code\s+(\d+)", regex=True, case_sensitive=False, context_lines=0, max_matches=10
    )
    assert len(matches) == 3


def test_case_insensitive_default():
    text = "WARNING: heads up\nwarning: lowercase\n"
    matches, _ = server._grep_lines(
        text, "warning", regex=False, case_sensitive=False, context_lines=0, max_matches=10
    )
    assert len(matches) == 2


def test_case_sensitive_when_set():
    text = "WARNING: A\nwarning: B\n"
    matches, _ = server._grep_lines(
        text, "warning", regex=False, case_sensitive=True, context_lines=0, max_matches=10
    )
    assert len(matches) == 1
    assert matches[0]["line"] == "warning: B"


def test_context_lines_returns_n_before_and_after():
    text = "L1\nL2\nL3\nMATCH\nL5\nL6\nL7\n"
    matches, _ = server._grep_lines(
        text, "MATCH", regex=False, case_sensitive=False, context_lines=2, max_matches=10
    )
    assert len(matches) == 1
    ctx = matches[0]["context"]
    assert ctx == ["L2", "L3", "MATCH", "L5", "L6"]


def test_max_matches_cap_fires():
    text = "\n".join(f"hit {i}" for i in range(20))
    matches, hit_cap = server._grep_lines(
        text, "hit", regex=False, case_sensitive=False, context_lines=0, max_matches=5
    )
    assert hit_cap is True
    assert len(matches) == 5


def test_no_matches_returns_empty_not_error():
    matches, hit_cap = server._grep_lines(
        "alpha\nbeta\n", "nope", regex=False, case_sensitive=False, context_lines=0, max_matches=10
    )
    assert matches == []
    assert hit_cap is False


# --- contains selector --------------------------------------------------


def _make_window(*, window_id: int, app: str, title: str = "", sensitive: bool = False, bundle_id: str | None = None) -> Window:
    return {
        "window_id": window_id,
        "pid": 1000 + window_id,
        "app": app,
        "bundle_id": bundle_id,
        "title": title,
        "on_screen": True,
        "focused": False,
        "bounds": (0, 0, 100, 100),
        "sensitive": sensitive,
    }


@pytest.fixture(autouse=True)
def _empty_denylist(monkeypatch):
    monkeypatch.setattr(server, "_DENYLIST", denylist.Denylist.empty())


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setattr(server.permissions, "is_trusted", lambda: True)


async def test_contains_returns_first_text_match_in_list_order(trusted, monkeypatch):
    win_a = _make_window(window_id=1, app="A", title="alpha")
    win_b = _make_window(window_id=2, app="B", title="beta")
    win_c = _make_window(window_id=3, app="C", title="gamma")
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [win_a, win_b, win_c])

    text_by_window = {
        1: "no match here",
        2: "this contains the magic word",
        3: "the magic word again, but later",
    }

    def fake_read(window, **_kw):
        return {
            "window": window,
            "text": f"<wrapped>{text_by_window[window['window_id']]}</wrapped>",
            "raw_text": text_by_window[window["window_id"]],
            "truncation": {},
            "errors": [],
        }

    monkeypatch.setattr(server, "_read_one_window_sync", fake_read)

    out = await server.read_window_impl(
        window_id=None,
        app=None,
        title_match=None,
        focused=False,
        contains="magic word",
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
    assert out["window"]["window_id"] == 2  # first in list order


async def test_contains_skips_denylisted_windows(trusted, monkeypatch):
    secret_win = _make_window(
        window_id=1, app="1Password", title="vault", sensitive=True, bundle_id="com.1password.1password"
    )
    public_win = _make_window(window_id=2, app="Public", title="t")
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [secret_win, public_win])

    def fake_read(window, **_kw):
        return {
            "window": window,
            "text": "<wrapped>magic</wrapped>",
            "raw_text": "magic",
            "truncation": {},
            "errors": [],
        }

    monkeypatch.setattr(server, "_read_one_window_sync", fake_read)

    out = await server.read_window_impl(
        window_id=None,
        app=None,
        title_match=None,
        focused=False,
        contains="magic",
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
    assert out["window"]["window_id"] == 2  # secret was skipped
    assert "denylist_skipped" in out
    assert "com.1password.1password" in out["denylist_skipped"]


async def test_contains_no_matches_returns_not_found(trusted, monkeypatch):
    win = _make_window(window_id=1, app="A")
    monkeypatch.setattr(server, "_list_windows_sync", lambda _on: [win])
    monkeypatch.setattr(
        server,
        "_read_one_window_sync",
        lambda w, **_kw: {
            "window": w,
            "text": "<wrapped></wrapped>",
            "raw_text": "no match here",
            "truncation": {},
            "errors": [],
        },
    )
    out = await server.read_window_impl(
        window_id=None,
        app=None,
        title_match=None,
        focused=False,
        contains="missing-string",
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
    assert out["error"] == server.ERR_NOT_FOUND

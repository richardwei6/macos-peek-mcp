"""Tests for `peek.windows` filter + shape helpers."""

from __future__ import annotations

from peek import windows
from peek.windows import (
    KEY_BOUNDS,
    KEY_BOUNDS_H,
    KEY_BOUNDS_W,
    KEY_BOUNDS_X,
    KEY_BOUNDS_Y,
    KEY_IS_ONSCREEN,
    KEY_LAYER,
    KEY_OWNER_NAME,
    KEY_PID,
    KEY_TITLE,
    KEY_WINDOW_ID,
)


def _fake_info(*, wid, pid, layer=0, on_screen=True, app="App", title="", bounds=(0, 0, 100, 100)):
    return {
        KEY_WINDOW_ID: wid,
        KEY_PID: pid,
        KEY_LAYER: layer,
        KEY_IS_ONSCREEN: on_screen,
        KEY_OWNER_NAME: app,
        KEY_TITLE: title,
        KEY_BOUNDS: {
            KEY_BOUNDS_X: bounds[0],
            KEY_BOUNDS_Y: bounds[1],
            KEY_BOUNDS_W: bounds[2],
            KEY_BOUNDS_H: bounds[3],
        },
    }


def test_filters_kernel_pid_zero():
    raw = [
        _fake_info(wid=1, pid=0, app="Window Server"),
        _fake_info(wid=2, pid=42, app="Real"),
    ]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=lambda pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    assert len(out) == 1
    assert out[0]["app"] == "Real"


def test_filters_non_zero_layer():
    raw = [
        _fake_info(wid=1, pid=10, layer=25, app="Control Center"),
        _fake_info(wid=2, pid=20, layer=0, app="Real"),
    ]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=lambda pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    assert {w["app"] for w in out} == {"Real"}


def test_on_screen_only_filters_offscreen():
    raw = [
        _fake_info(wid=1, pid=10, on_screen=False, app="Hidden"),
        _fake_info(wid=2, pid=20, on_screen=True, app="Visible"),
    ]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=lambda pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    assert {w["app"] for w in out} == {"Visible"}

    out_all = windows.filter_and_shape(
        raw,
        on_screen_only=False,
        focused_pid=None,
        resolve_bundle_id=lambda pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    assert {w["app"] for w in out_all} == {"Hidden", "Visible"}


def test_preserves_input_order():
    raw = [
        _fake_info(wid=10, pid=10, app="C"),
        _fake_info(wid=20, pid=20, app="A"),
        _fake_info(wid=30, pid=30, app="B"),
    ]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=lambda pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    assert [w["window_id"] for w in out] == [10, 20, 30]


def test_bundle_id_resolution_via_callable():
    raw = [
        _fake_info(wid=1, pid=42, app="Foo"),
        _fake_info(wid=2, pid=99, app="Bar"),
    ]

    def resolver(pid):
        return {42: "com.foo"}.get(pid)

    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=resolver,
        is_sensitive=lambda _b, _a: False,
    )
    assert out[0]["bundle_id"] == "com.foo"
    assert out[1]["bundle_id"] is None


def test_focused_marker():
    raw = [
        _fake_info(wid=1, pid=10, app="A"),
        _fake_info(wid=2, pid=20, app="B"),
    ]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=20,
        resolve_bundle_id=lambda _pid: None,
        is_sensitive=lambda _b, _a: False,
    )
    by_app = {w["app"]: w for w in out}
    assert by_app["A"]["focused"] is False
    assert by_app["B"]["focused"] is True


def test_sensitive_marker_propagates():
    raw = [_fake_info(wid=1, pid=10, app="1Password")]
    out = windows.filter_and_shape(
        raw,
        on_screen_only=True,
        focused_pid=None,
        resolve_bundle_id=lambda _pid: "com.1password.1password",
        is_sensitive=lambda b, a: b == "com.1password.1password",
    )
    assert out[0]["sensitive"] is True

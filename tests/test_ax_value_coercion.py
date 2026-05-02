"""Tests for `peek.ax.stringify_ax_value`."""

from __future__ import annotations

from peek import ax


def test_nsstring_equivalent_returns_string():
    assert ax.stringify_ax_value("hello") == "hello"


def test_bytes_decoded():
    assert ax.stringify_ax_value(b"hi") == "hi"


def test_undecodable_bytes_returns_none():
    assert ax.stringify_ax_value(b"\xff\xfe\x00") is None


def test_cfnumber_like_returns_none():
    # An int is the closest stand-in for a CFNumber pyobjc would hand us.
    assert ax.stringify_ax_value(42) is None
    assert ax.stringify_ax_value(3.14) is None


def test_cfarray_like_returns_none():
    assert ax.stringify_ax_value(["a", "b"]) is None


def test_axvalueref_like_returns_none():
    class FakeAXValueRef:
        # Has .x / .y like a CGPoint; without an explicit string conversion.
        x = 1.0
        y = 2.0

    assert ax.stringify_ax_value(FakeAXValueRef()) is None


def test_none_returns_none():
    assert ax.stringify_ax_value(None) is None

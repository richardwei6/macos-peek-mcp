"""Tests for `peek.ax.walk_ax_tree`.

We drive the walker against `FakeElement` (defined in conftest.py), which
satisfies the `peek.ax.Element` Protocol. No pyobjc / no AX needed.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from peek import ax


def make(text=None, children=None, position=None, attrs=None):
    """Local convenience — same shape as the conftest factory."""
    from tests.conftest import FakeElement

    a: dict[str, Any] = dict(attrs or {})
    if text is not None:
        a.setdefault("AXValue", text)
    if children is not None:
        a["AXChildren"] = children
    return FakeElement(attrs=a, position=position)


def test_empty_subtree_returns_empty_text():
    root = make()
    out = ax.walk_ax_tree(root)
    assert out["text"] == ""
    assert out["flags"] == {}
    assert out["elements_visited"] == 1


def test_depth_cap_fires():
    # Build a chain 5 deep; cap at 2.
    leaf = make(text="leaf")
    mid = make(text="mid", children=[leaf])
    root = make(text="root", children=[mid])
    out = ax.walk_ax_tree(root, max_depth=1)
    # depth=0 (root) and depth=1 (mid) are walked; depth=2 (leaf) is not.
    assert "root" in out["text"]
    assert "mid" in out["text"]
    assert "leaf" not in out["text"]
    assert out["flags"].get("truncated_at_depth") is True


def test_char_cap_fires_and_trims():
    leaf = make(text="x" * 1000)
    root = make(children=[leaf])
    out = ax.walk_ax_tree(root, max_chars=50)
    assert len(out["text"]) <= 50
    assert out["flags"].get("truncated_at_chars") is True


def test_element_cap_fires():
    # 10 children, cap at 3 elements.
    children = [make(text=f"c{i}") for i in range(10)]
    root = make(children=children)
    out = ax.walk_ax_tree(root, max_elements=3)
    # Root counts as one; we should have visited at most 3.
    assert out["elements_visited"] <= 3
    assert out["flags"].get("truncated_at_elements") is True


def test_time_deadline_fires(monkeypatch):
    # Force the budget to think time has already expired by monkeypatching
    # time.monotonic so the *first* check returns "deadline already passed."
    base = time.monotonic()
    seq = iter([base, base + 100.0, base + 100.0, base + 100.0])

    def fake_monotonic():
        return next(seq, base + 100.0)

    monkeypatch.setattr(ax.time, "monotonic", fake_monotonic)
    root = make(text="visible", children=[make(text="invisible")])
    out = ax.walk_ax_tree(root, max_time_seconds=0.001)
    assert out["flags"].get("truncated_at_time_limit") is True


def test_reading_order_y_then_x():
    # children at (y=10,x=80), (y=10,x=5), (y=200,x=5)
    a = make(text="A", position=(80, 10))
    b = make(text="B", position=(5, 10))
    c = make(text="C", position=(5, 200))
    root = make(children=[a, b, c])
    out = ax.walk_ax_tree(root)
    # B before A (same y, lower x), then C (later y).
    text = out["text"]
    assert text.index("B") < text.index("A") < text.index("C")


def test_mixed_title_and_value_does_not_double_extract():
    # Same string in Title and Value should appear once.
    root = make(attrs={"AXTitle": "shared", "AXValue": "shared"})
    out = ax.walk_ax_tree(root)
    assert out["text"].count("shared") == 1


def test_non_string_axvalue_is_skipped_not_stringified():
    # AXValue is a "weird" CFNumber-shaped object; should be skipped.
    class WeirdValue:
        def __repr__(self):
            return "<WeirdValue 0x123: number=42>"

    root = make(attrs={"AXValue": WeirdValue(), "AXTitle": "kept"})
    out = ax.walk_ax_tree(root)
    assert "WeirdValue" not in out["text"]
    assert "kept" in out["text"]


def test_element_raising_during_attribute_copy_is_skipped():
    from tests.conftest import FakeElement

    # First child raises, second child is fine. Walker should report the
    # error in errors[] and continue to the second child.
    bad = FakeElement(raise_on_attrs=RuntimeError("boom"))
    good = make(text="survived")
    root = make(children=[bad, good])
    out = ax.walk_ax_tree(root)
    assert "survived" in out["text"]
    assert any("boom" in e for e in out["errors"])

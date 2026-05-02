"""Shared pytest fixtures.

Notably: a `FakeElement` dataclass that satisfies the `peek.ax.Element`
Protocol so unit tests don't have to mock pyobjc at the FFI boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pytest


@dataclass
class FakeElement:
    """Implements `peek.ax.Element` for unit tests.

    `attrs` is the dict that `attribute_values()` returns. `position` is a
    convenience for callers that want to set the AXPosition without
    typing it into `attrs` themselves. `raise_on_attrs` lets a test force
    an exception for the next attribute_values() call.
    """

    attrs: dict[str, Any] = field(default_factory=dict)
    position: tuple[float, float] | None = None  # (x, y)
    raise_on_attrs: BaseException | None = None

    def __post_init__(self) -> None:
        if self.position is not None and "AXPosition" not in self.attrs:
            self.attrs["AXPosition"] = self.position

    def attribute_values(self, names: Iterable[str]) -> dict[str, Any]:
        if self.raise_on_attrs is not None:
            err = self.raise_on_attrs
            self.raise_on_attrs = None
            raise err
        return {n: self.attrs.get(n) for n in names if n in self.attrs}


@pytest.fixture
def fake_element_factory():
    """Returns a builder that creates FakeElement chains easily."""

    def _make(text: str | None = None, *, children: list[FakeElement] | None = None,
              position: tuple[float, float] | None = None,
              attrs: dict[str, Any] | None = None) -> FakeElement:
        a: dict[str, Any] = dict(attrs or {})
        if text is not None:
            a.setdefault("AXValue", text)
        if children is not None:
            a["AXChildren"] = children
        return FakeElement(attrs=a, position=position)

    return _make

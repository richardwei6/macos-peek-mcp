"""AX tree walker, value coercion, and CGWindowID <-> AXUIElement mapping.

This is the core of the read path. Three responsibilities:

1. `walk_ax_tree()` — depth-first traversal of an AXUIElement that pulls
   text out of every text-bearing attribute, with hard caps on depth, char
   count, element count, and wall-clock time. Returns the joined text plus
   a flag dict the server uses to populate `truncated_at_*` in the response.
2. `stringify_ax_value()` — defensive coercion. Some AX values are NSString,
   some are CFNumber / CFArray / AXValueRef-encoded points/sizes/rects.
   We only stringify the first kind; the others come back as None so they
   never leak into the agent's context as junk.
3. `find_window_ax(pid, ...)` — resolve a CGWindowID-or-title-match to a
   specific AXUIElement under an app's AXApplication root. Uses the
   private `_AXUIElementGetWindow` SPI when available; falls back to title
   matching when the symbol can't be resolved.

The `Element` Protocol at the top of the module describes the shape this
file expects. Live pyobjc objects satisfy it; unit tests pass dataclasses
that implement the same protocol so we don't need to mock at the FFI
boundary.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, TypedDict

logger = logging.getLogger(__name__)


# --- AX attribute names ----------------------------------------------------

AX_TITLE = "AXTitle"
AX_VALUE = "AXValue"
AX_DESCRIPTION = "AXDescription"
AX_HELP = "AXHelp"
AX_SELECTED_TEXT = "AXSelectedText"
AX_PLACEHOLDER = "AXPlaceholderValue"
AX_CHILDREN = "AXChildren"
AX_POSITION = "AXPosition"
AX_WINDOW = "AXWindow"
AX_WINDOWS = "AXWindows"
AX_FOCUSED_UI_ELEMENT = "AXFocusedUIElement"
AX_FOCUSED_WINDOW = "AXFocusedWindow"
AX_TITLE_OF_WINDOW = "AXTitle"
AX_ROLE = "AXRole"

# Attributes that contribute text to the walker output.
TEXT_ATTRS = (AX_VALUE, AX_TITLE, AX_DESCRIPTION, AX_HELP, AX_SELECTED_TEXT, AX_PLACEHOLDER)

# Attributes batch-fetched per element. Includes children + position so we
# need only one syscall per element instead of one per attribute.
BATCH_ATTRS = (*TEXT_ATTRS, AX_CHILDREN, AX_POSITION)


# --- public types ----------------------------------------------------------


class TruncationFlags(TypedDict, total=False):
    truncated_at_chars: bool
    truncated_at_depth: bool
    truncated_at_elements: bool
    truncated_at_time_limit: bool


class WalkResult(TypedDict):
    text: str
    flags: TruncationFlags
    elements_visited: int
    errors: list[str]


# --- mockable element protocol ---------------------------------------------


class Element(Protocol):
    """Minimal interface the walker needs from an AXUIElement.

    Live pyobjc AXUIElement instances satisfy this through duck typing on
    `attribute_values()`. Unit tests pass `FakeElement` dataclasses with
    the same shape.
    """

    def attribute_values(self, names: Iterable[str]) -> dict[str, Any]:
        """Return a dict mapping attribute name -> value for the given names.

        Names not present on the element should be omitted (or mapped to
        `None`) — callers tolerate both. Implementations should swallow
        per-attribute errors and surface them as missing keys; raising is
        permitted but the walker will catch and log + skip the element.
        """
        ...


# --- value coercion --------------------------------------------------------


def stringify_ax_value(value: Any) -> str | None:
    """Coerce an AX attribute value to a printable string.

    Returns None for anything that isn't already string-shaped. We do
    *not* str()-cast CFNumber / CFArray / AXValueRef because that produces
    debug-shaped junk like '<CFArray 0x...: ...>' that pollutes the
    agent's context.
    """
    if value is None:
        return None
    # NSString-equivalents: pyobjc returns objc.pyobjc_unicode for these,
    # which is a str subclass. Plain str also flows through this path.
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


# --- attribute-batch helpers (live pyobjc + protocol fallback) ------------


def copy_multiple_attribute_values(
    element: Any, names: Iterable[str]
) -> dict[str, Any]:
    """Batch-read attributes from a *live* AXUIElement via pyobjc.

    Falls back to the `attribute_values()` Protocol method when the input
    isn't a pyobjc AXUIElementRef (which is what unit tests pass).

    The macOS API `AXUIElementCopyMultipleAttributeValues` returns one
    syscall worth of attribute values; we use it instead of N individual
    `AXUIElementCopyAttributeValue` calls.
    """
    if hasattr(element, "attribute_values"):
        try:
            return element.attribute_values(names)
        except Exception as exc:  # tests use this path; surface errors
            raise

    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCopyMultipleAttributeValues,
        )
    except ImportError:
        return {}

    names_list = list(names)
    err, values = AXUIElementCopyMultipleAttributeValues(element, names_list, 0, None)
    if err != 0 or values is None:
        return {}
    out: dict[str, Any] = {}
    for name, val in zip(names_list, values):
        # AXValueGetTypeID-flagged sentinel: pyobjc returns an AXValueRef
        # for missing attributes when the bitmask flag is 0 (default). The
        # sentinel object is identity-comparable to a known constant; we
        # simply treat any non-string/non-list value as opaque downstream.
        out[name] = val
    return out


# --- walker ----------------------------------------------------------------


@dataclass
class _WalkBudget:
    """Mutable accounting for the current walk."""

    max_depth: int
    max_chars: int
    max_elements: int
    deadline: float  # monotonic time after which we set truncated_at_time_limit
    pieces: list[str] = field(default_factory=list)
    chars: int = 0
    elements: int = 0
    flags: TruncationFlags = field(default_factory=lambda: TruncationFlags())
    errors: list[str] = field(default_factory=list)

    def time_exceeded(self) -> bool:
        return time.monotonic() >= self.deadline

    def push_text(self, text: str) -> bool:
        """Append `text`, respecting `max_chars`. Returns True if we accepted
        any (or all) of it; False if the cap was already at zero remaining.
        """
        if not text:
            return True
        remaining = self.max_chars - self.chars
        if remaining <= 0:
            self.flags["truncated_at_chars"] = True
            return False
        if len(text) > remaining:
            self.pieces.append(text[:remaining])
            self.chars = self.max_chars
            self.flags["truncated_at_chars"] = True
            return True
        self.pieces.append(text)
        self.chars += len(text)
        return True


def _position_key(child: Any, attrs: dict[str, Any]) -> tuple[float, float]:
    """Return (y, x) sort key, defaulting to (0, 0) when position is missing.

    `kAXPositionAttribute` returns an AXValueRef wrapping a CGPoint. pyobjc
    exposes it via `__pyobjc_object__.x` / `.y` on macOS. Tests typically
    pass a tuple `(x, y)` or an object with `.x` / `.y` attributes.
    """
    pos = attrs.get(AX_POSITION)
    if pos is None:
        return (0.0, 0.0)
    # tuple (x, y)
    if isinstance(pos, (tuple, list)) and len(pos) >= 2:
        return (float(pos[1]), float(pos[0]))
    x = getattr(pos, "x", None)
    y = getattr(pos, "y", None)
    if x is not None and y is not None:
        return (float(y), float(x))
    return (0.0, 0.0)


def walk_ax_tree(
    root: Any,
    *,
    max_depth: int = 50,
    max_chars: int = 200_000,
    max_elements: int = 50_000,
    max_time_seconds: float = 3.0,
) -> WalkResult:
    """Depth-first walk of an AX subtree, emitting joined text.

    Caps fire independently. When any cap fires we set the corresponding
    flag and stop accumulating (but we don't raise — partial output is
    valuable). Per-element exceptions are logged and the offending element
    is skipped; the walk continues.
    """
    budget = _WalkBudget(
        max_depth=max_depth,
        max_chars=max_chars,
        max_elements=max_elements,
        deadline=time.monotonic() + max_time_seconds,
    )
    _walk(root, depth=0, budget=budget, seen_text=set())
    return {
        "text": "\n".join(budget.pieces),
        "flags": budget.flags,
        "elements_visited": budget.elements,
        "errors": budget.errors,
    }


def _walk(element: Any, *, depth: int, budget: _WalkBudget, seen_text: set[int]) -> None:
    if budget.time_exceeded():
        budget.flags["truncated_at_time_limit"] = True
        return
    if depth > budget.max_depth:
        budget.flags["truncated_at_depth"] = True
        return
    if budget.elements >= budget.max_elements:
        budget.flags["truncated_at_elements"] = True
        return
    if budget.chars >= budget.max_chars:
        budget.flags["truncated_at_chars"] = True
        return

    budget.elements += 1

    try:
        attrs = copy_multiple_attribute_values(element, BATCH_ATTRS)
    except Exception as exc:
        # Skip this element; the walk continues.
        msg = f"attribute_copy_failed: {type(exc).__name__}: {exc}"
        logger.debug(msg)
        budget.errors.append(msg)
        return

    # Emit text from each text-bearing attribute. De-duplicate against
    # the per-element set of pieces we've already emitted: many AX
    # implementations duplicate Title into Description, etc.
    seen_for_element: set[str] = set()
    for attr in TEXT_ATTRS:
        text = stringify_ax_value(attrs.get(attr))
        if text is None:
            continue
        text = text.strip()
        if not text or text in seen_for_element:
            continue
        seen_for_element.add(text)
        if not budget.push_text(text):
            # char cap fully hit; stop walking children
            return

    children = attrs.get(AX_CHILDREN)
    if not children:
        return
    # children may be a CFArray; coerce to list for safe iteration
    children_list = list(children)

    # Reading order: sort by position (y, x). Collect each child's position
    # alongside it; on missing position, fall back to (0, 0) which preserves
    # original order through Python's stable sort.
    decorated: list[tuple[tuple[float, float], int, Any]] = []
    for idx, child in enumerate(children_list):
        try:
            pos_attrs = copy_multiple_attribute_values(child, (AX_POSITION,))
        except Exception:
            pos_attrs = {}
        decorated.append((_position_key(child, pos_attrs), idx, child))
    decorated.sort(key=lambda t: (t[0], t[1]))

    for _, _, child in decorated:
        if budget.time_exceeded():
            budget.flags["truncated_at_time_limit"] = True
            return
        if budget.elements >= budget.max_elements:
            budget.flags["truncated_at_elements"] = True
            return
        if budget.chars >= budget.max_chars:
            budget.flags["truncated_at_chars"] = True
            return
        _walk(child, depth=depth + 1, budget=budget, seen_text=seen_text)


# --- _AXUIElementGetWindow binding ----------------------------------------


_FRAMEWORK_PATH = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


def _bind_get_window() -> Any | None:
    """Bind the private `_AXUIElementGetWindow` SPI via ctypes.

    Returns None on any failure (framework can't load, symbol missing).
    Callers fall back to title+bounds matching when this is None.
    """
    try:
        lib = ctypes.CDLL(_FRAMEWORK_PATH)
    except OSError as exc:
        logger.warning("cannot dlopen ApplicationServices (%s); falling back", exc)
        return None
    try:
        sym = lib._AXUIElementGetWindow
    except AttributeError:
        logger.warning(
            "_AXUIElementGetWindow symbol not found in ApplicationServices; "
            "falling back to title+bounds matching"
        )
        return None
    sym.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    sym.restype = ctypes.c_int32
    return sym


_GET_WINDOW = _bind_get_window()


def get_window_id_for_ax_window(ax_window: Any) -> int | None:
    """Return the CGWindowID for an AX window element, or None on failure."""
    if _GET_WINDOW is None:
        return None
    try:
        import objc  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        ptr = objc.pyobjc_id(ax_window)
    except Exception as exc:
        logger.debug("cannot extract raw pointer for AXUIElement: %s", exc)
        return None
    out = ctypes.c_uint32(0)
    status = _GET_WINDOW(ctypes.c_void_p(ptr), ctypes.byref(out))
    if status != 0:
        return None
    return int(out.value)


# --- AXUIElement resolvers -------------------------------------------------


def app_to_ax(pid: int) -> Any | None:
    """Return the AXApplication element for a given pid, or None on error."""
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCreateApplication,
        )
    except ImportError:
        return None
    return AXUIElementCreateApplication(pid)


def _ax_copy(element: Any, attr: str) -> Any:
    """Read a single attribute from a live AXUIElement (pyobjc)."""
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCopyAttributeValue,
        )
    except ImportError:
        return None
    err, value = AXUIElementCopyAttributeValue(element, attr, None)
    if err != 0:
        return None
    return value


@dataclass
class FindWindowResult:
    element: Any | None  # AXUIElementRef
    error: str | None = None  # "ambiguous_window" | "not_found" | None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def find_window_ax(
    pid: int,
    *,
    window_id: int | None = None,
    title_match: str | None = None,
    case_sensitive: bool = False,
) -> FindWindowResult:
    """Locate an AXWindow under the given app.

    Resolution priority:
    1. If `window_id` is given and the `_AXUIElementGetWindow` SPI is
       available, find the AXWindow whose CGWindowID matches.
    2. Otherwise filter the app's AXWindows by `title_match`. Exact match
       wins over substring; substring is case-insensitive by default. If
       multiple windows match equally, return `ambiguous_window`.
    3. If neither selector matches anything, return `not_found`.
    """
    app = app_to_ax(pid)
    if app is None:
        return FindWindowResult(element=None, error="not_found")
    windows = _ax_copy(app, AX_WINDOWS)
    if not windows:
        return FindWindowResult(element=None, error="not_found")
    windows_list = list(windows)

    return _select_window(
        windows_list,
        window_id=window_id,
        title_match=title_match,
        case_sensitive=case_sensitive,
    )


def _select_window(
    windows: list[Any],
    *,
    window_id: int | None,
    title_match: str | None,
    case_sensitive: bool,
) -> FindWindowResult:
    """Pure helper: pick a window from a list of AX windows by selector.

    Tests drive this directly with mocked windows; live code reaches it
    through `find_window_ax`.
    """
    # 1. window_id via _AXUIElementGetWindow when available
    if window_id is not None and _GET_WINDOW is not None:
        for w in windows:
            wid = get_window_id_for_ax_window(w)
            if wid == window_id:
                return FindWindowResult(element=w)
        # window_id supplied but not found — fall through to title match
        # if also supplied, otherwise return not_found.
        if title_match is None:
            return FindWindowResult(element=None, error="not_found")

    # 2. title-based fallback
    if title_match is not None:
        haystack = title_match if case_sensitive else title_match.casefold()
        exact: list[Any] = []
        substring: list[Any] = []
        for w in windows:
            title_attr = _ax_copy(w, AX_TITLE)
            title = stringify_ax_value(title_attr) or ""
            cmp_title = title if case_sensitive else title.casefold()
            if cmp_title == haystack:
                exact.append(w)
            elif haystack in cmp_title:
                substring.append(w)
        if len(exact) == 1:
            return FindWindowResult(element=exact[0])
        if len(exact) > 1:
            return FindWindowResult(
                element=None,
                error="ambiguous_window",
                candidates=[{"title": stringify_ax_value(_ax_copy(w, AX_TITLE)) or ""} for w in exact],
            )
        if len(substring) == 1:
            return FindWindowResult(element=substring[0])
        if len(substring) > 1:
            return FindWindowResult(
                element=None,
                error="ambiguous_window",
                candidates=[{"title": stringify_ax_value(_ax_copy(w, AX_TITLE)) or ""} for w in substring],
            )
        return FindWindowResult(element=None, error="not_found")

    # 3. window_id given but symbol unavailable
    if window_id is not None and _GET_WINDOW is None:
        return FindWindowResult(
            element=None,
            error="ambiguous_window",
            candidates=[{"reason": "no_window_id_resolution_available"}],
        )

    return FindWindowResult(element=None, error="not_found")


def focused_window_ax() -> tuple[int | None, Any | None]:
    """Return (pid, AXWindow) of the system-wide focused window, or (None, None)."""
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCreateSystemWide,
        )
    except ImportError:
        return None, None
    sysw = AXUIElementCreateSystemWide()
    focused = _ax_copy(sysw, AX_FOCUSED_UI_ELEMENT)
    if focused is None:
        return None, None
    window = _ax_copy(focused, AX_WINDOW)
    if window is None:
        return None, None
    pid = _pid_of_ax(window)
    return pid, window


def _pid_of_ax(element: Any) -> int | None:
    """Return the pid of the process owning an AXUIElement, via pyobjc helper."""
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementGetPid,
        )
    except ImportError:
        return None
    err, pid = AXUIElementGetPid(element, None)
    if err != 0:
        return None
    return int(pid)

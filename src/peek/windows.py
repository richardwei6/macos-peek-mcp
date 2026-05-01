"""Window enumeration via CGWindowList.

`list_windows()` returns the visible-window inventory we expose through the
`list_windows` MCP tool, plus what `read_window` consumes when resolving a
window by `app+title_match`, `focused`, or `contains` selectors.

AX trust is **not** required for any of this — `CGWindowListCopyWindowInfo`
is granted to every process on macOS.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, TypedDict

logger = logging.getLogger(__name__)


# --- public types ----------------------------------------------------------


class Window(TypedDict):
    window_id: int
    pid: int
    app: str
    bundle_id: str | None
    title: str
    on_screen: bool
    focused: bool
    bounds: tuple[int, int, int, int]  # (x, y, w, h) in screen coords
    sensitive: bool


# --- CGWindowList field keys (string constants the dict uses) --------------

# These are the literal CFString values that the CGWindowList API returns.
# We hard-code the strings so unit tests can synthesize the input dicts
# without importing pyobjc.
KEY_WINDOW_ID = "kCGWindowNumber"
KEY_PID = "kCGWindowOwnerPID"
KEY_OWNER_NAME = "kCGWindowOwnerName"
KEY_TITLE = "kCGWindowName"
KEY_LAYER = "kCGWindowLayer"
KEY_BOUNDS = "kCGWindowBounds"
KEY_IS_ONSCREEN = "kCGWindowIsOnscreen"
KEY_BOUNDS_X = "X"
KEY_BOUNDS_Y = "Y"
KEY_BOUNDS_W = "Width"
KEY_BOUNDS_H = "Height"


# --- bundle-id resolver (pyobjc-backed; mockable) --------------------------

BundleIDResolver = Callable[[int], str | None]
SensitiveResolver = Callable[[str | None, str], bool]
FocusedPidResolver = Callable[[], int | None]


def _default_bundle_id_resolver(pid: int) -> str | None:
    """Resolve the bundle ID for a pid via NSRunningApplication.

    Returns None for processes without a registered bundle (helpers, agents,
    daemons). Imports pyobjc lazily so the module loads on non-macOS hosts.
    """
    try:
        from AppKit import NSRunningApplication  # type: ignore[import-not-found]
    except ImportError:
        return None
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return None
    bundle_id = app.bundleIdentifier()
    return str(bundle_id) if bundle_id else None


def _default_focused_pid_resolver() -> int | None:
    """Return the PID of the frontmost application, or None on failure."""
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]
    except ImportError:
        return None
    ws = NSWorkspace.sharedWorkspace()
    front = ws.frontmostApplication()
    if front is None:
        return None
    return int(front.processIdentifier())


def _default_sensitive_resolver(_bundle_id: str | None, _app: str) -> bool:
    """Default: nothing is sensitive when no denylist is supplied.

    `server.py` injects the real resolver bound to the loaded denylist.
    Keeping the default false-only means tests that don't care about the
    denylist don't need to fake one.
    """
    return False


# --- core: read CGWindowList -----------------------------------------------


def _copy_window_list(on_screen_only: bool) -> list[dict[str, Any]]:
    """Call `CGWindowListCopyWindowInfo` and return a list of plain dicts.

    Pyobjc returns a CFArray of CFDictionary; we coerce to a list of native
    dicts so callers (and tests) only deal with stdlib types.
    """
    try:
        from Quartz import (  # type: ignore[import-not-found]
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionAll,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError:
        logger.warning("pyobjc Quartz unavailable; returning empty window list")
        return []

    if on_screen_only:
        opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    else:
        opts = kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements

    infos = CGWindowListCopyWindowInfo(opts, kCGNullWindowID)
    if infos is None:
        return []
    # Coerce CFDictionary -> dict (pyobjc supports `dict(cfdict)`).
    return [dict(info) for info in infos]


# --- filtering & shaping ---------------------------------------------------


def _is_useful_window(info: dict[str, Any]) -> bool:
    """Drop kernel/system entries we never want to expose.

    - pid == 0 → kernel-owned ghost windows
    - layer != 0 → menubar / dock / status indicators / floating chrome
    """
    pid = info.get(KEY_PID, 0)
    if not pid:
        return False
    layer = info.get(KEY_LAYER, 0)
    if layer != 0:
        return False
    return True


def _bounds_tuple(info: dict[str, Any]) -> tuple[int, int, int, int]:
    b = info.get(KEY_BOUNDS) or {}
    # CGRectMakeWithDictionaryRepresentation keys are X/Y/Width/Height.
    return (
        int(b.get(KEY_BOUNDS_X, 0) or 0),
        int(b.get(KEY_BOUNDS_Y, 0) or 0),
        int(b.get(KEY_BOUNDS_W, 0) or 0),
        int(b.get(KEY_BOUNDS_H, 0) or 0),
    )


def shape_window(
    info: dict[str, Any],
    *,
    focused_pid: int | None,
    resolve_bundle_id: BundleIDResolver,
    is_sensitive: SensitiveResolver,
) -> Window:
    """Convert a CGWindowList dict into our `Window` TypedDict shape."""
    pid = int(info.get(KEY_PID) or 0)
    app = str(info.get(KEY_OWNER_NAME) or "")
    title = str(info.get(KEY_TITLE) or "")
    bundle_id = resolve_bundle_id(pid) if pid else None
    return {
        "window_id": int(info.get(KEY_WINDOW_ID) or 0),
        "pid": pid,
        "app": app,
        "bundle_id": bundle_id,
        "title": title,
        "on_screen": bool(info.get(KEY_IS_ONSCREEN, True)),
        "focused": pid == focused_pid if focused_pid else False,
        "bounds": _bounds_tuple(info),
        "sensitive": is_sensitive(bundle_id, app),
    }


def filter_and_shape(
    raw: Iterable[dict[str, Any]],
    *,
    on_screen_only: bool,
    focused_pid: int | None,
    resolve_bundle_id: BundleIDResolver,
    is_sensitive: SensitiveResolver,
) -> list[Window]:
    """Pure helper that takes already-fetched CGWindowList dicts and returns
    our shaped, filtered output. Tests drive `windows.list_windows()` by
    feeding fake CGWindowList dicts to this function.
    """
    out: list[Window] = []
    for info in raw:
        if not _is_useful_window(info):
            continue
        if on_screen_only and not bool(info.get(KEY_IS_ONSCREEN, True)):
            continue
        out.append(
            shape_window(
                info,
                focused_pid=focused_pid,
                resolve_bundle_id=resolve_bundle_id,
                is_sensitive=is_sensitive,
            )
        )
    return out


def list_windows(
    on_screen_only: bool = True,
    *,
    resolve_bundle_id: BundleIDResolver | None = None,
    focused_pid_resolver: FocusedPidResolver | None = None,
    is_sensitive: SensitiveResolver | None = None,
) -> list[Window]:
    """Return the visible-window inventory.

    Hooks let server.py inject the loaded denylist resolver and let unit
    tests inject everything. By default we use the live pyobjc resolvers.
    """
    raw = _copy_window_list(on_screen_only)
    bundle_id_resolver = resolve_bundle_id or _default_bundle_id_resolver
    focused_resolver = focused_pid_resolver or _default_focused_pid_resolver
    sensitivity = is_sensitive or _default_sensitive_resolver
    focused_pid = focused_resolver()
    return filter_and_shape(
        raw,
        on_screen_only=on_screen_only,
        focused_pid=focused_pid,
        resolve_bundle_id=bundle_id_resolver,
        is_sensitive=sensitivity,
    )

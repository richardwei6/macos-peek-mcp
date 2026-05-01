"""Spike for the private SPI `_AXUIElementGetWindow`.

This is a standalone script — *not* part of the package — used to verify the
ctypes binding works on the developer's Mac before we wire it into ax.py.

Run with:

    uv run python scripts/spike_ax_window.py

Requires AX trust on the running interpreter. Without trust, AX calls return
nil/None silently; the binding itself still loads.

What this prints:
- whether the symbol resolved at the framework path we use
- AXIsProcessTrusted() result
- the focused application's AXUIElement (best-effort)
- the CGWindowID of the focused window, if `_AXUIElementGetWindow` succeeds
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys


FRAMEWORK_PATH = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


def main() -> int:
    try:
        appservices = ctypes.CDLL(FRAMEWORK_PATH)
    except OSError as exc:
        print(f"FAIL: cannot dlopen ApplicationServices framework: {exc}", file=sys.stderr)
        return 1

    # OSStatus _AXUIElementGetWindow(AXUIElementRef element, CGWindowID *outWindowID);
    # OSStatus is int32, AXUIElementRef is void*, CGWindowID is uint32.
    try:
        get_window = appservices._AXUIElementGetWindow
    except AttributeError as exc:
        print(f"FAIL: _AXUIElementGetWindow symbol not found: {exc}", file=sys.stderr)
        return 2

    get_window.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_window.restype = ctypes.c_int32
    print(f"OK: bound _AXUIElementGetWindow at {FRAMEWORK_PATH}")

    # Optional: poke at AX trust state and the focused window.
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXIsProcessTrusted,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXFocusedUIElementAttribute,
            kAXWindowAttribute,
        )
    except ImportError:
        print("note: pyobjc ApplicationServices not importable in this venv; skipping live probe")
        return 0

    print(f"AXIsProcessTrusted() = {AXIsProcessTrusted()}")

    system_wide = AXUIElementCreateSystemWide()
    err, focused_elem = AXUIElementCopyAttributeValue(
        system_wide, kAXFocusedUIElementAttribute, None
    )
    if err != 0 or focused_elem is None:
        print(f"note: could not read focused UI element (err={err}); not trusted?")
        return 0

    err, focused_window = AXUIElementCopyAttributeValue(
        focused_elem, kAXWindowAttribute, None
    )
    if err != 0 or focused_window is None:
        print(f"note: focused element has no AXWindow (err={err})")
        return 0

    # pyobjc returns the AXUIElementRef as a wrapped object. We need the raw
    # pointer to feed ctypes. The wrapper exposes `__c_void_p__` via objc.
    try:
        import objc  # type: ignore[import-not-found]

        ptr = objc.pyobjc_id(focused_window)
    except Exception as exc:
        print(f"note: cannot extract raw pointer from AXUIElementRef ({exc})")
        return 0

    out = ctypes.c_uint32(0)
    status = get_window(ctypes.c_void_p(ptr), ctypes.byref(out))
    if status != 0:
        print(f"_AXUIElementGetWindow returned OSStatus={status}")
    else:
        print(f"focused window CGWindowID = {out.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

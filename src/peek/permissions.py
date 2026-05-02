"""AX trust + binary-path-drift detection.

Two responsibilities:

1. Wrap the small set of pyobjc calls that report whether the running
   binary has been granted Accessibility trust, and open the right
   System Settings pane on first failure.
2. Persist the path + sha256 of the binary inside the .app bundle the
   user granted trust to (the Mach-O at
   ``/Applications/Peek.app/Contents/MacOS/peek-mcp``, placed there by
   ``peek install``). On every subsequent call, diff against the
   persisted record so we can warn when the bundle's binary has been
   replaced (e.g., a rebuild) and AX trust must be re-granted.

State persists at:
    $XDG_CONFIG_HOME/peek-mcp/state.json
    or ~/.config/peek-mcp/state.json
    or ~/Library/Application Support/peek-mcp/state.json (fallback)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


# --- config dir resolution -------------------------------------------------

CONFIG_DIRNAME = "peek-mcp"
STATE_FILENAME = "state.json"

# AX trust is granted to the .app bundle at APP_BUNDLE_PATH. `peek
# install` copies the freshly-built bundle here and creates a CLI
# symlink at CLI_SYMLINK_PATH that resolves to BUNDLE_BINARY_PATH.
#
# The kernel attaches AX trust to the Mach-O the bundle exec()s plus
# the bundle's CFBundleIdentifier; drift detection hashes
# BUNDLE_BINARY_PATH (when present) so we can warn when the bundle has
# been replaced (e.g. by a rebuild) and trust must be re-granted.
APP_BUNDLE_PATH = Path("/Applications/Peek.app")
BUNDLE_BINARY_PATH = APP_BUNDLE_PATH / "Contents" / "MacOS" / "peek-mcp"
CLI_SYMLINK_PATH = Path.home() / ".local" / "bin" / "peek-mcp"


def config_dir() -> Path:
    """Return the directory we use for user state and the user denylist.

    Honors `$XDG_CONFIG_HOME` if set; otherwise falls back to `~/.config`,
    then to `~/Library/Application Support` (macOS-native).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / CONFIG_DIRNAME
    home_config = Path.home() / ".config"
    if home_config.exists() or _can_create(home_config):
        return home_config / CONFIG_DIRNAME
    return Path.home() / "Library" / "Application Support" / CONFIG_DIRNAME


def _can_create(path: Path) -> bool:
    """Return True if `path` (or its parent) is writable enough to mkdir."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def state_path() -> Path:
    return config_dir() / STATE_FILENAME


# --- AX trust --------------------------------------------------------------


def is_trusted() -> bool:
    """Return True if the running binary has been granted AX trust.

    We import pyobjc lazily so the module is importable on non-macOS hosts
    (and in unit tests that mock this function).
    """
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("pyobjc ApplicationServices unavailable; reporting AX trust = False")
        return False
    return bool(AXIsProcessTrusted())


def open_settings_pane() -> None:
    """Open System Settings → Privacy & Security → Accessibility."""
    url = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    try:
        subprocess.run(["open", url], check=False)
    except FileNotFoundError:
        logger.warning("`open` not found; cannot launch System Settings")


# --- binary-path drift -----------------------------------------------------


@dataclass(frozen=True)
class TrustRecord:
    path: str
    sha256: str


class DriftReport(TypedDict):
    drifted: bool
    prior_path: str | None
    prior_hash: str | None
    current_path: str
    current_hash: str


def _sha256_of_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _interpreter_path() -> str:
    """Return the path of the trusted artifact whose hash we persist.

    The user grants AX trust to the .app bundle at
    ``/Applications/Peek.app``. The kernel records the cdhash of the
    Mach-O at ``Contents/MacOS/peek-mcp``, so that's the meaningful
    artifact for drift detection.

    Precedence:
      1. ``BUNDLE_BINARY_PATH`` if present — the installed bundle's
         binary, which is what TCC actually checks.
      2. ``sys.executable`` fallback — covers running from the
         freshly-built ``dist/peek-mcp/peek-mcp`` (--onedir output)
         before install, and dev mode via ``python -m peek`` or uv.
         Either way drift detection still gives a signal.
    """
    if BUNDLE_BINARY_PATH.exists():
        return str(BUNDLE_BINARY_PATH)
    return sys.executable


def _load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable state file at %s (%s); ignoring", p, exc)
        return {}


def _save_state(data: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)


def record_trusted_state(path: str | None = None) -> TrustRecord:
    """Persist the path + sha256 of the binary AX trust was granted to.

    Called by `peek doctor` after a successful trusted call. Subsequent
    `check_path_drift()` calls compare against this record.
    """
    target = path or _interpreter_path()
    digest = _sha256_of_file(target)
    state = _load_state()
    state["trusted"] = {"path": target, "sha256": digest}
    _save_state(state)
    return TrustRecord(path=target, sha256=digest)


def check_path_drift() -> DriftReport:
    """Diff persisted trust record against the current binary path/hash.

    `drifted=False` covers both "no prior record" and "matches prior". The
    caller decides how to surface drift to the user.
    """
    current_path = _interpreter_path()
    try:
        current_hash = _sha256_of_file(current_path)
    except OSError as exc:
        logger.warning("cannot hash current binary at %s (%s)", current_path, exc)
        current_hash = ""

    state = _load_state().get("trusted") or {}
    prior_path = state.get("path")
    prior_hash = state.get("sha256")

    drifted = bool(prior_path) and (
        prior_path != current_path or prior_hash != current_hash
    )
    return {
        "drifted": drifted,
        "prior_path": prior_path,
        "prior_hash": prior_hash,
        "current_path": current_path,
        "current_hash": current_hash,
    }

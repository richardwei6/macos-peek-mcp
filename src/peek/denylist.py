"""Denylist load + match for sensitive apps.

The denylist is two lists:
  - `bundle_ids`: exact bundle ID match (preferred)
  - `app_name_patterns`: case-insensitive substring against the app's name
    (CGWindow `kCGWindowOwnerName`)

We ship a default denylist inside the package
(`src/peek/data/default-denylist.toml`). On first run, the user copy is
seeded at:

    $XDG_CONFIG_HOME/peek-mcp/denylist.toml
    or ~/.config/peek-mcp/denylist.toml
    or ~/Library/Application Support/peek-mcp/denylist.toml

After the seed, the user owns it: subsequent calls read the user file. We
never overwrite it.
"""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from peek.permissions import config_dir

logger = logging.getLogger(__name__)


DENYLIST_FILENAME = "denylist.toml"
_DEFAULT_RESOURCE_PATH = Path(__file__).parent / "data" / "default-denylist.toml"


# --- public types ----------------------------------------------------------


@dataclass(frozen=True)
class Denylist:
    bundle_ids: frozenset[str] = field(default_factory=frozenset)
    app_name_patterns: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "Denylist":
        return cls(bundle_ids=frozenset(), app_name_patterns=())


# --- file resolution -------------------------------------------------------


def user_denylist_path() -> Path:
    return config_dir() / DENYLIST_FILENAME


def install_default_if_missing(target: Path | None = None) -> Path:
    """Copy the package's default denylist into the user config dir if absent.

    Returns the path to the (now-present) user denylist.
    """
    target = target or user_denylist_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if not _DEFAULT_RESOURCE_PATH.exists():
        # Should never happen — the file is force-included in the wheel.
        logger.warning(
            "package default denylist missing at %s; writing empty user denylist",
            _DEFAULT_RESOURCE_PATH,
        )
        target.write_text("bundle_ids = []\napp_name_patterns = []\n")
        return target
    shutil.copyfile(_DEFAULT_RESOURCE_PATH, target)
    return target


# --- load + parse ----------------------------------------------------------


def parse(text: str) -> Denylist:
    """Parse TOML denylist text. Raises tomllib.TOMLDecodeError on bad TOML.

    Callers (load()) are responsible for fail-safe behavior on parse failures.
    Tolerates missing or wrong-typed keys (logs a warning, ignores them).
    """
    data = tomllib.loads(text)
    bundle_ids = data.get("bundle_ids") or []
    patterns = data.get("app_name_patterns") or []
    if not isinstance(bundle_ids, list):
        logger.warning("denylist bundle_ids is not a list; ignoring")
        bundle_ids = []
    if not isinstance(patterns, list):
        logger.warning("denylist app_name_patterns is not a list; ignoring")
        patterns = []
    return Denylist(
        bundle_ids=frozenset(str(b) for b in bundle_ids),
        app_name_patterns=tuple(str(p) for p in patterns),
    )


def _load_package_default() -> Denylist:
    """Load the bundled default-denylist.toml. Used as a fail-safe."""
    if not _DEFAULT_RESOURCE_PATH.exists():
        logger.error(
            "package default-denylist missing at %s — privacy denylist is empty!",
            _DEFAULT_RESOURCE_PATH,
        )
        return Denylist.empty()
    try:
        return parse(_DEFAULT_RESOURCE_PATH.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.error("cannot parse package default-denylist (%s); empty fallback", exc)
        return Denylist.empty()


def load(path: Path | None = None, *, install_default: bool = True) -> Denylist:
    """Load the user denylist with fail-safe fallback to the package default.

    A malformed user denylist.toml falls back to the shipped default — *not*
    an empty denylist — so a typo or accidental edit doesn't silently
    disable privacy protection.
    """
    target = path or user_denylist_path()
    if not target.exists():
        if install_default:
            target = install_default_if_missing(target)
        else:
            return Denylist.empty()
    try:
        text = target.read_text()
    except OSError as exc:
        logger.warning(
            "cannot read user denylist at %s (%s); falling back to package default",
            target, exc,
        )
        return _load_package_default()
    try:
        return parse(text)
    except tomllib.TOMLDecodeError as exc:
        logger.warning(
            "user denylist at %s has invalid TOML (%s); falling back to package default. "
            "Fix the file and reload the MCP server to use your customizations.",
            target, exc,
        )
        return _load_package_default()


# --- matching --------------------------------------------------------------


def matches(
    bundle_id: str | None,
    app_name: str,
    denylist: Denylist,
    *,
    allow_sensitive: bool = False,
) -> bool:
    """Return True iff (bundle_id, app_name) is sensitive under `denylist`.

    `allow_sensitive=True` short-circuits to False. Bundle ID match is
    preferred and exact. App-name match is case-insensitive substring.
    """
    if allow_sensitive:
        return False
    if bundle_id and bundle_id in denylist.bundle_ids:
        return True
    if not app_name:
        return False
    haystack = app_name.casefold()
    for pat in denylist.app_name_patterns:
        if not pat:
            continue
        if pat.casefold() in haystack:
            return True
    return False

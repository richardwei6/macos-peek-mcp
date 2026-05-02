"""Tests for `peek.denylist`."""

from __future__ import annotations

import pathlib

import pytest

from peek import denylist


@pytest.fixture
def fixture_denylist(tmp_path: pathlib.Path) -> denylist.Denylist:
    """A denylist with 1Password / Keychain / Mail / Messages / Notes."""
    target = tmp_path / "denylist.toml"
    target.write_text(
        """
bundle_ids = [
    "com.1password.1password",
    "com.apple.keychainaccess",
    "com.apple.mail",
    "com.apple.MobileSMS",
    "com.apple.Notes",
]
app_name_patterns = ["1Password", "Keychain Access", "Mail", "Messages", "Notes"]
"""
    )
    return denylist.load(target, install_default=False)


def test_bundle_id_exact_match(fixture_denylist):
    assert denylist.matches("com.1password.1password", "1Password 7", fixture_denylist) is True
    assert denylist.matches("com.google.Chrome", "Google Chrome", fixture_denylist) is False


def test_app_name_substring_case_insensitive(fixture_denylist):
    # No bundle ID, app name matches case-insensitively
    assert denylist.matches(None, "1password 7", fixture_denylist) is True
    assert denylist.matches(None, "MAIL", fixture_denylist) is True


def test_missing_file_returns_empty_with_warning(tmp_path, caplog):
    target = tmp_path / "does-not-exist.toml"
    dl = denylist.load(target, install_default=False)
    assert dl.bundle_ids == frozenset()
    assert dl.app_name_patterns == ()


def test_allow_sensitive_short_circuits(fixture_denylist):
    assert (
        denylist.matches(
            "com.1password.1password", "1Password 7", fixture_denylist, allow_sensitive=True
        )
        is False
    )


def test_default_denylist_covers_known_apps():
    # Read the package's shipped default and confirm coverage.
    default_path = pathlib.Path(denylist._DEFAULT_RESOURCE_PATH)
    assert default_path.exists()
    dl = denylist.parse(default_path.read_text())
    assert "com.1password.1password" in dl.bundle_ids
    assert "com.apple.keychainaccess" in dl.bundle_ids
    assert "com.apple.mail" in dl.bundle_ids
    assert "com.apple.MobileSMS" in dl.bundle_ids
    assert "com.apple.Notes" in dl.bundle_ids
    # Sanity-check the app name patterns include "Keychain Access"
    assert any("keychain" in p.casefold() for p in dl.app_name_patterns)


def test_parse_raises_on_malformed_toml():
    """parse() raises so load() can decide on fail-safe behavior."""
    import tomllib
    with pytest.raises(tomllib.TOMLDecodeError):
        denylist.parse("this is = not valid [toml")


def test_load_falls_back_to_package_default_on_malformed_user_file(tmp_path, monkeypatch):
    """A typo in user denylist.toml must NOT silently disable privacy."""
    user_file = tmp_path / "denylist.toml"
    user_file.write_text("this is = not valid [toml")
    monkeypatch.setattr(denylist, "user_denylist_path", lambda: user_file)
    dl = denylist.load(install_default=False)
    # Falls back to the package default — has 1Password, Keychain, etc.
    assert "com.1password.1password" in dl.bundle_ids
    assert any("keychain" in p.casefold() for p in dl.app_name_patterns)


def test_load_falls_back_to_package_default_on_unreadable_user_file(tmp_path, monkeypatch):
    """An unreadable user file (permissions, etc.) also fails safe."""
    user_file = tmp_path / "denylist.toml"
    user_file.write_text("bundle_ids = []\napp_name_patterns = []\n")
    user_file.chmod(0o000)
    monkeypatch.setattr(denylist, "user_denylist_path", lambda: user_file)
    try:
        dl = denylist.load(install_default=False)
        assert "com.1password.1password" in dl.bundle_ids
    finally:
        user_file.chmod(0o600)  # restore so tmpdir cleanup works


def test_default_resource_path_resolves_in_dev_mode():
    """In dev mode (no sys._MEIPASS), _DEFAULT_RESOURCE_PATH points at the
    in-tree data file and that file exists.

    The frozen-mode (_MEIPASS) branch is exercised manually by running
    the built binary; we don't try to fake _MEIPASS in unit tests.
    """
    import sys as _sys
    assert not hasattr(_sys, "_MEIPASS"), (
        "test runs in dev mode; PyInstaller bundles set _MEIPASS"
    )
    # The module-level constant captured the dev-mode path at import time.
    assert denylist._DEFAULT_RESOURCE_PATH.exists()
    assert denylist._DEFAULT_RESOURCE_PATH.name == "default-denylist.toml"
    # And the resolver function returns the same path under the same conditions.
    assert denylist._resolve_default_resource_path() == denylist._DEFAULT_RESOURCE_PATH


def test_install_default_creates_user_file(tmp_path):
    target = tmp_path / "denylist.toml"
    assert not target.exists()
    written = denylist.install_default_if_missing(target)
    assert written == target
    assert target.exists()
    # Subsequent call is a no-op
    written2 = denylist.install_default_if_missing(target)
    assert written2 == target

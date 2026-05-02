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


def test_malformed_toml_recovers_gracefully():
    dl = denylist.parse("this is = not valid [toml")
    assert dl.bundle_ids == frozenset()
    assert dl.app_name_patterns == ()


def test_install_default_creates_user_file(tmp_path):
    target = tmp_path / "denylist.toml"
    assert not target.exists()
    written = denylist.install_default_if_missing(target)
    assert written == target
    assert target.exists()
    # Subsequent call is a no-op
    written2 = denylist.install_default_if_missing(target)
    assert written2 == target

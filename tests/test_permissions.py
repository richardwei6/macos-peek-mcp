"""Unit tests for `peek.permissions._interpreter_path` precedence
plus `peek.cli` sudo-aware helpers.

The trusted-artifact path determines what AX trust drift is computed
against. Three cases the function must handle, in order:

  1. The installed bundle binary at ``/Applications/Peek.app/Contents/MacOS/peek-mcp``
     is present -> hash that.
  2. Bundle missing -> hash ``sys.executable`` (covers running from the
     freshly-built ``dist/peek-mcp/peek-mcp`` --onedir output, and dev
     mode via ``python -m peek`` / uv).

All paths are tmp_path / monkeypatch-driven; we never touch the user's
real ``/Applications`` or interpreter.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from peek import cli, permissions


def _make_bundle(tmp_path: Path) -> Path:
    """Build a fake Peek.app skeleton with a binary inside."""
    bundle = tmp_path / "Peek.app"
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    binary = macos / "peek-mcp"
    binary.write_bytes(b"\xCF\xFA\xED\xFE fake mach-o")
    return bundle


def test_interpreter_path_uses_bundle_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _make_bundle(tmp_path)
    binary = bundle / "Contents" / "MacOS" / "peek-mcp"

    monkeypatch.setattr(permissions, "APP_BUNDLE_PATH", bundle)
    monkeypatch.setattr(permissions, "BUNDLE_BINARY_PATH", binary)
    # sys.executable should be irrelevant when the bundle is present;
    # set it to a missing path to make wrong fallback obvious.
    monkeypatch.setattr(
        "peek.permissions.sys.executable", str(tmp_path / "nope" / "python")
    )

    assert permissions._interpreter_path() == str(binary)


def test_interpreter_path_falls_back_to_sys_executable_when_bundle_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "no-such-bundle.app"
    binary = bundle / "Contents" / "MacOS" / "peek-mcp"
    # Neither exists -> condition 1 fails.

    dist_binary = tmp_path / "dist" / "peek-mcp"
    dist_binary.parent.mkdir(parents=True)
    dist_binary.write_bytes(b"\xCF\xFA\xED\xFE dist mach-o")

    monkeypatch.setattr(permissions, "APP_BUNDLE_PATH", bundle)
    monkeypatch.setattr(permissions, "BUNDLE_BINARY_PATH", binary)
    monkeypatch.setattr("peek.permissions.sys.executable", str(dist_binary))

    assert permissions._interpreter_path() == str(dist_binary)


def test_interpreter_path_dev_mode_returns_sys_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev mode: no bundle, sys.executable is the venv interpreter.

    Should still return sys.executable so drift detection has a signal,
    even if the value isn't a Mach-O the user could grant trust to.
    """
    bundle = tmp_path / "no-bundle.app"
    binary = bundle / "Contents" / "MacOS" / "peek-mcp"

    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env python3\n")

    monkeypatch.setattr(permissions, "APP_BUNDLE_PATH", bundle)
    monkeypatch.setattr(permissions, "BUNDLE_BINARY_PATH", binary)
    monkeypatch.setattr("peek.permissions.sys.executable", str(venv_python))

    assert permissions._interpreter_path() == str(venv_python)


# --- module-level constant smoke check -------------------------------------


def test_app_bundle_path_is_system_applications() -> None:
    """The installer must default to /Applications/, not ~/Applications/.

    macOS's Privacy & Security file picker opens to /Applications/, and
    ~/Applications/ isn't in the default Spotlight scope, so users
    can't find the bundle there.
    """
    assert permissions.APP_BUNDLE_PATH == Path("/Applications/Peek.app")
    assert permissions.BUNDLE_BINARY_PATH == Path(
        "/Applications/Peek.app/Contents/MacOS/peek-mcp"
    )


# --- cli sudo-aware helpers ------------------------------------------------


def test_user_home_no_sudo_returns_path_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert cli._user_home() == Path.home()


def test_user_home_under_sudo_returns_invoking_user_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SUDO_USER is set, _user_home should return their pw_dir."""
    import pwd as _pwd

    real = _pwd.getpwuid(os.getuid())
    monkeypatch.setenv("SUDO_USER", real.pw_name)
    # Even though Path.home() under real sudo would be /var/root, in
    # this process we just want to confirm the lookup uses SUDO_USER.
    assert cli._user_home() == Path(real.pw_dir)


def test_user_home_unknown_sudo_user_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUDO_USER", "definitely-not-a-real-user-abc123xyz")
    assert cli._user_home() == Path.home()


def test_user_uid_gid_no_sudo_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert cli._user_uid_gid() is None


def test_user_uid_gid_under_sudo_returns_uid_gid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd as _pwd

    real = _pwd.getpwuid(os.getuid())
    monkeypatch.setenv("SUDO_USER", real.pw_name)
    assert cli._user_uid_gid() == (real.pw_uid, real.pw_gid)


def test_user_uid_gid_unknown_sudo_user_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUDO_USER", "definitely-not-a-real-user-abc123xyz")
    assert cli._user_uid_gid() is None


def test_cli_symlink_path_uses_user_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert cli._cli_symlink_path() == Path.home() / ".local" / "bin" / "peek-mcp"


def test_ensure_dir_owned_by_user_creates_path(tmp_path: Path) -> None:
    """Without an owner, the helper still mkdir -p's the target."""
    target = tmp_path / "a" / "b" / "c"
    cli._ensure_dir_owned_by_user(target, None)
    assert target.is_dir()


def test_ensure_dir_owned_by_user_idempotent_on_existing(tmp_path: Path) -> None:
    """When path already exists we shouldn't try to chown anything."""
    cli._ensure_dir_owned_by_user(tmp_path, None)
    assert tmp_path.is_dir()

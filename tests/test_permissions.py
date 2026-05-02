"""Unit tests for `peek.permissions._interpreter_path` precedence.

The trusted-artifact path determines what AX trust drift is computed
against. Three cases the function must handle, in order:

  1. The installed bundle binary at ``~/Applications/Peek.app/Contents/MacOS/peek-mcp``
     is present -> hash that.
  2. Bundle missing, ``sys.executable`` looks like a freshly-built
     ``dist/peek-mcp`` -> hash ``sys.executable``.
  3. Neither -> fall back to ``sys.executable`` (dev mode).

All paths are tmp_path / monkeypatch-driven; we never touch the user's
real ``~/Applications`` or interpreter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from peek import permissions


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

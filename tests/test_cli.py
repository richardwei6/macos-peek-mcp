"""Unit tests for `peek.cli._add_peek_to_claude_config`.

The helper auto-adds the peek MCP server entry to ``~/.claude.json`` so
users don't have to copy-paste a snippet after `peek-mcp install`. Every
status return value gets a dedicated test, plus the malformed-input
guards, atomicity, idempotency, and the SUDO chown path.

All tests use tmp_path + monkeypatched ``cli._user_home`` — the user's
real ``~/.claude.json`` is never touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from peek import cli


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect cli._user_home to tmp_path; suppress chown attempts."""
    monkeypatch.setattr(cli, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_user_uid_gid", lambda: None)
    return tmp_path


SYMLINK = Path("/Users/example/.local/bin/peek-mcp")


def _read_config(home: Path) -> dict:
    return json.loads((home / ".claude.json").read_text())


# --- status return values --------------------------------------------------


def test_created_when_file_does_not_exist(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    assert not config.exists()

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "created"
    assert config.exists()
    data = json.loads(config.read_text())
    assert data == {"mcpServers": {"peek": {"command": str(SYMLINK)}}}


def test_added_when_file_exists_without_mcp_servers(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    config.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n")

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "added"
    data = _read_config(fake_home)
    assert data["theme"] == "dark"
    assert data["mcpServers"] == {"peek": {"command": str(SYMLINK)}}


def test_added_alongside_existing_servers_preserves_siblings(
    fake_home: Path,
) -> None:
    config = fake_home / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "/usr/local/bin/other-mcp"},
                }
            },
            indent=2,
        )
        + "\n"
    )

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "added"
    data = _read_config(fake_home)
    assert data["mcpServers"]["other"] == {"command": "/usr/local/bin/other-mcp"}
    assert data["mcpServers"]["peek"] == {"command": str(SYMLINK)}


def test_updated_when_peek_command_path_differs(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "peek": {"command": "/old/path/peek-mcp"},
                    "other": {"command": "/usr/local/bin/other-mcp"},
                }
            },
            indent=2,
        )
        + "\n"
    )

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "updated"
    data = _read_config(fake_home)
    assert data["mcpServers"]["peek"] == {"command": str(SYMLINK)}
    # sibling untouched
    assert data["mcpServers"]["other"] == {"command": "/usr/local/bin/other-mcp"}


def test_unchanged_when_peek_already_at_desired_path(
    fake_home: Path,
) -> None:
    config = fake_home / ".claude.json"
    config.write_text(
        json.dumps(
            {"mcpServers": {"peek": {"command": str(SYMLINK)}}},
            indent=2,
        )
        + "\n"
    )
    mtime_before = config.stat().st_mtime_ns

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "unchanged"
    # No write should have happened.
    assert config.stat().st_mtime_ns == mtime_before


# --- malformed-input guards ------------------------------------------------


def test_malformed_skipped_invalid_json(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    config.write_text("{ this is not json")

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "malformed_skipped"
    # File untouched.
    assert config.read_text() == "{ this is not json"


def test_malformed_skipped_root_is_array(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    config.write_text(json.dumps([1, 2, 3]))

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "malformed_skipped"
    assert json.loads(config.read_text()) == [1, 2, 3]


def test_malformed_skipped_mcp_servers_is_string(fake_home: Path) -> None:
    config = fake_home / ".claude.json"
    config.write_text(json.dumps({"mcpServers": "not-an-object"}))

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "malformed_skipped"
    assert json.loads(config.read_text()) == {"mcpServers": "not-an-object"}


# --- formatting + atomicity ------------------------------------------------


def test_pretty_formatted_with_trailing_newline(fake_home: Path) -> None:
    cli._add_peek_to_claude_config(SYMLINK)
    text = (fake_home / ".claude.json").read_text()

    assert text.endswith("\n")
    # indent=2 means nested values are indented by two spaces.
    assert '\n  "mcpServers"' in text
    assert '\n    "peek"' in text


def test_no_temp_file_remains_after_write(fake_home: Path) -> None:
    cli._add_peek_to_claude_config(SYMLINK)

    leftovers = list(fake_home.glob(".claude.json*"))
    # Only the final file should exist.
    assert leftovers == [fake_home / ".claude.json"]


# --- idempotency -----------------------------------------------------------


def test_idempotent_created_then_unchanged(fake_home: Path) -> None:
    s1 = cli._add_peek_to_claude_config(SYMLINK)
    s2 = cli._add_peek_to_claude_config(SYMLINK)

    assert (s1, s2) == ("created", "unchanged")


# --- SUDO ownership path ---------------------------------------------------


def test_chown_called_with_sudo_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_user_uid_gid", lambda: (501, 20))

    chown_calls: list[tuple[str, int, int]] = []

    def fake_chown(path, uid, gid):
        chown_calls.append((str(path), uid, gid))

    monkeypatch.setattr(cli.os, "chown", fake_chown)

    status = cli._add_peek_to_claude_config(SYMLINK)

    assert status == "created"
    config = tmp_path / ".claude.json"
    assert (str(config), 501, 20) in chown_calls


def test_chown_oserror_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing chown shouldn't blow up the install — it's best-effort."""
    monkeypatch.setattr(cli, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_user_uid_gid", lambda: (501, 20))

    def fake_chown(path, uid, gid):
        raise OSError("not permitted")

    monkeypatch.setattr(cli.os, "chown", fake_chown)

    status = cli._add_peek_to_claude_config(SYMLINK)
    assert status == "created"
    # File should still have been written.
    assert (tmp_path / ".claude.json").exists()


# --- empty-file edge case --------------------------------------------------


def test_empty_file_treated_as_empty_object(fake_home: Path) -> None:
    """A pre-existing zero-byte ~/.claude.json is common; treat as empty."""
    config = fake_home / ".claude.json"
    config.write_text("")

    status = cli._add_peek_to_claude_config(SYMLINK)

    # File existed (so 'added', not 'created'), and we filled it in.
    assert status == "added"
    data = _read_config(fake_home)
    assert data == {"mcpServers": {"peek": {"command": str(SYMLINK)}}}


# --- claude config path resolution ----------------------------------------


def test_claude_config_path_uses_user_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_user_home", lambda: tmp_path)
    assert cli._claude_config_path() == tmp_path / ".claude.json"

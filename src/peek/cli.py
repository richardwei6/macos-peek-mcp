"""`peek` CLI: `doctor`, `list`, `read`, `install` subcommands.

Used for manual development against the live AX API and to install the
PyInstaller-built ``Peek.app`` bundle at the stable path AX trust is
granted to (``/Applications/Peek.app``), plus a CLI symlink at
``~/.local/bin/peek-mcp`` for ergonomics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pwd
import shutil
import sys
from pathlib import Path

from peek import denylist, permissions, server, windows
from peek.permissions import (
    APP_BUNDLE_PATH,
    BUNDLE_BINARY_PATH,
)

logger = logging.getLogger(__name__)


def _user_home() -> Path:
    """Return invoking user's home, even when running under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


def _user_uid_gid() -> tuple[int, int] | None:
    """Return (uid, gid) of the invoking user when running under sudo, else None."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            pw = pwd.getpwnam(sudo_user)
            return (pw.pw_uid, pw.pw_gid)
        except KeyError:
            pass
    return None


def _cli_symlink_path() -> Path:
    """Resolve the CLI symlink path against the invoking user's $HOME.

    Under sudo, ``Path.home()`` returns ``/var/root``; the user's actual
    bin dir lives under ``SUDO_USER``'s home.
    """
    return _user_home() / ".local" / "bin" / "peek-mcp"


def _ensure_dir_owned_by_user(path: Path, owner: tuple[int, int] | None) -> None:
    """mkdir -p path. Chown to owner only directories we just created."""
    created: list[Path] = []
    p = path
    while not p.exists():
        created.append(p)
        p = p.parent
    path.mkdir(parents=True, exist_ok=True)
    if owner:
        for d in created:
            try:
                os.chown(d, owner[0], owner[1])
            except OSError:
                pass  # best-effort


def _claude_config_path() -> Path:
    """Return the path to the invoking user's ``~/.claude.json``."""
    return _user_home() / ".claude.json"


def _add_peek_to_claude_config(symlink_path: Path) -> str:
    """Add or update the peek MCP server entry in ~/.claude.json.

    Returns one of:
      'created'           file did not exist; created with peek entry
      'added'             file existed; mcpServers.peek added
      'updated'           file existed; mcpServers.peek command path updated
      'unchanged'         file existed; peek already at the desired path
      'malformed_skipped' file existed but was not valid JSON / not an object;
                          left untouched, caller prints manual instructions
    """
    config_path = _claude_config_path()
    desired = {"command": str(symlink_path)}

    if config_path.exists():
        try:
            raw = config_path.read_text()
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            logger.warning("%s has invalid JSON (%s); not modified", config_path, exc)
            return "malformed_skipped"
        existed = True
    else:
        data = {}
        existed = False

    if not isinstance(data, dict):
        logger.warning("%s root is not a JSON object; not modified", config_path)
        return "malformed_skipped"

    mcp_servers = data.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        data["mcpServers"] = mcp_servers
    elif not isinstance(mcp_servers, dict):
        logger.warning("%s mcpServers is not an object; not modified", config_path)
        return "malformed_skipped"

    existing = mcp_servers.get("peek")
    if existing == desired:
        return "unchanged"

    if "peek" in mcp_servers:
        status = "updated"
    elif existed:
        status = "added"
    else:
        status = "created"

    mcp_servers["peek"] = desired

    # atomic write
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(config_path)

    owner = _user_uid_gid()
    if owner is not None:
        try:
            os.chown(config_path, owner[0], owner[1])
        except OSError:
            pass

    return status


def _display(p: Path) -> str:
    """Render a path with ``~`` for the invoking user's home prefix."""
    s = str(p)
    home = str(_user_home())
    if s == home:
        return "~"
    if s.startswith(home + "/"):
        return "~" + s[len(home):]
    return s


# --- doctor ---------------------------------------------------------------


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("peek doctor")
    print("-" * 40)

    trusted = permissions.is_trusted()

    cli_symlink = _cli_symlink_path()
    bundle_present = APP_BUNDLE_PATH.exists()
    binary_present = BUNDLE_BINARY_PATH.exists()
    symlink_present = cli_symlink.is_symlink() or cli_symlink.exists()

    print(
        f"app bundle:        {_display(APP_BUNDLE_PATH)}  "
        f"[{'present' if bundle_present else 'MISSING'}]"
    )

    drift = permissions.check_path_drift()
    if binary_present:
        short_hash = drift["current_hash"][:16] if drift["current_hash"] else "?"
        print(
            f"bundle binary:     {_display(BUNDLE_BINARY_PATH)}  "
            f"[hash:{short_hash}...]"
        )
    else:
        print(
            f"bundle binary:     {_display(BUNDLE_BINARY_PATH)}  [MISSING]"
        )

    print(
        f"cli symlink:       {_display(cli_symlink)}  "
        f"[{'present' if symlink_present else 'MISSING'}]"
    )

    print(f"AX trust:          {'GRANTED' if trusted else 'NOT GRANTED'}")

    if drift["prior_path"]:
        print()
        print(f"prior trusted path: {drift['prior_path']}")
        print(f"prior sha256:       {drift['prior_hash'][:16] if drift['prior_hash'] else '?'}...")
        if drift["drifted"]:
            print("WARNING: trust may have been silently revoked.")
            print("         The .app bundle's binary changed since the last grant.")
            print("         Re-grant Accessibility access to:")
            print(f"             {_display(APP_BUNDLE_PATH)}")
        else:
            print("trust state:        unchanged since last run")

    user_dl = denylist.user_denylist_path()
    print()
    print(f"user denylist:      {user_dl} {'(present)' if user_dl.exists() else '(will be installed on first run)'}")

    if not bundle_present:
        print()
        print("The Peek.app bundle is not installed yet.")
        print("Build the bundle then install it:")
        print("    ./build.sh && ./dist/peek-mcp install")
        return 1

    if not trusted:
        print()
        print("Opening System Settings -> Privacy & Security -> Accessibility...")
        print(f"Drag {_display(APP_BUNDLE_PATH)} into the list (or click + and select it).")
        permissions.open_settings_pane()
        return 1

    # Persist the success record.
    permissions.record_trusted_state()
    print("Trust state recorded for drift detection.")
    return 0


# --- list -----------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    dl = denylist.load()

    def _is_sens(bid, app):
        return denylist.matches(bid, app, dl)

    win_list = windows.list_windows(
        on_screen_only=not args.all,
        is_sensitive=_is_sens,
    )
    if args.json:
        json.dump(win_list, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    print(f"{'WID':>7}  {'PID':>6}  {'F':1} {'S':1} {'APP':<24} {'TITLE'}")
    print("-" * 80)
    for w in win_list:
        focused_marker = "*" if w["focused"] else " "
        sensitive_marker = "!" if w["sensitive"] else " "
        title = (w["title"] or "")[:60]
        app = (w["app"] or "")[:24]
        print(
            f"{w['window_id']:>7}  {w['pid']:>6}  {focused_marker} {sensitive_marker} {app:<24} {title}"
        )
    return 0


# --- read -----------------------------------------------------------------


def cmd_read(args: argparse.Namespace) -> int:
    selectors = sum(
        bool(v)
        for v in (args.window_id, args.app, args.focused, args.contains)
    )
    if selectors == 0:
        print("error: pass exactly one of --window-id, --app, --focused, --contains", file=sys.stderr)
        return 2

    async def _go() -> dict:
        return await server.read_window_impl(
            window_id=args.window_id,
            app=args.app,
            title_match=args.title_match,
            focused=bool(args.focused),
            contains=args.contains,
            grep=args.grep,
            regex=bool(args.regex),
            case_sensitive=bool(args.case_sensitive),
            context_lines=args.context_lines,
            max_matches=args.max_matches,
            max_chars=args.max_chars,
            max_depth=args.max_depth,
            max_elements=args.max_elements,
            max_time_seconds=args.max_time_seconds,
            allow_sensitive=bool(args.allow_sensitive),
        )

    result = asyncio.run(_go())

    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        if "message" in result:
            print(f"  {result['message']}", file=sys.stderr)
        if "candidates" in result:
            print("  candidates:", file=sys.stderr)
            for c in result["candidates"]:
                print(f"    {c}", file=sys.stderr)
        if "instructions" in result:
            print(f"  {result['instructions']}", file=sys.stderr)
        return 1

    if "redacted_app" in result:
        print(f"REDACTED: {result['redacted_app']} ({result['reason']})")
        print("Pass --allow-sensitive to override.")
        return 0

    if "matches" in result:
        for m in result["matches"]:
            print(f"--- L{m['line_number']}")
            for line in m["context"]:
                print(line)
        if result.get("truncation", {}).get("truncated_at_match_limit"):
            print(f"... (match limit hit at {args.max_matches})")
        return 0

    if "text" in result:
        sys.stdout.write(result["text"])
        sys.stdout.write("\n")
        if result.get("truncation"):
            print(f"truncation: {result['truncation']}", file=sys.stderr)
        return 0

    return 0


# --- install --------------------------------------------------------------


def _resolve_source_bundle() -> Path | None:
    """Find the Peek.app to install, given how this binary was launched.

    Two valid launch contexts:

    1. Running from inside an existing Peek.app — the binary is at
       ``<bundle>/Contents/MacOS/peek-mcp``. Walk up from
       ``sys.executable`` looking for a ``.app`` ancestor.
    2. Running from a freshly-built ``dist/peek-mcp`` raw binary —
       the sibling ``dist/Peek.app`` is the bundle to install.

    Returns the bundle Path on success, or ``None`` if neither applies.
    """
    exe = Path(sys.executable).resolve()

    # Case 1: walk up looking for a *.app ancestor whose Contents/MacOS
    # directory contains us. This makes "install from the installed
    # bundle" a no-op that still drops/refreshes the symlink.
    for ancestor in exe.parents:
        if ancestor.suffix == ".app" and ancestor.is_dir():
            return ancestor

    # Case 2: dist/peek-mcp + sibling dist/Peek.app
    sibling = exe.parent / "Peek.app"
    if sibling.is_dir():
        return sibling

    return None


def cmd_install(args: argparse.Namespace) -> int:
    """Install the Peek.app bundle to /Applications and create the CLI symlink.

    Frozen-mode only: AX trust must attach to the actual Mach-O binary
    inside a .app bundle, so we refuse to "install" from a dev-mode
    Python interpreter.

    Writing to ``/Applications/`` requires root, so the user is expected
    to invoke this via sudo. We honor ``SUDO_USER`` for the symlink
    target (their home, not ``/var/root``) and chown anything we create
    under their home back to them.
    """
    is_frozen = bool(getattr(sys, "frozen", False))
    if not is_frozen:
        print(
            "error: `peek install` only works when run from the built binary.",
            file=sys.stderr,
        )
        print(
            "       Build the .app first via ./build.sh, then run "
            "`./dist/peek-mcp install`.",
            file=sys.stderr,
        )
        return 2

    source_bundle = _resolve_source_bundle()
    if source_bundle is None:
        print(
            "error: could not locate a Peek.app bundle to install.",
            file=sys.stderr,
        )
        print(
            "       Build the .app first via ./build.sh, then run "
            "`./dist/peek-mcp install`.",
            file=sys.stderr,
        )
        return 2

    target_bundle = (
        Path(args.path).expanduser() if args.path else APP_BUNDLE_PATH
    )

    owner = _user_uid_gid()  # (uid, gid) under sudo, else None
    cli_symlink = _cli_symlink_path()

    try:
        target_bundle.parent.mkdir(parents=True, exist_ok=True)

        # If we're already running from inside the target bundle (the
        # "re-install from installed copy" path), there's nothing to copy —
        # just refresh the symlink.
        same_bundle = False
        try:
            same_bundle = source_bundle.resolve() == target_bundle.resolve()
        except FileNotFoundError:
            same_bundle = False

        if same_bundle:
            print(f"already installed at: {target_bundle}")
        else:
            # Wipe-and-copy: a stale Contents/MacOS/peek-mcp from a prior
            # install would otherwise be retained by copytree's
            # dirs_exist_ok merge semantics.
            if target_bundle.exists() or target_bundle.is_symlink():
                if target_bundle.is_symlink() or target_bundle.is_file():
                    target_bundle.unlink()
                else:
                    shutil.rmtree(target_bundle)
            shutil.copytree(source_bundle, target_bundle, symlinks=True)
            print(f"installed: {target_bundle}")
    except PermissionError:
        print(
            f"error: cannot write to {target_bundle.parent} (permission denied).",
            file=sys.stderr,
        )
        print("       Re-run with sudo:", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"           sudo {sys.executable} install", file=sys.stderr)
        return 1

    # CLI symlink. Replace any existing entry (regular file or symlink)
    # at the literal target path before recreating. Chown freshly-created
    # parent dirs to the invoking user so they aren't left root-owned.
    target_binary = target_bundle / "Contents" / "MacOS" / "peek-mcp"
    _ensure_dir_owned_by_user(cli_symlink.parent, owner)
    if cli_symlink.is_symlink() or cli_symlink.exists():
        cli_symlink.unlink()
    cli_symlink.symlink_to(target_binary)
    if owner:
        try:
            os.lchown(cli_symlink, owner[0], owner[1])
        except OSError:
            pass  # best-effort
    print(f"symlink:   {cli_symlink} -> {target_binary}")

    # Auto-configure ~/.claude.json with the peek MCP entry, unless the
    # user opted out. The CLI symlink path is what we register: it
    # resolves to the bundle binary, so TCC follows it correctly.
    config_status = "skipped"
    config_path = _claude_config_path()
    if not getattr(args, "skip_claude_config", False):
        config_status = _add_peek_to_claude_config(cli_symlink)
        if config_status in ("created", "added"):
            print(f"{_display(config_path)}: added peek MCP server entry")
        elif config_status == "updated":
            print(f"{_display(config_path)}: updated peek command path")
        elif config_status == "unchanged":
            print(f"{_display(config_path)}: peek already configured")
        elif config_status == "malformed_skipped":
            print(
                f"{_display(config_path)} has invalid JSON; could not auto-configure peek.",
                file=sys.stderr,
            )
            print('Add this block manually under "mcpServers":', file=sys.stderr)
            print(f'    "peek": {{ "command": "{cli_symlink}" }}', file=sys.stderr)

    print()
    print("Next steps:")
    print("  1. Open System Settings -> Privacy & Security -> Accessibility.")
    print(f"  2. Click + and select Peek.app from Applications (or drag {target_bundle} in).")
    print("  3. Enable the toggle.")
    print("  4. Run: peek-mcp doctor")
    print("  5. Restart Claude Code, run /mcp -- peek should appear with two tools.")
    if config_status == "malformed_skipped":
        print(
            f"     (Reminder: {_display(config_path)} had invalid JSON; "
            "add the peek block manually as shown above.)"
        )
    return 0


# --- argparse plumbing ----------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="peek", description="macos-peek-mcp CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="Check AX trust state and bundle hash")
    p_doctor.set_defaults(func=cmd_doctor)

    p_list = sub.add_parser("list", help="List visible windows")
    p_list.add_argument("--all", action="store_true", help="Include off-screen windows")
    p_list.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    p_list.set_defaults(func=cmd_list)

    p_read = sub.add_parser("read", help="Read text from a window")
    g = p_read.add_mutually_exclusive_group()
    g.add_argument("--window-id", type=int, help="Read by CGWindowID")
    g.add_argument("--app", help="Read by app name (use --title-match to disambiguate)")
    g.add_argument("--focused", action="store_true", help="Read the focused window")
    g.add_argument("--contains", help="Find first window whose text contains this")
    p_read.add_argument("--title-match", help="Substring to disambiguate when using --app")
    p_read.add_argument("--grep", help="Filter to lines matching this pattern")
    p_read.add_argument("--regex", action="store_true", help="Interpret --grep as a regex")
    p_read.add_argument("--case-sensitive", action="store_true")
    p_read.add_argument("--context-lines", type=int, default=2)
    p_read.add_argument("--max-matches", type=int, default=50)
    p_read.add_argument("--max-chars", type=int, default=200_000)
    p_read.add_argument("--max-depth", type=int, default=50)
    p_read.add_argument("--max-elements", type=int, default=50_000)
    p_read.add_argument("--max-time-seconds", type=float, default=3.0)
    p_read.add_argument("--allow-sensitive", action="store_true",
                        help="Bypass denylist for the resolved app")
    p_read.add_argument("--json", action="store_true")
    p_read.set_defaults(func=cmd_read)

    p_install = sub.add_parser(
        "install",
        help=(
            f"Install Peek.app to {APP_BUNDLE_PATH} "
            f"and create CLI symlink at {_display(_cli_symlink_path())} "
            f"(requires sudo for /Applications/)"
        ),
    )
    p_install.add_argument(
        "--path",
        help=f"Override .app install location (default {APP_BUNDLE_PATH})",
    )
    p_install.add_argument(
        "--skip-claude-config",
        action="store_true",
        help="Don't touch ~/.claude.json (advanced; default is to auto-add the peek MCP entry)",
    )
    p_install.set_defaults(func=cmd_install)

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    rc = args.func(args)
    sys.exit(rc if rc is not None else 0)


if __name__ == "__main__":
    main()

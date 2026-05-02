"""`peek` CLI: `doctor`, `list`, `read`, `install` subcommands.

Used for manual development against the live AX API and to install the
PyInstaller-built binary at the stable path AX trust is granted to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from peek import denylist, permissions, server, windows
from peek.permissions import INSTALL_PATH


# --- doctor ---------------------------------------------------------------


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("peek doctor")
    print("-" * 40)

    trusted = permissions.is_trusted()
    print(f"AX trust:           {'GRANTED' if trusted else 'NOT GRANTED'}")

    drift = permissions.check_path_drift()
    print(f"current binary:     {drift['current_path']}")
    print(f"current sha256:     {drift['current_hash'][:16]}...")
    if drift["prior_path"]:
        print(f"prior trusted path: {drift['prior_path']}")
        print(f"prior sha256:       {drift['prior_hash'][:16] if drift['prior_hash'] else '?'}...")
        if drift["drifted"]:
            print("WARNING: trust may have been silently revoked.")
            print("         Re-grant Accessibility access to the binary at:")
            print(f"             {INSTALL_PATH}")
        else:
            print("trust state:        unchanged since last run")

    install_exists = INSTALL_PATH.exists()
    print(
        f"install ({INSTALL_PATH}): "
        f"{'present' if install_exists else 'MISSING — run `peek-mcp install`'}"
    )

    user_dl = denylist.user_denylist_path()
    print(f"user denylist:      {user_dl} {'(present)' if user_dl.exists() else '(will be installed on first run)'}")

    if not trusted:
        print()
        print("Opening System Settings → Privacy & Security → Accessibility...")
        print(f"Add the binary path to the list: {INSTALL_PATH}")
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


def cmd_install(args: argparse.Namespace) -> int:
    """Copy the running PyInstaller binary to ~/.local/bin/peek-mcp.

    Frozen-mode only: AX trust must attach to the actual Mach-O binary,
    so we refuse to "install" from a dev-mode Python interpreter.
    """
    target = Path(args.path).expanduser() if args.path else INSTALL_PATH
    is_frozen = bool(getattr(sys, "frozen", False))
    if not is_frozen:
        print(
            "error: `peek install` only works when run from the built binary.",
            file=sys.stderr,
        )
        print(
            "       build the binary first via ./build.sh, then run "
            "`./dist/peek-mcp install`.",
            file=sys.stderr,
        )
        return 2

    source = Path(sys.executable).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Resolve through symlinks for the equality check, but always remove
    # any existing entry (regular file or symlink) at the literal target
    # path before copying. Otherwise shutil.copy2 would follow a stale
    # symlink (e.g. from a prior `uv tool install`) and write into the
    # symlink's target — leaving the install path as a symlink to a
    # different binary, which would break AX trust assumptions.
    same_path = False
    try:
        same_path = source.resolve() == target.resolve()
    except FileNotFoundError:
        same_path = False
    if same_path:
        print(f"already installed at: {target}")
    else:
        if target.is_symlink() or target.exists():
            target.unlink()
        shutil.copy2(source, target)
        print(f"installed: {target}")
    target.chmod(0o700)  # rwx user only; AX-trusted binary, no group/other access
    print()
    print("Next step: grant Accessibility access to this exact path:")
    print(f"  {target}")
    print("In System Settings → Privacy & Security → Accessibility, click +")
    print("and select the binary. (Spotlight won't find it — use Cmd+Shift+G")
    print("in the file picker and paste the path.)")
    return 0


# --- argparse plumbing ----------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="peek", description="macos-peek-mcp CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="Check AX trust state and binary hash")
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
        help=f"Copy this binary to {INSTALL_PATH} (the AX-trusted path)",
    )
    p_install.add_argument("--path", help=f"Override install location (default {INSTALL_PATH})")
    p_install.set_defaults(func=cmd_install)

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    rc = args.func(args)
    sys.exit(rc if rc is not None else 0)


if __name__ == "__main__":
    main()

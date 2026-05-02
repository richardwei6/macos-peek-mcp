"""Single entry point for the `peek-mcp` binary.

The frozen PyInstaller binary at `~/.local/bin/peek-mcp` is the one and
only artifact that AX trust is granted to. We can't ship two binaries
(the kernel attaches AX trust to the binary it actually exec's), so this
module dispatches on `sys.argv` to support both modes:

- `peek-mcp` (no args)              -> MCP stdio server (peek.server.main)
- `peek-mcp doctor|list|read|install` -> CLI (peek.cli.main)

When invoked with one of the CLI verbs, we hand argv off to peek.cli.main
unchanged; argparse there handles the parsing. Otherwise, we delegate to
peek.server.main.
"""

from __future__ import annotations

import sys


CLI_COMMANDS = frozenset({"doctor", "list", "read", "install"})


def main() -> None:
    """Dispatch entry: CLI verb -> CLI, otherwise -> MCP server."""
    if len(sys.argv) >= 2 and sys.argv[1] in CLI_COMMANDS:
        from peek import cli
        cli.main()
        return
    from peek import server
    server.main()


if __name__ == "__main__":
    main()

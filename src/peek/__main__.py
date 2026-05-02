"""PyInstaller entry script for the `peek-mcp` binary.

The single-file binary's bootstrap calls into this module's top-level
code at process start. Delegates to `peek.entry.main`, which dispatches
between MCP server mode and CLI mode.

Also enables `python -m peek` to do the same dispatch in dev mode.
"""

from __future__ import annotations

from peek.entry import main

if __name__ == "__main__":
    main()

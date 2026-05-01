"""macos-peek-mcp — read-only window text MCP for macOS.

Exposes two MCP tools (`list_windows`, `read_window`) over stdio that let
an MCP client retrieve text from other macOS windows via the Accessibility
(AX) API. See README.md for installation and the threat model.
"""

__version__ = "0.1.0"

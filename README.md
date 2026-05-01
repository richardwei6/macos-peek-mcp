# macos-peek-mcp

Local stdio MCP server that exposes text from other macOS windows via the
Accessibility (AX) API. Built so an MCP client (e.g. Claude Code) can grep
your Terminal / Console / log viewer windows directly instead of round-tripping
through screenshots + OCR.

See the design doc and implementation plan for context. README is filled out
in step 11 of the implementation plan.

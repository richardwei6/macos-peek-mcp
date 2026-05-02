#!/usr/bin/env bash
# Build the peek-mcp Mach-O binary via PyInstaller.
#
# Output: dist/peek-mcp (single-file, ad-hoc signed).
# Next:   ./dist/peek-mcp install  # copy to ~/.local/bin/peek-mcp
set -euo pipefail

cd "$(dirname "$0")"

# Install dev deps (includes pyinstaller).
uv sync --group dev

# Build single-file binary.
uv run pyinstaller peek-mcp.spec --clean --noconfirm

# Ad-hoc codesign so macOS doesn't refuse to launch it. AX trust is
# granted to the binary at its install path, so the signature only needs
# to be valid (not an Apple Developer ID). The kernel records the
# Identifier (peek-mcp) and the cdhash, both of which come from this
# step.
codesign --force --sign - --identifier peek-mcp dist/peek-mcp

echo "Built: dist/peek-mcp ($(du -sh dist/peek-mcp | cut -f1))"
echo "Run:   ./dist/peek-mcp install   # copies to ~/.local/bin/peek-mcp"

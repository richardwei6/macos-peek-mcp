#!/usr/bin/env bash
# Build the peek-mcp Mach-O binary AND the Peek.app bundle via PyInstaller.
#
# Outputs:
#   dist/peek-mcp   single-file Mach-O, ad-hoc signed (raw binary, dev only)
#   dist/Peek.app   .app bundle wrapping that binary, ad-hoc signed
#
# Next: ./dist/peek-mcp install
#   copies the bundle to ~/Applications/Peek.app and drops a CLI
#   symlink at ~/.local/bin/peek-mcp -> the bundle's binary.
set -euo pipefail

cd "$(dirname "$0")"

# Install dev deps (includes pyinstaller).
uv sync --group dev

# Clean stale dist/build state — PyInstaller's BUNDLE step occasionally
# leaves a half-formed Peek.app behind that confuses subsequent builds.
rm -rf dist build

# Build single-file binary AND Peek.app.
uv run pyinstaller peek-mcp.spec --clean --noconfirm

# Ad-hoc codesign the raw binary so direct invocation isn't refused.
# AX trust is granted to the .app bundle (see below); this signature on
# the standalone binary only needs to be valid (not Apple Developer ID).
codesign --force --sign - --identifier peek-mcp dist/peek-mcp

# Ad-hoc codesign the bundle. The bundle identifier here is what TCC
# records — keep it in sync with peek-mcp.spec's bundle_identifier.
# --deep covers the embedded Mach-O inside Contents/MacOS/.
codesign --deep --force --sign - \
    --identifier com.richardwei6.macos-peek-mcp \
    dist/Peek.app
codesign --verify --verbose dist/Peek.app

echo ""
echo "Built dist/Peek.app   ($(du -sh dist/Peek.app | cut -f1))"
echo "Built dist/peek-mcp   ($(du -sh dist/peek-mcp | cut -f1), raw binary, dev only)"
echo ""
echo "Run: ./dist/peek-mcp install   # installs Peek.app to ~/Applications/, creates ~/.local/bin/peek-mcp symlink"

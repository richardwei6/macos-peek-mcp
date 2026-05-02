#!/usr/bin/env bash
# Build the Peek.app bundle (PyInstaller --onedir) and ad-hoc-codesign it.
#
# Output:
#   dist/Peek.app   .app bundle, ad-hoc signed.
#                   Binary at dist/Peek.app/Contents/MacOS/peek-mcp.
#
# Why --onedir: macOS TCC (Accessibility) matches on the running
# process's bundle context. PyInstaller --onefile self-extracts to
# /var/folders/.../_MEIxxxxx/ and re-execs, breaking that match.
# --onedir runs the binary in-place inside the .app, so AX trust
# granted to /Applications/Peek.app actually applies.
#
# Next: sudo ./dist/Peek.app/Contents/MacOS/peek-mcp install
#   copies the bundle to /Applications/Peek.app (sudo for /Applications/)
#   and drops a CLI symlink at ~/.local/bin/peek-mcp -> the bundle's binary.
set -euo pipefail

cd "$(dirname "$0")"

# Install dev deps (includes pyinstaller).
uv sync --group dev

# Clean stale dist/build state — PyInstaller's BUNDLE step occasionally
# leaves a half-formed Peek.app behind that confuses subsequent builds.
rm -rf dist build

# Build Peek.app via --onedir + BUNDLE.
uv run pyinstaller peek-mcp.spec --clean --noconfirm

# Ad-hoc codesign the bundle. The bundle identifier here is what TCC
# records — keep it in sync with peek-mcp.spec's bundle_identifier.
# --deep covers the embedded Mach-O inside Contents/MacOS/.
codesign --deep --force --sign - \
    --identifier com.richardwei6.macos-peek-mcp \
    dist/Peek.app
codesign --verify --verbose dist/Peek.app

echo ""
echo "Built dist/Peek.app   ($(du -sh dist/Peek.app | cut -f1))"
echo ""
echo "Run: sudo ./dist/Peek.app/Contents/MacOS/peek-mcp install"
echo "     # installs Peek.app to /Applications/, creates ~/.local/bin/peek-mcp symlink"

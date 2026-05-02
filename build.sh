#!/usr/bin/env bash
# Build the Peek.app bundle (PyInstaller --onedir) and codesign it with a
# local Apple Development identity (hardened runtime + entitlements).
#
# Output:
#   dist/Peek.app   .app bundle, signed with a real Team Identifier.
#                   Binary at dist/Peek.app/Contents/MacOS/peek-mcp.
#
# Why a real signing identity (not ad-hoc):
#   macOS TCC (Accessibility) on Sonoma 14.4+ / Sequoia attaches AX trust
#   at the kernel cdhash + Team Identifier level. Ad-hoc signed bundles
#   have no Team Identifier, and TCC silently denies AX grants for them
#   even when the bundle layout is correct. Using a free Apple Development
#   identity (Personal Team) yields a real Team Identifier so the AX
#   grant actually sticks.
#
# Why --onedir: macOS TCC matches on the running process's bundle context.
# PyInstaller --onefile self-extracts to /var/folders/.../_MEIxxxxx/ and
# re-execs, breaking that match. --onedir runs the binary in-place inside
# the .app, so AX trust granted to /Applications/Peek.app actually applies.
#
# Identity selection:
#   $PEEK_CODESIGN_IDENTITY     full identity string (verbatim), if set
#   else                         first "Apple Development:" entry from
#                                `security find-identity -v -p codesigning`
#
# Next: sudo ./dist/Peek.app/Contents/MacOS/peek-mcp install
#   copies the bundle to /Applications/Peek.app (sudo for /Applications/)
#   and drops a CLI symlink at ~/.local/bin/peek-mcp -> the bundle's binary.
set -euo pipefail

cd "$(dirname "$0")"

# --- output helpers ---------------------------------------------------------
if [ -t 1 ]; then
    BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi
info() { printf "${BLUE}==>${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}  ok${NC}  %s\n" "$1"; }
fail() { printf "${RED}  xx${NC}  %s\n" "$1" >&2; exit 1; }

# --- resolve signing identity ----------------------------------------------
NO_IDENTITY_HELP="No Apple Development codesigning identity found.

  Set up a free Personal Team in Xcode (no paid membership required):
    1. Open Xcode
    2. Xcode -> Settings -> Accounts -> + -> Apple ID
    3. Sign in, select your team -> Manage Certificates -> + -> Apple Development
    4. Re-run ./build.sh

  Reference: https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases

  Override with an explicit identity:
    PEEK_CODESIGN_IDENTITY='Apple Development: you@example.com (TEAMID)' ./build.sh
"

if [ -n "${PEEK_CODESIGN_IDENTITY:-}" ]; then
    IDENTITY="$PEEK_CODESIGN_IDENTITY"
else
    # Pick the first "Apple Development:" line; extract the quoted human-readable
    # identity (not the SHA hash). `security find-identity` output looks like:
    #   1) <SHA> "Apple Development: name (TEAMID)"
    IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
        | awk -F'"' '/Apple Development:/ {print $2; exit}')"
    if [ -z "$IDENTITY" ]; then
        fail "$NO_IDENTITY_HELP"
    fi
fi

info "signing with: $IDENTITY"

# Install dev deps (includes pyinstaller).
uv sync --group dev

# Clean stale dist/build state - PyInstaller's BUNDLE step occasionally
# leaves a half-formed Peek.app behind that confuses subsequent builds.
rm -rf dist build

# Build Peek.app via --onedir + BUNDLE.
uv run pyinstaller peek-mcp.spec --clean --noconfirm

# Codesign the bundle with the resolved Apple Development identity.
#
#   --options runtime    enables hardened runtime (required for Notarization-
#                        adjacent flows; also satisfies modern TCC strictness)
#   --entitlements ...   PyInstaller's bootloader loads libpython + bundled .so
#                        files, which trips hardened-runtime library validation
#                        and JIT/exec-memory checks. The three entitlements
#                        (allow-jit, allow-unsigned-executable-memory,
#                        disable-library-validation) are the standard set for
#                        a PyInstaller-built app with hardened runtime.
#   --timestamp=none     keep the build offline; we're not Notarizing
#   --deep               also signs nested binaries inside the bundle
#   --force              overwrite any existing signature on disk
codesign --force --deep \
    --sign "$IDENTITY" \
    --options runtime \
    --entitlements entitlements.plist \
    --timestamp=none \
    dist/Peek.app

# Verify strictly. Fails loudly if anything is off.
codesign --verify --strict --verbose=2 dist/Peek.app
ok "signature verified"

# Surface the Team Identifier so the user can confirm a real team landed
# (not "not set", which is what ad-hoc signing produces).
TEAM_LINE="$(codesign -dv dist/Peek.app 2>&1 | grep -E '^TeamIdentifier=' || true)"
if [ -n "$TEAM_LINE" ]; then
    ok "$TEAM_LINE"
else
    fail "codesign reported no TeamIdentifier - signing didn't attach a team. Check the identity."
fi

echo ""
echo "Built dist/Peek.app   ($(du -sh dist/Peek.app | cut -f1))"
echo ""
echo "Run: sudo ./dist/Peek.app/Contents/MacOS/peek-mcp install"
echo "     # installs Peek.app to /Applications/, creates ~/.local/bin/peek-mcp symlink"

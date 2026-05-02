#!/usr/bin/env bash
#
# macos-peek-mcp installer.
#
#   curl -fsSL https://raw.githubusercontent.com/richardwei6/macos-peek-mcp/main/install.sh | bash
#
# Clones the repo, builds the Peek.app bundle (and a raw Mach-O binary)
# with PyInstaller, ad-hoc-codesigns both, installs the bundle to
# /Applications/Peek.app (one sudo prompt), and creates a CLI symlink at
# ~/.local/bin/peek-mcp. Idempotent — re-running updates source and rebuilds.

set -euo pipefail

REPO_URL="https://github.com/richardwei6/macos-peek-mcp.git"
INSTALL_DIR="${HOME}/.local/share/macos-peek-mcp"
SRC_DIR="${INSTALL_DIR}/src"
BUNDLE_PATH="/Applications/Peek.app"
BIN_PATH="${HOME}/.local/bin/peek-mcp"

# --- output helpers ---------------------------------------------------------
if [ -t 1 ]; then
    BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi
info() { printf "${BLUE}==>${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}  ok${NC}  %s\n" "$1"; }
warn() { printf "${YELLOW}  !!${NC}  %s\n" "$1" >&2; }
fail() { printf "${RED}  xx${NC}  %s\n" "$1" >&2; exit 1; }

echo ""
printf "${BOLD}macos-peek-mcp installer${NC}\n"
echo "----------------------------------------"
echo ""

# --- preflight --------------------------------------------------------------
info "checking prerequisites"

[ "$(uname)" = "Darwin" ] || fail "macos-peek-mcp is macOS only (got $(uname))"

# macOS version (need 13+)
MACOS_VER=$(sw_vers -productVersion 2>/dev/null || echo "0.0")
MACOS_MAJOR=${MACOS_VER%%.*}
if [ "${MACOS_MAJOR:-0}" -lt 13 ]; then
    warn "macOS ${MACOS_VER} detected; this is built against macOS 13+. Continuing anyway."
fi

command -v git >/dev/null 2>&1 || fail "git not found. Install Xcode Command Line Tools: xcode-select --install"
command -v codesign >/dev/null 2>&1 || fail "codesign not found. Install Xcode Command Line Tools: xcode-select --install"

if ! command -v uv >/dev/null 2>&1; then
    warn "uv (https://docs.astral.sh/uv/) is required and not installed."
    echo ""
    echo "  Install uv with:"
    echo ""
    echo "      curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    fail "re-run this installer after uv is on your \$PATH"
fi

ok "macOS ${MACOS_VER}, git, codesign, uv $(uv --version 2>/dev/null | awk '{print $2}')"

# --- clone or update --------------------------------------------------------
mkdir -p "$INSTALL_DIR"

if [ -d "$SRC_DIR/.git" ]; then
    info "updating existing source at $SRC_DIR"
    git -C "$SRC_DIR" fetch --quiet origin main
    git -C "$SRC_DIR" reset --hard --quiet origin/main
    ok "source updated to $(git -C "$SRC_DIR" rev-parse --short HEAD)"
else
    info "cloning $REPO_URL into $SRC_DIR"
    git clone --quiet --depth 1 "$REPO_URL" "$SRC_DIR"
    ok "cloned $(git -C "$SRC_DIR" rev-parse --short HEAD)"
fi

# --- build ------------------------------------------------------------------
info "building Peek.app bundle (downloads pyinstaller + pyobjc on first run; takes ~1-2 min)"
cd "$SRC_DIR"
./build.sh
APP_SIZE=$(du -sh dist/Peek.app | cut -f1)
ok "built dist/Peek.app (${APP_SIZE})"

# --- install ----------------------------------------------------------------
info "installing Peek.app to $BUNDLE_PATH and CLI symlink to $BIN_PATH"
if [ -w /Applications ]; then
    ./dist/peek-mcp install
else
    warn "/Applications/ requires sudo. You may be prompted for your password."
    sudo ./dist/peek-mcp install
fi
ok "installed"

# --- next steps -------------------------------------------------------------
echo ""
printf "${BOLD}next steps${NC}\n"
echo "----------------------------------------"
echo ""
echo "  1. Run:"
echo ""
echo "         peek-mcp doctor"
echo ""
echo "     It opens System Settings to the right pane on first run."
echo ""
echo "  2. In System Settings -> Privacy & Security -> Accessibility,"
echo "     click + -- the file picker opens to /Applications/ by default,"
echo "     where Peek.app now lives. Select it."
echo "     (Or drag $BUNDLE_PATH into the list.)"
echo ""
echo "     Enable the toggle. Re-run \`peek-mcp doctor\` -- should report GRANTED."
echo ""
echo "  3. Add to ~/.claude.json (or your MCP client config):"
echo ""
echo "         {"
echo "           \"mcpServers\": {"
echo "             \"peek\": { \"command\": \"$BIN_PATH\" }"
echo "           }"
echo "         }"
echo ""
echo "     The symlink at $BIN_PATH resolves to the bundle's binary, so TCC"
echo "     applies trust correctly when MCP clients invoke it."
echo ""
echo "  4. Restart Claude Code; run /mcp -- peek should appear with two tools."
echo ""

# Path warning if ~/.local/bin isn't on PATH
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) warn "${HOME}/.local/bin is not on your \$PATH. Add it to your shell rc:" ;;
esac
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) echo "         export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ""
ok "done"

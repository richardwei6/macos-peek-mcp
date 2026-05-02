#!/usr/bin/env bash
#
# macos-peek-mcp uninstaller.
#
#   curl -fsSL https://raw.githubusercontent.com/richardwei6/macos-peek-mcp/main/uninstall.sh | bash
#
# Removes:
#   /Applications/Peek.app                  (sudo)
#   ~/.local/bin/peek-mcp                   (symlink)
#   ~/.local/share/macos-peek-mcp           (source clone)
#   ~/.config/peek-mcp                      (state + denylist)
#   uv tool entry: macos-peek-mcp           (if present)
#
# Reminds the user to remove Peek from System Settings -> Accessibility
# (TCC has no CLI for revoking grants).

set -euo pipefail

BUNDLE_PATH="/Applications/Peek.app"
BIN_PATH="${HOME}/.local/bin/peek-mcp"
SRC_DIR="${HOME}/.local/share/macos-peek-mcp"
CONFIG_DIR="${HOME}/.config/peek-mcp"
CLAUDE_JSON="${HOME}/.claude.json"

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
printf "${BOLD}macos-peek-mcp uninstaller${NC}\n"
echo "----------------------------------------"
echo ""

# --- preflight --------------------------------------------------------------
[ "$(uname)" = "Darwin" ] || fail "macos-peek-mcp is macOS only (got $(uname))"

# --- summary ---------------------------------------------------------------
mark() {
    # $1 = path, $2 = "file"|"dir"|"any"
    if [ -e "$1" ] || [ -L "$1" ]; then
        printf "  ${GREEN}[present]${NC}  %s\n" "$1"
    else
        printf "  ${YELLOW}[absent ]${NC}  %s\n" "$1"
    fi
}

UV_PRESENT=no
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^macos-peek-mcp '; then
    UV_PRESENT=yes
fi

info "the following will be removed:"
mark "$BUNDLE_PATH"
mark "$BIN_PATH"
mark "$SRC_DIR"
mark "$CONFIG_DIR"
if [ "$UV_PRESENT" = "yes" ]; then
    printf "  ${GREEN}[present]${NC}  uv tool: macos-peek-mcp\n"
else
    printf "  ${YELLOW}[absent ]${NC}  uv tool: macos-peek-mcp\n"
fi
echo ""

# --- remove /Applications/Peek.app -----------------------------------------
if [ -e "$BUNDLE_PATH" ] || [ -L "$BUNDLE_PATH" ]; then
    if [ -w "$BUNDLE_PATH" ] && [ -w "/Applications" ]; then
        info "removing $BUNDLE_PATH"
        rm -rf "$BUNDLE_PATH"
    else
        info "removing $BUNDLE_PATH (sudo required)"
        sudo rm -rf "$BUNDLE_PATH"
    fi
    ok "removed $BUNDLE_PATH"
else
    ok "$BUNDLE_PATH already absent"
fi

# --- remove CLI symlink ----------------------------------------------------
if [ -e "$BIN_PATH" ] || [ -L "$BIN_PATH" ]; then
    info "removing $BIN_PATH"
    rm -f "$BIN_PATH"
    ok "removed $BIN_PATH"
else
    ok "$BIN_PATH already absent"
fi

# --- remove source clone ---------------------------------------------------
if [ -e "$SRC_DIR" ]; then
    info "removing $SRC_DIR"
    rm -rf "$SRC_DIR"
    ok "removed $SRC_DIR"
else
    ok "$SRC_DIR already absent"
fi

# --- remove config + state -------------------------------------------------
if [ -e "$CONFIG_DIR" ]; then
    info "removing $CONFIG_DIR (state.json, denylist.toml if customized)"
    rm -rf "$CONFIG_DIR"
    ok "removed $CONFIG_DIR"
else
    ok "$CONFIG_DIR already absent"
fi

# --- uv tool uninstall (if present) ----------------------------------------
if [ "$UV_PRESENT" = "yes" ]; then
    info "removing uv tool entry: macos-peek-mcp"
    if uv tool uninstall macos-peek-mcp >/dev/null 2>&1; then
        ok "uv tool uninstalled"
    else
        warn "uv tool uninstall failed; remove manually with: uv tool uninstall macos-peek-mcp"
    fi
else
    ok "no uv tool entry to remove"
fi

# --- ~/.claude.json hint (don't auto-edit) --------------------------------
if [ -f "$CLAUDE_JSON" ] && grep -l '"peek"' "$CLAUDE_JSON" >/dev/null 2>&1; then
    echo ""
    warn "$CLAUDE_JSON appears to contain a \"peek\" MCP server entry."
    warn "remove the \"peek\" block from \"mcpServers\" yourself; this script does not edit client config."
fi

# --- TCC reminder ----------------------------------------------------------
echo ""
printf "${BOLD}manual cleanup needed${NC}\n"
echo "----------------------------------------"
echo ""
echo "  TCC (the macOS privacy database) has no CLI for revoking AX grants."
echo "  Open System Settings -> Privacy & Security -> Accessibility,"
echo "  select \"Peek\", click - to revoke."
echo ""

ok "done"

# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the peek-mcp Mach-O binary.
#
# Why a real binary, not a bash shim:
#   macOS AX (TCC) trust attaches to the Mach-O the kernel exec()s. A
#   bash shim => bash => python chain means trust would have to be
#   granted to the venv Python interpreter, which uv re-signs on
#   upgrade (silent revoke) and which has a wide blast radius (any tool
#   that uses the same interpreter inherits AX). Shipping a single
#   ad-hoc-signed Mach-O binary makes the AX grant target the real,
#   stable artifact.
#
# Dispatch:
#   `peek-mcp` (no args)                  -> peek.entry.main -> server.main
#   `peek-mcp doctor|list|read|install`   -> peek.entry.main -> cli.main


block_cipher = None


a = Analysis(
    ["src/peek/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        # Ship the bundled default denylist inside the binary; denylist.py
        # resolves it via sys._MEIPASS when frozen.
        ("src/peek/data/default-denylist.toml", "peek/data"),
    ],
    hiddenimports=[
        # pyobjc framework modules: PyInstaller's pyobjc hook coverage is
        # historically incomplete for ApplicationServices, so we list the
        # ones we touch explicitly.
        "AppKit",
        "ApplicationServices",
        "Quartz",
        "objc",
        # MCP framework: FastMCP pulls a deep dependency graph; declare
        # the entry module so PyInstaller follows it.
        "mcp.server.fastmcp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="peek-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # stdio MCP server: must be a console binary
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)


# Wrap the EXE into a proper macOS .app bundle. Modern macOS TCC keys
# Accessibility trust on bundle ID + bundle cdhash; the Privacy &
# Security → Accessibility pane is built around .app bundles, so
# shipping one is what makes the entry show up natively in the list
# (drag-and-drop from ~/Applications, no Cmd+Shift+G required).
#
# The standalone EXE above is still emitted at dist/peek-mcp for dev /
# testing convenience; the user-facing artifact is dist/Peek.app.
app = BUNDLE(
    exe,
    name="Peek.app",
    icon=None,
    bundle_identifier="com.richardwei6.macos-peek-mcp",
    info_plist={
        "CFBundleName": "Peek",
        "CFBundleDisplayName": "Peek",
        "CFBundleExecutable": "peek-mcp",
        "CFBundleIdentifier": "com.richardwei6.macos-peek-mcp",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        # daemon-style: no Dock icon, no menu bar entry
        "LSUIElement": True,
        "NSAccessibilityUsageDescription": (
            "macos-peek-mcp reads text from other windows on your behalf "
            "to expose to MCP clients (e.g. Claude Code). You will be "
            "prompted for Accessibility access."
        ),
        "LSMinimumSystemVersion": "13.0",
    },
)

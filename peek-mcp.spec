# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the peek-mcp Mach-O binary.
#
# Why a real binary, not a bash shim:
#   macOS AX (TCC) trust attaches to the Mach-O the kernel exec()s. A
#   bash shim => bash => python chain means trust would have to be
#   granted to the venv Python interpreter, which uv re-signs on
#   upgrade (silent revoke) and which has a wide blast radius (any tool
#   that uses the same interpreter inherits AX). Shipping a single
#   Mach-O binary (codesigned by build.sh with the user's local Apple
#   Development identity) makes the AX grant target the real, stable
#   artifact.
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


# --onedir mode (exclude_binaries=True, paired with COLLECT below).
#
# Why not --onefile: PyInstaller's onefile bootloader extracts the
# bundle's payload to /var/folders/.../_MEIxxxxx/ at startup and
# re-execs from there. macOS TCC (Accessibility) on Sonoma 14.4+ and
# Sequoia matches the running process's bundle context strictly — once
# the binary re-execs from the temp dir, it's no longer "inside"
# /Applications/Peek.app from the kernel's POV, and the AX grant
# silently fails to apply.
#
# In --onedir mode, the running binary IS the bundle's binary at
# dist/Peek.app/Contents/MacOS/peek-mcp (no extraction, no re-exec),
# which is what TCC expects.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="peek-mcp",
)


# Wrap the COLLECT into a proper macOS .app bundle. Modern macOS TCC
# keys Accessibility trust on bundle ID + bundle cdhash; the Privacy &
# Security → Accessibility pane is built around .app bundles, so
# shipping one is what makes the entry show up natively in the list
# (drag-and-drop from /Applications, no Cmd+Shift+G required).
app = BUNDLE(
    coll,
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

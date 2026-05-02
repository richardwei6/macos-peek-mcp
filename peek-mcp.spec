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

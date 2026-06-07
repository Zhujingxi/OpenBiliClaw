# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OpenBiliClaw desktop application.

Build:
    pip install pyinstaller
    cd /path/to/OpenBiliClaw
    pyinstaller packaging/openbiliclaw.spec

Output:  dist/OpenBiliClaw/
"""

import os
import platform
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).parent
bundle_version = os.environ.get("OPENBILICLAW_BUNDLE_VERSION", "0.2.0")

# System-tray desktop mode (packaging/entry.py): the app runs as a tray icon
# (Windows system tray / macOS menu bar) with no console window. Bundle pystray
# + Pillow on both; the macOS backend additionally needs pyobjc (Foundation /
# AppKit). Linux isn't a build target, so exclude pystray there.
_tray_hiddenimports = []
_tray_excludes = []
if platform.system() == "Windows":
    _tray_hiddenimports = ["pystray", "pystray._win32", "PIL", "PIL.Image", "PIL.ImageDraw"]
elif platform.system() == "Darwin":
    _tray_hiddenimports = [
        "pystray",
        "pystray._darwin",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "Foundation",
        "AppKit",
        "objc",
        "CoreFoundation",
    ]
else:
    _tray_excludes = ["pystray"]

a = Analysis(
    [str(project_root / "packaging" / "entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[
        (str(project_root / "config.example.toml"), "."),
        # Web UI + first-run setup wizard. app.py serves these via StaticFiles
        # at /web, /m, /setup; without bundling them those routes 404 in the
        # packaged app. Dest mirrors the import path so __file__-relative
        # resolution (web_dir = .../openbiliclaw/web) works when frozen.
        (str(project_root / "src" / "openbiliclaw" / "web"), "openbiliclaw/web"),
    ],
    hiddenimports=[
        # --- FastAPI / Uvicorn ---
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.middleware",
        "starlette.middleware.cors",
        "anyio",
        "anyio._backends._asyncio",
        # --- HTTP / networking ---
        "httpx",
        "httpcore",
        "h11",
        "certifi",
        "idna",
        "sniffio",
        # --- Pydantic ---
        "pydantic",
        "pydantic_core",
        "annotated_types",
        # --- LLM providers ---
        "openai",
        "anthropic",
        "google.genai",
        "google.generativeai",
        # --- Bilibili ---
        "bilibili_api",
        # --- Database ---
        "sqlite3",
        # --- Scheduling ---
        "apscheduler",
        "apscheduler.schedulers.asyncio",
        # --- CLI (not used at runtime but imported) ---
        "typer",
        "rich",
        "click",
        # --- Config ---
        "tomllib",
        # --- Internal modules ---
        "openbiliclaw",
        "openbiliclaw.api",
        "openbiliclaw.api.app",
        "openbiliclaw.api.models",
        "openbiliclaw.config",
        "openbiliclaw.cli",
        "openbiliclaw.llm",
        "openbiliclaw.soul",
        "openbiliclaw.soul.engine",
        "openbiliclaw.soul.dialogue",
        "openbiliclaw.discovery",
        "openbiliclaw.discovery.engine",
        "openbiliclaw.recommendation",
        "openbiliclaw.recommendation.engine",
        "openbiliclaw.memory",
        "openbiliclaw.memory.manager",
        "openbiliclaw.storage",
        "openbiliclaw.storage.database",
        "openbiliclaw.runtime",
        "openbiliclaw.runtime.refresh",
        "openbiliclaw.runtime.events",
        "openbiliclaw.runtime.account_sync",
        "openbiliclaw.runtime.updater",
        "openbiliclaw.bilibili",
        "openbiliclaw.bilibili.api",
        "openbiliclaw.bilibili.auth",
    ]
    + _tray_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
        "xmlrpc",
    ]
    + _tray_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenBiliClaw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Windowed (no console). The Windows build runs as a system-tray app
    # (packaging/entry.py); logs go to logs/desktop.log and are viewable from the
    # tray menu. macOS already runs windowed via the .app bundle below.
    console=False,
    icon=None,     # TODO: add icon -- packaging/icon.ico (Windows) / packaging/icon.icns (macOS)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OpenBiliClaw",
)

# --- macOS .app bundle (only on macOS) ---
if platform.system() == "Darwin":
    app = BUNDLE(
        coll,
        name="OpenBiliClaw.app",
        icon=None,  # TODO: packaging/icon.icns
        bundle_identifier="com.openbiliclaw.desktop",
        info_plist={
            "CFBundleName": "OpenBiliClaw",
            "CFBundleDisplayName": "OpenBiliClaw",
            "CFBundleVersion": bundle_version,
            "CFBundleShortVersionString": bundle_version,
            "LSMinimumSystemVersion": "10.15",
            "NSHighResolutionCapable": True,
            # Menu-bar agent: run as a status-bar item with no Dock icon,
            # matching the Windows system-tray behaviour (packaging/entry.py).
            "LSUIElement": True,
        },
    )

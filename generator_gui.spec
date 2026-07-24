# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for XRayGenerator.exe
#
# Build:
#   cd xray.koplugin
#   pyinstaller generator_gui.spec
#
# Output: dist/XRayGenerator.exe  (single self-contained executable)
#
# The .exe extracts itself to a temp folder on first run, so startup takes a
# few seconds.  User data (.env, xray/ output, Calibre library path) is always
# read/written relative to the .exe location, not the temp folder.

a = Analysis(
    ["generator_gui.py"],
    pathex=["."],
    binaries=[],
    # Bundle the prompts folder — generator.py loads it at import time.
    datas=[
        ("prompts", "prompts"),
        ("icons", "icons"),
    ],
    hiddenimports=[
        # AI providers (all may be used depending on user config)
        "anthropic",
        "openai",
        "groq",
        "google.generativeai",
        "requests",
        # Utility packages
        "dotenv",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy optional packages that aren't used by the GUI
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "tkinter",
        "test",
        "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XRayGenerator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX compression (install upx.exe and add to PATH for smaller output)
    upx=True,
    upx_exclude=[
        # PyQt6 DLLs can be corrupted by UPX on some platforms
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
    ],
    runtime_tmpdir=None,
    # No console window — the GUI has its own log display
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# PyInstaller spec — one --onedir bundle, both roles, console=False (spec §15).
#
# Build:  .venv/Scripts/python.exe -m PyInstaller billytalk.spec --noconfirm
# Output: dist/BillyTalk/BillyTalk.exe  (core by default; --ui for the interface)
#
# hiddenimports cover the two frozen traps this stack has:
#   * comtypes — the UIA verifier generates comtypes.gen at runtime; the client
#     submodules must be present or the generation import fails (verify.py then
#     degrades to verify_impossible, but we ship the client so it can work).
#   * pywin32 — win32timezone/win32cred/win32clipboard are imported lazily and
#     PyInstaller's static analysis misses them.

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("comtypes")
hiddenimports += collect_submodules("billytalk")
hiddenimports += [
    "win32timezone",
    "win32cred",
    "win32clipboard",
    "win32api",
    "win32process",
    "win32con",
    "win32gui",
]

a = Analysis(
    ["billytalk/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "hypothesis", "_pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BillyTalk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # spec §15: console=False for both roles
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BillyTalk",
)

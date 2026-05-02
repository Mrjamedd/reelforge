# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


_spec_root = Path(SPECPATH).resolve()
if (_spec_root / "reelpush_desktop.py").exists():
    ROOT = _spec_root
elif (_spec_root.parent / "reelpush_desktop.py").exists():
    ROOT = _spec_root.parent
else:
    ROOT = _spec_root.parent.parent
ICON_ICO = ROOT / "packaging" / "assets" / "reelpush-studio.ico"
ICON_ICNS = ROOT / "packaging" / "assets" / "reelpush-studio.icns"
ICON = ICON_ICNS if sys.platform == "darwin" and ICON_ICNS.exists() else ICON_ICO

datas = [
    (str(ROOT / "backend"), "backend"),
    (str(ROOT / "docker-compose.yml"), "."),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "README.md"), "."),
]

a = Analysis(
    [str(ROOT / "reelpush_desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReelPush Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ReelPush Studio",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ReelPush Studio.app",
        icon=str(ICON_ICNS if ICON_ICNS.exists() else ICON),
        bundle_identifier="com.reelpush.studio",
        info_plist={
            "CFBundleName": "ReelPush Studio",
            "CFBundleDisplayName": "ReelPush Studio",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )

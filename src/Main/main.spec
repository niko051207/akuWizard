# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller main.spec
from PyInstaller.utils.hooks import collect_data_files

datas = [('hand_landmarker.task', '.'), ('spells.json', '.'), ('assets', 'assets')]
datas += collect_data_files('mediapipe')   # mediapipe ships model/graph files it loads at runtime

a = Analysis(
    ['main.py'],
    pathex=[],
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
    a.binaries,
    a.datas,
    [],
    name='WarOfWizards',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # set to True while debugging a build to see print() output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

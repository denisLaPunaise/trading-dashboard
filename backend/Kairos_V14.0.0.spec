# -*- mode: python ; coding: utf-8 -*-
# V14.0.0 — Migration SaaS Firebase V2 (Auth email/password REST, pas d'Admin SDK côté client)
# (#30) Renommé Bot_FTMO -> Kairos

from PyInstaller.utils.hooks import collect_all

cert_d, cert_b, cert_h = collect_all('certifi')

all_datas    = cert_d
all_binaries = cert_b
all_hidden   = cert_h + [
    'firebase_sync',
    'requests',
    'urllib3',
    'charset_normalizer',
]

a = Analysis(
    ['bot_release13.py'],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
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
    name='Kairos_V14.0.0',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)

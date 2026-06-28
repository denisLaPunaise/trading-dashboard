# -*- mode: python ; coding: utf-8 -*-
# V14.0.0 — Migration SaaS Firebase (push Firestore + listener commandes)
# serviceAccountKey.json doit être placé à côté du .exe (non embarqué)

from PyInstaller.utils.hooks import collect_all

# Collecte automatique de toutes les dépendances Firebase + gRPC + certifi
fb_d,    fb_b,    fb_h    = collect_all('firebase_admin')
fs_d,    fs_b,    fs_h    = collect_all('google.cloud.firestore_v1')
auth_d,  auth_b,  auth_h  = collect_all('google.auth')
api_d,   api_b,   api_h   = collect_all('google.api_core')
grpc_d,  grpc_b,  grpc_h  = collect_all('grpc')
proto_d, proto_b, proto_h = collect_all('proto')
cert_d,  cert_b,  cert_h  = collect_all('certifi')

all_datas    = fb_d    + fs_d    + auth_d  + api_d   + grpc_d  + proto_d + cert_d
all_binaries = fb_b    + fs_b    + auth_b  + api_b   + grpc_b  + proto_b + cert_b
all_hidden   = fb_h    + fs_h    + auth_h  + api_h   + grpc_h  + proto_h + cert_h + [
    'firebase_sync',
    'google.oauth2.service_account',
    'google.oauth2.credentials',
    'google.protobuf',
    'google.protobuf.descriptor',
    'google.protobuf.message',
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
    name='Bot_FTMO_V14.0.0',
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

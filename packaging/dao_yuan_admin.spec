# -*- mode: python ; coding: utf-8 -*-
"""道源采集管理（维护端）onedir 构建规格。

维护包保留 GUI、无界面 CLI、采集适配器和备份恢复；排除普通用户端界面，
避免两套发布包互相夹带。windowed 子系统由入口脚本在 CLI 分支恢复标准输出。
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ASSETS = ROOT / "assets"
PACKAGING = ROOT / "packaging"

datas = []
if ASSETS.is_dir():
    datas.append((str(ASSETS), "assets"))

a = Analysis(
    [str(PACKAGING / "entry_admin.py")],
    pathex=[str(ROOT / "src"), str(PACKAGING)],
    binaries=[],
    datas=datas,
    hiddenimports=["smoke_admin", "smoke_common"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "daoyuan.ui_reader",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="道源采集管理",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "brand" / "app_icon.ico"),
    version=str(PACKAGING / "version_admin.txt"),
    manifest=str(PACKAGING / "app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="道源采集管理",
)

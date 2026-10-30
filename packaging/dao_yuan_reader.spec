# -*- mode: python ; coding: utf-8 -*-
"""道源知识库（普通用户端）onedir 构建规格。

普通用户包只包含阅读、检索、导出、智问和个人状态所需模块。采集适配器、
采集 CLI 与管理端界面通过 excludes 明确排除，不是靠隐藏按钮模拟分离。
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ASSETS = ROOT / "assets"
PACKAGING = ROOT / "packaging"

datas = []
if ASSETS.is_dir():
    datas.append((str(ASSETS), "assets"))

a = Analysis(
    [str(PACKAGING / "entry_reader.py")],
    pathex=[str(ROOT / "src"), str(PACKAGING)],
    binaries=[],
    datas=datas,
    hiddenimports=["smoke_reader", "smoke_common"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "daoyuan.collector",
        "daoyuan.ui_admin",
        "daoyuan.cli",
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
    name="道源知识库",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "brand" / "app_icon.ico"),
    version=str(PACKAGING / "version_reader.txt"),
    manifest=str(PACKAGING / "app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="道源知识库",
)

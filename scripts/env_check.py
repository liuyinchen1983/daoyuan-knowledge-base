# -*- coding: utf-8 -*-
"""只读环境盘点。不做任何安装、修改或系统设置变更。"""
import importlib.util
import os
import platform
import shutil
import sqlite3
import sys
from pathlib import Path


def has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def ver(pkg):
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return "?"


def main():
    print("=== 基础 ===")
    print("OS:", platform.platform())
    print("Python:", sys.version.replace("\n", " "))
    print("Executable:", sys.executable)
    print("CWD:", os.getcwd())
    drive = Path.cwd().anchor or "C:\\"
    print("Free GB (%s):" % drive, round(shutil.disk_usage(drive).free / 1024**3, 1))

    print("\n=== Python 包 ===")
    pkgs = [
        ("PySide6", "PySide6"),
        ("shiboken6", "shiboken6"),
        ("PyInstaller", "PyInstaller"),
        ("python-docx", "docx"),
        ("reportlab", "reportlab"),
        ("openpyxl", "openpyxl"),
        ("pypdf", "pypdf"),
        ("PyMuPDF", "fitz"),
        ("Pillow", "PIL"),
        ("lxml", "lxml"),
        ("requests", "requests"),
        ("jieba", "jieba"),
    ]
    for name, mod in pkgs:
        print("%-14s installed=%-5s version=%s" % (name, has(mod), ver(name)))

    print("\n=== Qt 模块 ===")
    for mod in [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
    ]:
        try:
            __import__(mod)
            print(mod, "OK")
        except Exception as e:
            print(mod, "FAIL", type(e).__name__, str(e)[:120])

    print("\n=== SQLite ===")
    print("sqlite3 version:", sqlite3.sqlite_version)
    con = sqlite3.connect(":memory:")
    try:
        opts = [r[0] for r in con.execute("PRAGMA compile_options")]
        for o in opts:
            if "FTS" in o.upper() or "JSON" in o.upper():
                print("compile_option:", o)
    except Exception as e:
        print("compile_options FAIL:", e)
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        con.execute("INSERT INTO t VALUES ('测试修行与见深')")
        row = con.execute("SELECT count(*) FROM t WHERE t MATCH '修行'").fetchone()
        print("FTS5 trigram: OK; 2字匹配数(已知限制):", row[0])
        row3 = con.execute("SELECT count(*) FROM t WHERE t MATCH '见深'").fetchone()
        print("FTS5 trigram 2字示例:", row3[0])
    except Exception as e:
        print("FTS5 trigram: FAIL", type(e).__name__, e)
    con.close()

    print("\n=== Git ===")
    git = shutil.which("git")
    print("git:", git)
    if git:
        os.system('git --version')

    print("\n=== 项目目录 ===")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            n = sum(len(f) for _, _, f in os.walk(p))
            print("[dir] %-16s files=%d" % (name, n))
        else:
            print("[file] %-16s %d bytes" % (name, os.path.getsize(p)))

    print("\n=== 图片/品牌素材 ===")
    exts = (".png", ".jpg", ".jpeg", ".ico", ".svg", ".webp", ".bmp")
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "build", "dist")]
        for f in files:
            if f.lower().endswith(exts):
                found.append(os.path.join(base, f))
    print("count:", len(found))
    for f in found[:30]:
        print(" ", os.path.relpath(f, root), os.path.getsize(f))


if __name__ == "__main__":
    main()

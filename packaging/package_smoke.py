# -*- coding: utf-8 -*-
"""对构建后的两套 EXE 做黑盒回归。

- 普通用户包：合成库阅读、双字检索、三格式导出、模块隔离。
- 维护包：GUI 管理页、备份、计划设置、CLI 输出与未到期跳过。
- 不访问微信/DeepSeek，不注册计划任务，不修改真实内容库。
"""
from __future__ import annotations

import json
import locale
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
READER_DIR = Path(
    os.environ.get("DAOYUAN_READER_DIR")
    or (ROOT / "dist" / "reader" / "道源知识库")
)
READER_EXE = READER_DIR / "道源知识库.exe"
ADMIN_DIR = ROOT / "dist" / "admin" / "道源采集管理"
ADMIN_EXE = ADMIN_DIR / "道源采集管理.exe"
SMOKE_BASE = Path(
    os.environ.get("DAOYUAN_SMOKE_BASE")
    or (Path(tempfile.gettempdir()) / "daoyuan_package_smoke")
)
ENCODING = locale.getpreferredencoding(False) or "utf-8"


def _run_gui_smoke(exe: Path, mode: str, out: Path) -> dict:
    env = os.environ.copy()
    env["DAOYUAN_PACKAGE_SMOKE"] = mode
    env["DAOYUAN_SMOKE_OUT"] = str(out)
    env.pop("DAOYUAN_CONFIG", None)
    completed = subprocess.run(
        [str(exe)],
        cwd=str(exe.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding=ENCODING,
        errors="replace",
        timeout=240,
    )
    if not out.exists():
        raise RuntimeError(
            "%s 打包冒烟没有生成结果文件；退出码 %s；stdout=%r stderr=%r"
            % (mode, completed.returncode, completed.stdout, completed.stderr)
        )
    report = json.loads(out.read_text(encoding="utf-8"))
    if completed.returncode != 0 or not report.get("ok"):
        raise RuntimeError(
            "%s 打包冒烟失败：exit=%s report=%s"
            % (mode, completed.returncode, json.dumps(report, ensure_ascii=False))
        )
    return report


def _run_cli(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ADMIN_EXE)] + args,
        cwd=str(ADMIN_DIR),
        capture_output=True,
        text=True,
        encoding=ENCODING,
        errors="replace",
        timeout=timeout,
    )


def _pyz_modules(exe: Path) -> set[str]:
    archive = CArchiveReader(str(exe))
    pyz_name = next(
        (name for name, entry in archive.toc.items() if name.startswith("PYZ")),
        None,
    )
    if not pyz_name:
        raise RuntimeError("%s 中没有找到 PYZ 归档" % exe)
    pyz = archive.open_embedded_archive(pyz_name)
    return set(pyz.toc)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    SMOKE_BASE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = SMOKE_BASE / ("run_" + stamp)
    base.mkdir(parents=True, exist_ok=True)
    summary: dict = {"ok": False, "started_at": stamp}
    try:
        for path in (READER_EXE, ADMIN_EXE):
            _check(path.exists(), "缺少构建产物：%s" % path)
        summary["builds"] = {
            "reader_exe": str(READER_EXE),
            "reader_bytes": READER_EXE.stat().st_size,
            "admin_exe": str(ADMIN_EXE),
            "admin_bytes": ADMIN_EXE.stat().st_size,
        }

        reader_report = _run_gui_smoke(
            READER_EXE, "reader", base / "reader_result.json"
        )
        summary["reader"] = reader_report
        _check(reader_report["frozen"], "普通用户端不是冻结 EXE")
        _check(reader_report.get("search", {}).get("hits", 0) >= 1, "普通用户端检索失败")
        exports = reader_report.get("exports") or {}
        _check(all(exports.get(fmt, 0) > 0 for fmt in ("txt", "docx", "pdf")), "导出不完整")
        _check(reader_report.get("excluded_modules_absent"), "普通用户端含采集/管理模块")
        _check(
            reader_report.get("topics", {}).get("list_items", 0) >= 1
            and reader_report.get("topics", {}).get("has_evidence")
            and reader_report.get("topics", {}).get("anchor_ok"),
            "普通用户端专题库没有可用的已收录条目",
        )

        admin_report = _run_gui_smoke(ADMIN_EXE, "admin", base / "admin_result.json")
        summary["admin"] = admin_report
        _check(admin_report["frozen"], "维护端不是冻结 EXE")
        _check(admin_report.get("required_modules_present"), "维护端缺少采集/CLI 模块")
        _check(admin_report.get("topics", {}).get("approved", 0) >= 1, "维护端专题审核失败")
        _check(
            admin_report.get("manual_import", {}).get("status") == "ready"
            and admin_report.get("manual_import", {}).get("recent", 0) >= 1,
            "维护端手动粘贴导入失败",
        )
        _check(admin_report.get("backup", {}).get("ready", 0) >= 1, "维护端备份失败")
        _check(
            admin_report.get("backup", {}).get("topic_entities", 0) >= 1,
            "维护端备份未包含专题库",
        )
        _check(
            (Path(admin_report["backup"]["path"]) / "topics.db").exists(),
            "备份包缺少 topics.db",
        )

        reader_modules = _pyz_modules(READER_EXE)
        admin_modules = _pyz_modules(ADMIN_EXE)
        forbidden_in_reader = sorted(
            name
            for name in reader_modules
            if name == "daoyuan.collector"
            or name.startswith("daoyuan.collector.")
            or name == "daoyuan.ui_admin"
            or name.startswith("daoyuan.ui_admin.")
            or name == "daoyuan.cli"
            or name.startswith("daoyuan.cli.")
        )
        _check(not forbidden_in_reader, "普通用户端 PYZ 含禁用模块：%s" % forbidden_in_reader)
        prohibited_in_admin = sorted(
            name
            for name in admin_modules
            if name == "daoyuan.ui_reader" or name.startswith("daoyuan.ui_reader.")
        )
        _check(not prohibited_in_admin, "维护端 PYZ 含普通用户界面：%s" % prohibited_in_admin)
        summary["archive_isolation"] = {
            "reader_modules": len(reader_modules),
            "admin_modules": len(admin_modules),
            "forbidden_in_reader": forbidden_in_reader,
            "prohibited_in_admin": prohibited_in_admin,
        }

        config_path = Path(admin_report["runtime_root"]) / "config.toml"
        _check(config_path.exists(), "维护端临时配置不存在")
        db_path = Path(admin_report["runtime_root"]) / "content" / "knowledge.db"
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO collect_runs(mode, status, started_at, finished_at, "
                "success_count, failed_count) VALUES('update','success',?,?,0,0)",
                (now, now),
            )
            conn.commit()
        finally:
            conn.close()

        cli_cases = []
        cases = [
            (["--version"], 0, "道源知识库"),
            (["--config", str(config_path), "--health"], 0, "RESULT: OK"),
            (["--config", str(config_path), "--schedule-status"], 0, "下次到期"),
            (
                ["--config", str(config_path), "--schedule-command"],
                0,
                "--update --if-due --max-fetch 20",
            ),
            (
                ["--config", str(config_path), "--update", "--if-due"],
                0,
                "未到检查时间",
            ),
            (
                ["--config", str(config_path), "--update", "--full"],
                2,
                "不能同时使用",
            ),
            (
                ["--config", str(config_path), "--restore", admin_report["backup"]["path"]],
                2,
                "这是预览",
            ),
            (
                [
                    "--config",
                    str(config_path),
                    "--restore",
                    str(base / "does_not_exist"),
                    "--yes",
                ],
                1,
                "备份校验未通过",
            ),
            (
                ["--config", str(config_path), "--backup"],
                0,
                "备份",
            ),
        ]
        for args, expected_code, needle in cases:
            proc = _run_cli(args)
            output = (proc.stdout or "") + (proc.stderr or "")
            cli_cases.append(
                {
                    "args": args,
                    "exit": proc.returncode,
                    "expected_exit": expected_code,
                    "needle": needle,
                    "found": needle in output,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
            _check(
                proc.returncode == expected_code,
                "CLI %s 退出码 %s，期望 %s；输出：%s"
                % (args, proc.returncode, expected_code, output),
            )
            _check(needle in output, "CLI %s 输出缺少 %r：%s" % (args, needle, output))
        summary["cli"] = cli_cases

        summary["ok"] = True
        (base / "package_smoke_report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("普通用户端：检索 %s 篇，导出 %s" % (
            reader_report["search"]["hits"],
            reader_report["exports"],
        ))
        print("维护端：备份 %s，计划 %s" % (
            admin_report["backup"].get("path"),
            admin_report["schedule"],
        ))
        print("模块隔离：普通包禁用模块 %d 个，维护包普通界面 %d 个" % (
            len(forbidden_in_reader), len(prohibited_in_admin)
        ))
        print("CLI：%d 个场景通过" % len(cli_cases))
        print("报告：%s" % (base / "package_smoke_report.json"))
        print("PACKAGE_SMOKE_OK")
        return 0
    except Exception as exc:
        summary["error"] = "%s: %s" % (type(exc).__name__, exc)
        (base / "package_smoke_report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("PACKAGE_SMOKE_FAILED：%s" % exc)
        print("报告：%s" % (base / "package_smoke_report.json"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

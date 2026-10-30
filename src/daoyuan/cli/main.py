# -*- coding: utf-8 -*-
"""无界面命令行入口：健康检查、采集、计划状态、备份恢复。"""
from __future__ import annotations

import argparse
import logging
import sys

from .. import APP_NAME, __stage__, __version__
from ..core.config import Config, load_config
from ..core.lock import ProcessLock
from ..core.schedule import (
    build_task_command,
    compute_schedule_status,
    describe_status,
    display_command,
    effective_schedule,
)
from ..storage.backup import create_backup, inspect_backup, list_backups, restore_backup
from ..storage.db import initialize_content_db, initialize_state_db, schema_version
from ..storage.state_db import StateStore

DEFAULT_UNATTENDED_FETCH = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daoyuan", description="道源知识库管理系统命令行（维护端无界面模式）"
    )
    parser.add_argument(
        "--config",
        help="配置文件路径（默认 %%USERPROFILE%%\\DaoYuanData\\config.toml）",
    )
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("--health", action="store_true", help="检查环境、配置与数据库")
    parser.add_argument("--init", action="store_true", help="初始化内容库与个人状态库")
    parser.add_argument("--update", action="store_true", help="执行每日增量检查")
    parser.add_argument("--full", action="store_true", help="执行全量复核")
    parser.add_argument(
        "--if-due",
        action="store_true",
        help="仅在计划到期时执行；与 --update 搭配供计划任务调用",
    )
    parser.add_argument(
        "--collection-id",
        type=int,
        action="append",
        help="只处理指定文集 ID，可重复传入",
    )
    parser.add_argument(
        "--max-fetch",
        type=int,
        help="本次最多抓取正文篇数；不传时默认 20，0 表示不限（不建议）",
    )
    parser.add_argument("--limit", type=int, help="每个文集最多处理篇数；0 表示不限")
    parser.add_argument(
        "--delay-min",
        type=float,
        help="本次每篇最少等待秒数（仅本次运行生效，不得低于 8）",
    )
    parser.add_argument(
        "--delay-max", type=float, help="本次每篇最多等待秒数（仅本次运行生效）"
    )
    parser.add_argument(
        "--batch-size", type=int, help="本次连续抓取多少篇后强制暂停（仅本次运行生效）"
    )
    parser.add_argument(
        "--batch-pause-min", type=float, help="本次批次暂停最少秒数（仅本次运行生效）"
    )
    parser.add_argument(
        "--batch-pause-max", type=float, help="本次批次暂停最多秒数（仅本次运行生效）"
    )
    parser.add_argument("--list-only", action="store_true", help="只同步清单，不抓正文")
    parser.add_argument("--backup", action="store_true", help="立即创建一致性备份")
    parser.add_argument("--list-backups", action="store_true", help="列出备份")
    parser.add_argument("--schedule-status", action="store_true", help="显示计划状态")
    parser.add_argument(
        "--schedule-command",
        action="store_true",
        help="只打印 Windows 计划任务安装命令，不注册任务",
    )
    parser.add_argument("--restore", metavar="备份目录", help="从备份恢复内容库")
    parser.add_argument(
        "--restore-state",
        action="store_true",
        help="恢复时同时覆盖个人状态库（默认保留当前阅读状态）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行恢复等破坏性操作；没有此参数时只预览",
    )
    return parser


def _print_version() -> None:
    print("%s %s (stage %s)" % (APP_NAME, __version__, __stage__))
    print("Python:", sys.version.split()[0], sys.executable)


def _open_content(config: Config):
    path = config.paths.content_db
    if not path.exists():
        return None
    from ..storage.db import connect

    return connect(path, readonly=True)


def health(config: Config) -> int:
    print("%s %s (stage %s)" % (APP_NAME, __version__, __stage__))
    print("Python :", sys.version.split()[0], sys.executable)
    print("Config :", config.config_path, "exists" if config.config_path.exists() else "missing")
    print("Data   :", config.data_dir)
    print("Content:", config.paths.content_db)
    print("State  :", config.paths.state_db)

    ok = True
    if config.paths.content_db.exists():
        conn = _open_content(config)
        try:
            from ..storage.db import health_report

            report = health_report(conn)
            print(
                "Content schema v%s, integrity=%s"
                % (report.get("schema_version"), report.get("integrity"))
            )
            print(
                "Collections=%s Articles=%s ready=%s pending=%s failed=%s segments=%s"
                % (
                    report.get("collections"),
                    report.get("articles"),
                    report.get("ready"),
                    report.get("pending"),
                    report.get("failed"),
                    report.get("segments"),
                )
            )
            if report.get("integrity") != "ok":
                ok = False
            if schema_version(conn) == 0:
                ok = False
        finally:
            conn.close()
    else:
        print("Content DB: missing (run --init)")
        ok = False

    if config.paths.state_db.exists():
        store = StateStore(config.paths.state_db)
        try:
            row = store.conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
            print("State schema v%s" % (row[0] if row else 0))
        finally:
            store.close()
    else:
        print("State DB: missing (run --init)")
        ok = False

    print("RESULT:", "OK" if ok else "NOT_READY")
    return 0 if ok else 1


def init_dbs(config: Config) -> int:
    config.paths.ensure()
    conn = initialize_content_db(config.paths.content_db)
    conn.close()
    store = initialize_state_db(config.paths.state_db)
    store.close()
    print("initialized:", config.paths.content_db)
    print("initialized:", config.paths.state_db)
    return 0


def _configure_logging(config: Config) -> logging.Logger:
    logger = logging.getLogger("daoyuan")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(
            config.paths.logs / "collect.log", encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(stream)
    return logger


def _run_collection(config: Config, args: argparse.Namespace) -> int:
    mode = "full" if args.full else "update"
    schedule = effective_schedule(config)
    if args.if_due:
        status = compute_schedule_status(config, schedule=schedule)
        print(
            "计划：%s；上次完成：%s；下次到期：%s"
            % (
                "启用" if status["enabled"] else "关闭",
                status["last_run_at"] or "无",
                status["next_due_at"] or "无",
            )
        )
        print(describe_status(status))
        if not status["due"]:
            print("未到检查时间，跳过本次采集。")
            return 0

    lock = ProcessLock()
    try:
        if not lock.acquire():
            print("另一个采集任务正在运行（可能是维护端窗口或计划任务），本次跳过。")
            return 3
        config.paths.ensure()
        logger = _configure_logging(config)
        conn = initialize_content_db(config.paths.content_db)
        try:
            from ..collector.service import CollectService

            max_fetch = args.max_fetch
            if max_fetch is None and not args.list_only:
                max_fetch = DEFAULT_UNATTENDED_FETCH
            service = CollectService(config, conn, logger=logger)
            service.register_known()
            results = service.run(
                mode=mode,
                collection_ids=args.collection_id,
                max_fetch=max_fetch,
                limit=args.limit,
                list_only=args.list_only,
            )
        finally:
            conn.close()
    except Exception as exc:
        logging.getLogger("daoyuan").exception("采集任务异常")
        print("采集失败：%s" % exc)
        return 1
    finally:
        lock.release()

    if not results:
        print("没有可执行的文集。")
        return 0
    code = 0
    total_success = total_failed = total_skipped = 0
    for result in results:
        total_success += result.success
        total_failed += result.failed
        total_skipped += result.skipped
        print(
            "【%s】%s：清单 %d，待采 %d，抓取 %d，成功 %d，未变 %d，失败 %d，"
            "不可用 %d，限制 %d%s"
            % (
                result.status,
                result.title,
                result.list_found,
                result.pending,
                result.fetched,
                result.success,
                result.skipped,
                result.failed,
                result.unavailable,
                result.restricted,
                ("（%s）" % result.message) if result.message else "",
            )
        )
        if result.status == "restricted":
            code = max(code, 4)
        elif result.status == "failed" or result.failed:
            code = max(code, 5)
    print(
        "汇总：成功 %d，未变 %d，失败/不可用 %d"
        % (total_success, total_skipped, total_failed)
    )

    # 采集结束后立即做专题库增量更新。只处理新文章/正文变化，通常几秒钟。
    try:
        from ..topics.scanner import scan_after_collection

        topic_results = scan_after_collection(
            config,
            [result.collection_id for result in results],
            logger=logging.getLogger("daoyuan.topics"),
        )
        for item in topic_results:
            if item.get("error"):
                print(
                    "专题库更新失败（文集 %s）：%s"
                    % (item.get("collection_id"), item.get("error"))
                )
            else:
                print(
                    "专题库：文集 %s，扫描 %s 篇，跳过 %s 篇，新增实体 %s，新增出处 %s"
                    % (
                        item.get("collection_id"),
                        item.get("scanned"),
                        item.get("skipped"),
                        item.get("entities_created"),
                        item.get("mentions_added"),
                    )
                )
    except Exception as exc:
        logging.getLogger("daoyuan").exception("专题库更新异常")
        print("专题库更新失败：%s" % exc)

    print("RESULT:", "OK" if code == 0 else "ATTENTION")
    return code


def _backup(config: Config) -> int:
    try:
        info = create_backup(config, note="cli")
    except Exception as exc:
        print("备份失败：%s" % exc)
        return 1
    print("备份完成：%s" % info["path"])
    content = info.get("content") or {}
    print(
        "内容库：文章 %s，可读 %s，段落 %s，schema v%s，完整性 %s"
        % (
            content.get("articles"),
            content.get("ready"),
            content.get("segments"),
            content.get("schema_version"),
            content.get("integrity"),
        )
    )
    print("备份包不含 API Key、日志、HTML 快照或导出文件。")
    return 0


def _list_backups(config: Config) -> int:
    items = list_backups(config)
    if not items:
        print("暂无备份。")
        return 0
    for info in items:
        content = info.get("content") or {}
        print(
            "%s  %s  文章 %s  可读 %s  %s"
            % (
                "OK" if info.get("valid") else "INVALID",
                info.get("path"),
                content.get("articles"),
                content.get("ready"),
                info.get("created_at") or "",
            )
        )
        if not info.get("valid"):
            print("  " + "；".join(info.get("errors") or []))
    return 0


def _schedule_status(config: Config) -> int:
    schedule = effective_schedule(config)
    status = compute_schedule_status(config, schedule=schedule)
    print("自动采集：%s" % ("启用" if schedule["enabled"] else "关闭"))
    print("周期：每 %d 天，时刻 %s" % (schedule["interval_days"], schedule["daily_time"]))
    print("上次完成：%s" % (status["last_run_at"] or "无"))
    print("下次到期：%s" % (status["next_due_at"] or "无"))
    print(describe_status(status))
    return 0


def _schedule_command(config: Config) -> int:
    schedule = effective_schedule(config)
    print("以下命令仅用于预览，本程序不会自动注册计划任务：")
    print(display_command(build_task_command(config, schedule)))
    return 0


def _restore(config: Config, args: argparse.Namespace) -> int:
    info = inspect_backup(args.restore)
    print("备份：%s" % info["path"])
    content = info.get("content") or {}
    print(
        "文章 %s，可读 %s，段落 %s，完整性 %s"
        % (
            content.get("articles"),
            content.get("ready"),
            content.get("segments"),
            content.get("integrity"),
        )
    )
    state = info.get("state") or {}
    print(
        "个人状态：%s"
        % (
            "将同时恢复"
            if args.restore_state
            else "保留当前状态（阅读进度/问答记录不会回退）"
        )
    )
    if state and not args.restore_state:
        print("备份中的状态库仅作参考，不会覆盖当前状态库。")
    if not args.yes:
        print("这是预览。确认执行请追加 --yes。")
        return 2
    if not info.get("valid"):
        print("备份校验未通过，拒绝恢复：%s" % "；".join(info.get("errors") or []))
        return 1
    lock = ProcessLock()
    try:
        if not lock.acquire():
            print("采集任务正在运行，不能同时恢复数据库。请等采集结束后重试。")
            return 3
        try:
            result = restore_backup(
                config, args.restore, restore_state=args.restore_state
            )
        except Exception as exc:
            print("恢复失败：%s" % exc)
            return 1
    finally:
        lock.release()
    print("恢复完成：%s" % result["path"])
    print("恢复前安全备份：%s" % (result.get("safety_backup") or "无"))
    return 0


def _apply_pacing(config: Config, args: argparse.Namespace) -> str | None:
    """把本次运行的限速参数写入内存配置，不修改 config.toml。"""
    collector = config.collector
    for name in (
        "delay_min",
        "delay_max",
        "batch_size",
        "batch_pause_min",
        "batch_pause_max",
    ):
        value = getattr(args, name, None)
        if value is not None:
            setattr(collector, name, value)
    if collector.delay_min < 8:
        return "为避免触发访问限制，--delay-min 不能小于 8 秒。"
    if collector.delay_max < collector.delay_min:
        return "--delay-max 不能小于 --delay-min。"
    if collector.batch_size < 1:
        return "--batch-size 必须大于等于 1。"
    if collector.batch_pause_min < 0:
        return "--batch-pause-min 不能为负数。"
    if collector.batch_pause_max < collector.batch_pause_min:
        return "--batch-pause-max 不能小于 --batch-pause-min。"
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        _print_version()
        return 0
    config = load_config(args.config)
    pacing_error = _apply_pacing(config, args)
    if pacing_error:
        print(pacing_error)
        return 2
    if args.init:
        return init_dbs(config)
    if args.health:
        return health(config)
    if args.backup:
        return _backup(config)
    if args.list_backups:
        return _list_backups(config)
    if args.schedule_status:
        return _schedule_status(config)
    if args.schedule_command:
        return _schedule_command(config)
    if args.restore:
        return _restore(config, args)
    if args.update or args.full or args.list_only or args.if_due:
        if args.update and args.full:
            build_parser().error("--update 与 --full 不能同时使用")
        if args.if_due and not (args.update or args.full or args.list_only):
            build_parser().error("--if-due 需要与 --update/--full/--list-only 搭配")
        return _run_collection(config, args)
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

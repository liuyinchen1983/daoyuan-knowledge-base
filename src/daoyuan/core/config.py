# -*- coding: utf-8 -*-
"""配置加载。配置文件与运行数据默认放在用户目录下，与 Git 工作树分离。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError
from .timeutil import parse_hhmm

DEFAULT_DATA_DIR = Path.home() / "DaoYuanData"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.toml"

CONFIG_TEMPLATE = """# 道源知识库管理系统配置
# 本文件不含任何密钥。DeepSeek API Key 通过软件设置界面写入 Windows 安全存储。

[app]
name = "道源知识库"
data_dir = "~/DaoYuanData"       # 运行数据目录，必须在 Git 仓库之外
timezone = "Asia/Shanghai"
log_level = "INFO"

[collector]
delay_min = 8.0                  # 每篇之间最少等待秒数
delay_max = 15.0                 # 每篇之间最多等待秒数
batch_size = 20                  # 每连续抓取多少篇强制暂停
batch_pause_min = 180.0          # 批次暂停最少秒数
batch_pause_max = 300.0          # 批次暂停最多秒数
max_fetch_per_run = 150          # 单次运行最多实际抓取篇数；0 表示不限
timeout = 30.0
max_retries = 2
retry_backoff = 30.0
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

[schedule]
enabled = true
interval_days = 1
daily_time = "09:00"

[ai]
provider = "deepseek"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
timeout = 60.0
max_context_chars = 12000
max_answer_tokens = 8192         # AI 回答的最大 token 数，调大后回答更完整
temperature = 0.2

[ui]
theme = "light"
font_size = 18
page_width = 820
"""


@dataclass(frozen=True)
class DataPaths:
    root: Path
    config_file: Path
    content_dir: Path
    content_db: Path
    state_dir: Path
    state_db: Path
    topics_dir: Path
    topics_db: Path
    logs: Path
    snapshots: Path
    exports: Path
    backups: Path
    secrets: Path

    @classmethod
    def from_root(cls, root: Path, config_file: Path) -> "DataPaths":
        root = Path(root)
        return cls(
            root=root,
            config_file=Path(config_file),
            content_dir=root / "content",
            content_db=root / "content" / "knowledge.db",
            state_dir=root / "state",
            state_db=root / "state" / "user_state.db",
            topics_dir=root / "topics",
            topics_db=root / "topics" / "topics.db",
            logs=root / "logs",
            snapshots=root / "snapshots",
            exports=root / "exports",
            backups=root / "backups",
            secrets=root / "secrets",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.content_dir,
            self.state_dir,
            self.topics_dir,
            self.logs,
            self.snapshots,
            self.exports,
            self.backups,
            self.secrets,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass
class AppSettings:
    name: str = "道源知识库"
    data_dir: str = str(DEFAULT_DATA_DIR)
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"


@dataclass
class CollectorSettings:
    delay_min: float = 8.0
    delay_max: float = 15.0
    batch_size: int = 20
    batch_pause_min: float = 180.0
    batch_pause_max: float = 300.0
    max_fetch_per_run: int = 150
    timeout: float = 30.0
    max_retries: int = 2
    retry_backoff: float = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )


@dataclass
class ScheduleSettings:
    enabled: bool = True
    interval_days: int = 1
    daily_time: str = "09:00"


@dataclass
class AiSettings:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout: float = 60.0
    max_context_chars: int = 12000
    max_answer_tokens: int = 8192
    temperature: float = 0.2


@dataclass
class UiSettings:
    theme: str = "light"
    font_size: int = 18
    page_width: int = 820


@dataclass
class Config:
    data_dir: Path
    config_path: Path
    app: AppSettings = field(default_factory=AppSettings)
    collector: CollectorSettings = field(default_factory=CollectorSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    ai: AiSettings = field(default_factory=AiSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    @property
    def paths(self) -> DataPaths:
        return DataPaths.from_root(self.data_dir, self.config_path)


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError("配置段 [%s] 必须是表" % name)
    return value


def _pick(section: dict, key: str, default, caster):
    if key not in section:
        return default
    try:
        return caster(section[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError("配置项 %s 不合法：%r" % (key, section[key])) from exc


def write_default_config(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")


def load_config(path: str | Path | None = None, create_if_missing: bool = True) -> Config:
    if path is None:
        path = os.environ.get("DAOYUAN_CONFIG") or DEFAULT_CONFIG_PATH
    config_path = Path(path).expanduser().resolve()
    raw: dict = {}
    if config_path.exists():
        try:
            # utf-8-sig 兼容记事本保存的 BOM
            with open(config_path, "r", encoding="utf-8-sig") as f:
                raw = tomllib.loads(f.read())
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("读取配置失败：%s" % exc) from exc
    elif create_if_missing:
        write_default_config(config_path)

    app_raw = _section(raw, "app")
    collector_raw = _section(raw, "collector")
    schedule_raw = _section(raw, "schedule")
    ai_raw = _section(raw, "ai")
    ui_raw = _section(raw, "ui")

    data_dir_value = app_raw.get("data_dir", str(DEFAULT_DATA_DIR))
    if not isinstance(data_dir_value, str) or not data_dir_value.strip():
        raise ConfigError("[app].data_dir 必须是非空路径")
    data_dir = Path(data_dir_value).expanduser()
    if not data_dir.is_absolute():
        data_dir = (config_path.parent / data_dir).resolve()

    app = AppSettings(
        name=_pick(app_raw, "name", "道源知识库", str),
        data_dir=str(data_dir),
        timezone=_pick(app_raw, "timezone", "Asia/Shanghai", str),
        log_level=_pick(app_raw, "log_level", "INFO", str),
    )
    collector = CollectorSettings(
        delay_min=_pick(collector_raw, "delay_min", 8.0, float),
        delay_max=_pick(collector_raw, "delay_max", 15.0, float),
        batch_size=_pick(collector_raw, "batch_size", 20, int),
        batch_pause_min=_pick(collector_raw, "batch_pause_min", 180.0, float),
        batch_pause_max=_pick(collector_raw, "batch_pause_max", 300.0, float),
        max_fetch_per_run=_pick(collector_raw, "max_fetch_per_run", 150, int),
        timeout=_pick(collector_raw, "timeout", 30.0, float),
        max_retries=_pick(collector_raw, "max_retries", 2, int),
        retry_backoff=_pick(collector_raw, "retry_backoff", 30.0, float),
        user_agent=_pick(collector_raw, "user_agent", CollectorSettings().user_agent, str),
    )
    schedule = ScheduleSettings(
        enabled=bool(_pick(schedule_raw, "enabled", True, bool)),
        interval_days=_pick(schedule_raw, "interval_days", 1, int),
        daily_time=_pick(schedule_raw, "daily_time", "09:00", str),
    )
    ai = AiSettings(
        provider=_pick(ai_raw, "provider", "deepseek", str),
        base_url=_pick(ai_raw, "base_url", "https://api.deepseek.com", str),
        model=_pick(ai_raw, "model", "deepseek-flash", str),
        timeout=_pick(ai_raw, "timeout", 60.0, float),
        max_context_chars=_pick(ai_raw, "max_context_chars", 12000, int),
        max_answer_tokens=_pick(ai_raw, "max_answer_tokens", 8192, int),
        temperature=_pick(ai_raw, "temperature", 0.2, float),
    )
    ui = UiSettings(
        theme=_pick(ui_raw, "theme", "light", str),
        font_size=_pick(ui_raw, "font_size", 18, int),
        page_width=_pick(ui_raw, "page_width", 820, int),
    )

    config = Config(
        data_dir=data_dir,
        config_path=config_path,
        app=app,
        collector=collector,
        schedule=schedule,
        ai=ai,
        ui=ui,
    )
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    c = config.collector
    if c.delay_min < 3.0:
        raise ConfigError("采集间隔 delay_min 不得小于 3 秒（保护本机 IP）")
    if c.delay_max < c.delay_min:
        raise ConfigError("delay_max 必须大于等于 delay_min")
    if c.batch_size < 1:
        raise ConfigError("batch_size 必须大于 0")
    if c.batch_pause_min < 0 or c.batch_pause_max < c.batch_pause_min:
        raise ConfigError("批次暂停时间配置不合法")
    if c.max_fetch_per_run < 0:
        raise ConfigError("max_fetch_per_run 不能为负数")
    if c.max_retries < 0:
        raise ConfigError("max_retries 不能为负数")
    if c.timeout <= 0:
        raise ConfigError("timeout 必须大于 0")
    if config.schedule.interval_days < 1:
        raise ConfigError("采集周期至少为 1 天")
    try:
        parse_hhmm(config.schedule.daily_time)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if config.ai.max_answer_tokens < 256:
        raise ConfigError("AI 回答长度 max_answer_tokens 不得小于 256")
    if config.ui.font_size < 12 or config.ui.font_size > 40:
        raise ConfigError("字号应在 12–40 之间")
    if config.ui.page_width < 480 or config.ui.page_width > 1600:
        raise ConfigError("阅读宽度应在 480–1600 之间")

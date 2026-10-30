# 配置说明

## 配置文件位置

默认：

```text
%USERPROFILE%\DaoYuanData\config.toml
```

加载顺序：

1. 命令行或程序传入的配置路径；
2. 环境变量 `DAOYUAN_CONFIG`；
3. 默认路径 `%USERPROFILE%\DaoYuanData\config.toml`。

文件不存在且允许创建时，程序会写入内置模板。文件使用 UTF-8，可带 BOM；TOML 解析失败会给出中文错误并停止启动。

`config.example.toml` 是仓库中的示例，不包含任何密钥。

## `[app]`

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `name` | `道源知识库` | 窗口和品牌头显示名称 |
| `data_dir` | `~/DaoYuanData` | 运行数据根目录；相对路径按配置文件所在目录解析 |
| `timezone` | `Asia/Shanghai` | 展示用时区 |
| `log_level` | `INFO` | 日志级别 |

`data_dir` 应在 Git 工作树之外。程序会在其下创建：

```text
content/knowledge.db
state/user_state.db
topics/topics.db
logs/  snapshots/  exports/  backups/  secrets/
```

## `[collector]`

| 键 | 默认值 | 约束 |
| --- | --- | --- |
| `delay_min` | `8.0` | 不得小于 3 秒 |
| `delay_max` | `15.0` | 不得小于 `delay_min` |
| `batch_size` | `20` | 每抓取多少篇暂停一次，必须大于 0 |
| `batch_pause_min` | `180.0` | 批次暂停最少秒数 |
| `batch_pause_max` | `300.0` | 不得小于 `batch_pause_min` |
| `max_fetch_per_run` | `150` | 单次运行实际抓取上限；`0` 表示不限 |
| `timeout` | `30.0` | 必须大于 0 |
| `max_retries` | `2` | 不得为负 |
| `retry_backoff` | `30.0` | 重试退避秒数 |
| `user_agent` | 桌面 Chrome UA | 修改后需自行评估兼容性与平台规则 |

程序会拒绝把 `delay_min` 调到 3 秒以下。验证页、429、403 会触发冷却并停止当前批次；冷却文件位于 `data_dir`，自动采集和单篇链接采集共用。

## `[schedule]`

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true`（示例文件为 `false`） | 是否启用周期采集 |
| `interval_days` | `1` | 至少 1 天 |
| `daily_time` | `09:00` | `HH:MM`，按 `Asia/Shanghai` 计算 |

仓库示例默认关闭计划，避免首次运行就联网。确认来源、频率和本机计划任务后再启用。

## `[ai]`

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `provider` | `deepseek` | 当前只实现 DeepSeek 兼容接口 |
| `base_url` | `https://api.deepseek.com` | 可改为兼容端点 |
| `model` | `deepseek-flash` | 模型名称 |
| `timeout` | `60.0` | 请求超时秒数 |
| `max_context_chars` | `12000` | 本地召回证据的最大字符数 |
| `max_answer_tokens` | `8192` | 回答最大 token；不得小于 256 |
| `temperature` | `0.2` | 采样温度 |

API Key **不写在这里**。请在软件设置页输入，程序通过 Windows DPAPI 加密保存到：

```text
<data_dir>\secrets\deepseek.key
```

DPAPI 绑定当前 Windows 用户。更换用户、重装系统或迁移到另一台机器后，需要重新输入 Key。

## `[ui]`

| 键 | 默认值 | 约束 |
| --- | --- | --- |
| `theme` | `light` | 当前主题名 |
| `font_size` | `18` | 12–40 |
| `page_width` | `820` | 480–1600 |

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `DAOYUAN_CONFIG` | 指定配置文件路径 |
| `DAOYUAN_SMOKE_BASE` | 仅打包冒烟脚本使用，指定临时输出根目录 |
| `QT_QPA_PLATFORM` | 仅测试/无界面环境使用，如 `offscreen` |

## 配置迁移建议

- 升级前备份整个 `data_dir`，至少备份 `content/`、`state/`、`topics/` 三个数据库。
- 不要用开发机的 `config.toml` 覆盖另一台机器上的配置；路径和计划可能不同。
- 修改 `data_dir` 后，旧目录不会自动搬迁，请手动复制并确认新路径可读写。
- 不要在多个进程或同步盘中同时写同一组数据库；程序使用 WAL 和进程锁，但不支持跨机并发。

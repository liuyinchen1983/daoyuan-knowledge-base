# 测试说明

## 原则

- 单元测试完全离线：不访问微信、不访问 DeepSeek、不读取真实数据目录。
- 测试数据使用临时目录和合成正文，不包含真实文章、账号、合集 ID 或 API Key。
- UI 冒烟测试使用离屏 Qt，不弹出窗口、不写入用户数据目录。

## 运行单元测试

```powershell
python -X utf8 scripts\run_tests.py -v
```

`scripts\run_tests.py` 把 `src` 加入 `sys.path`，用 `unittest` 发现并运行 `tests/` 下的全部用例，失败时返回非零退出码。

当前开发机结果（Windows 11、Python 3.15、PySide6 6.12.0）：

```text
Ran 123 tests in 10.086s
OK
```

测试数量会随功能变化，以实际输出为准。

## 覆盖范围

| 文件 | 主要覆盖 |
| --- | --- |
| `test_config.py` | 默认配置、相对/绝对数据目录、非法限速与字号校验 |
| `test_storage.py` | 迁移、WAL、外键、完整性检查、文章/段落/检索 |
| `test_collector.py` | 清单解析、排序、分页、标题日期、增量判断、风控错误 |
| `test_single_link.py` | 单篇链接校验、`cur_album_id`、真实 `mid/idx` 入库 |
| `test_manual_import.py` | 手工粘贴导入、章节号、重复处理 |
| `test_deleted_articles.py` | 删除文章、检索与专题提及清理 |
| `test_reader_library.py` | 文库排序、章节导航、阅读进度 |
| `test_search_export.py` | FTS 检索、TXT/DOCX/PDF 导出、文件名与内容 |
| `test_topics.py` | 六维抽取、别名、人物/地点/物品、专题树与审核状态 |
| `test_ai.py` | 检索词、证据召回、引用校验、token 统计、密钥存储失败路径 |
| `test_backup_schedule.py` | SQLite 一致性备份、恢复、计划到期计算 |
| `test_cli.py` | 健康检查、初始化、采集/备份命令的参数与退出码 |

## UI 冒烟测试

```powershell
python -X utf8 scripts\ui_smoke.py
```

成功输出 `UI_SMOKE_OK`。该脚本：

- 设置 `QT_QPA_PLATFORM=offscreen`；
- 在系统临时目录创建独立配置和数据库；
- 实例化阅读端与管理端全部页面；
- 校验导航数量，然后关闭窗口和服务。

它只验证“能创建、能显示、能关闭”，不替代人工界面检查。

## 打包冒烟测试

先按 `packaging/README.md` 构建，再运行：

```powershell
python -X utf8 packaging\package_smoke.py
```

输出目录默认在 `%TEMP%\daoyuan_package_smoke`，可用环境变量 `DAOYUAN_SMOKE_BASE` 指定其他位置。脚本不会写入真实数据目录。

## 导出冒烟测试

```powershell
python -X utf8 scripts\export_smoke.py
```

使用临时数据验证 TXT / DOCX / PDF 生成。需要本机 Qt 打印支持；PDF 的字体与分页效果仍建议人工抽查。

## 依赖本地数据的开发脚本

以下脚本用于开发机界面检查和真实数据回归，会读取当前 `config.toml` 指向的数据目录，并写入 `snapshots/` 或 `exports/`：

- `scripts\reader_smoke.py`
- `scripts\ask_smoke.py`
- `scripts\screenshot_admin.py`
- `scripts\export_smoke.py`

运行前请确认数据目录指针，或把 `DAOYUAN_CONFIG` 指向一份测试副本。不要直接对生产知识库运行。它们不属于默认测试流程。

## 建议的人工验收

发布前至少检查：

1. 阅读端：文库排序、章节切换、全文检索、专题树展开、设置页。
2. 管理端：预置来源/文集登记、手动导入、单篇链接采集、计划状态、备份与恢复。
3. 智问：无 Key 时的提示、配置 Key 后的真实回答、引用跳转、token 统计。
4. 高分屏：125% / 150% 缩放下的字体、按钮和表格。
5. 构建产物：两个 EXE 能启动，阅读端不包含采集/管理页面。

## 已知未自动化项

- 真实微信页面结构和风控页面的在线回归（受网络与平台变化影响）。
- DeepSeek 真实 API 的回答质量（需要用户 Key，且会产生费用）。
- Windows 计划任务的真实注册与跨天触发。
- 安装包、代码签名和 SmartScreen 行为。
- 长时间采集下的内存、日志轮转和断点续采。

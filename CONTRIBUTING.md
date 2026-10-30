# 参与开发

感谢关注。这个项目是 Windows 本地知识库工具，优先保证源码可运行、数据可回退、采集行为保守。

## 开发环境

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

- Python 3.11+。
- Windows 10/11。
- PySide6 用于界面和 PDF 导出；PyInstaller/Pillow 只在打包和图标生成时使用。

## 提交前检查

```powershell
python -X utf8 scripts\run_tests.py -v
python -X utf8 scripts\ui_smoke.py
```

如果修改了打包、入口或模块隔离，再运行：

```powershell
python -X utf8 packaging\package_smoke.py
```

提交前请确认：

- 测试通过，或在 PR 中说明未通过的原因；
- 没有把真实文章、数据库、日志、API Key、Cookie、用户 Logo 或构建产物带进提交；
- 没有降低采集限速、移除冷却或加入绕过访问控制的功能；
- 新增文件写入限制在配置的数据目录内；
- 新增依赖有明确理由，并同步更新 `requirements*.txt` / `pyproject.toml`。

建议在提交前执行：

```powershell
git status
git diff --check
```

## 代码风格

- 保持现有模块边界：`core`、`collector`、`storage`、`reader`、`topics`、`ai`、`export`、`ui_*`、`cli`。
- 优先使用标准库；新增第三方依赖前先讨论。
- 面向用户的错误信息使用中文，并说明可执行的下一步。
- 采集相关改动必须保留限速、请求互斥、冷却和失败停止语义。
- 数据库结构变更必须新增迁移，不要直接改已有迁移脚本。
- UI 改动需考虑 Windows 高分屏、中文字体和键盘操作。

## 测试数据

- 单元测试使用临时目录和合成文章，不访问微信或 DeepSeek。
- 不要在测试中写入真实文章、真实账号、真实合集 ID 或真实 API Key。
- 需要演示数据时，使用明显虚构的标题、人物和正文。

## 提交与 PR

- 一个 PR 聚焦一个主题，说明改了什么、为什么改、如何验证。
- 涉及数据目录、备份、采集频率、密钥处理的变化，请在 PR 中单独标注风险。
- 不要提交 `dist/`、`build/`、`*.db`、`*.key`、`config.toml`、`secrets/`、`backups/`、`exports/` 或 `tmp_video/`。
- 提交即表示你同意以 MIT License 授权你的贡献。

## 安全问题

不要用公开 Issue 报告密钥泄露、任意文件读写、命令执行等安全问题；请按 `SECURITY.md` 的方式私下报告。

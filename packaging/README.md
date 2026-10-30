# 打包说明

本目录把 `src/daoyuan` 构建成两套可独立运行的 Windows onedir 程序，不包含文章数据、API Key 或用户配置。

| 产物 | 入口 | Spec | 界面子系统 | 包含 |
|---|---|---|---|---|
| `道源知识库.exe` | `entry_reader.py` | `dao_yuan_reader.spec` | 窗口（无控制台） | 阅读、检索、专题库、智问、导出、资料包导入 |
| `道源采集管理.exe` | `entry_admin.py` | `dao_yuan_admin.spec` | 控制台 + GUI | 采集、来源/合集、专题库审核、计划、备份恢复、CLI |

## 构建

在本机已安装 PySide6 与 PyInstaller 的前提下：

```powershell
python -m PyInstaller --noconfirm --clean --distpath dist\reader --workpath build\reader packaging\dao_yuan_reader.spec
python -m PyInstaller --noconfirm --clean --distpath dist\admin --workpath build\admin packaging\dao_yuan_admin.spec
```

也可以运行 `packaging\build.ps1`。脚本只检查依赖，不会自动安装或升级任何软件包。

- 输出目录：`dist\reader\道源知识库\`、`dist\admin\道源采集管理\`。
- 构建后把 `README_reader.txt` / `README_admin.txt` 复制为各目录下的 `使用说明.txt`，并复制应用图标。
- `--clean` 只清理 PyInstaller 缓存和 `build`/`dist` 下的构建产物，不触碰源码或数据目录。

## onedir 约定

两套都是 **onedir**，不是单文件：

- 发布时必须复制整个目录，`_internal` 与 EXE 必须保持相对位置。
- 目录可以整体移动到其他路径或机器，不依赖源码树和开发机的 Python。
- 程序数据写入配置指定的目录（默认 `%USERPROFILE%\DaoYuanData`），与安装目录分离，升级程序不会覆盖内容库和个人状态。

## 入口与模块隔离

- `entry_reader.py`：先加入 `src`/`packaging` 路径；普通模式只导入 `daoyuan.ui_reader.app`。
- `entry_admin.py`：先判断 `sys.argv[1]` 是否以 `-` 开头，是则走 `daoyuan.cli.main`，不加载 Qt；否则进入 GUI 前调用 `FreeConsole()` 释放控制台。
- `dao_yuan_reader.spec` 排除 `daoyuan.collector`、`daoyuan.ui_admin`、`daoyuan.cli`。
- `dao_yuan_admin.spec` 排除 `daoyuan.ui_reader`。
- `package_smoke.py` 会读取 EXE 内 PYZ 归档，断言普通包无采集/维护/CLI 模块、维护包无阅读端界面。

## 图标

仓库只附带中性占位图标 `assets/brand/app_icon.ico`。

如果你拥有 Logo 的完整授权，可把原图放到 `assets/brand/logo.png`（该文件已被 `.gitignore` 排除），再运行：

```powershell
python -X utf8 packaging\make_icon_from_logo.py
```

没有 Logo 时可以运行 `python -X utf8 packaging\make_placeholder_icon.py` 重新生成占位图标。不要把他人的 Logo 或未确认授权的品牌素材提交到仓库。

## 冒烟回归

```powershell
python -X utf8 packaging\package_smoke.py
```

覆盖内容：

1. 普通用户端：合成文章阅读、双字检索、专题树、TXT/DOCX/PDF 导出、资料包导入、模块隔离。
2. 维护端：来源/任务/手动导入/专题库/计划/维护页面、专题树增量扫描与人工收录、备份（含 `topics.db`）、模块隔离。
3. CLI：`--version`、`--health`、`--schedule-status`、`--schedule-command`、未到期 `--update --if-due` 跳过、恢复预览、无效恢复拒绝、`--backup`。

结果默认写入系统临时目录下的 `daoyuan_package_smoke`，可用环境变量 `DAOYUAN_SMOKE_BASE` 改到其他位置。测试使用合成数据，不访问微信/DeepSeek，不注册计划任务。

## 未验证事项

- 全新 Windows 机器、无 Python/PySide6 环境的回归。
- 数字签名、杀毒软件误报、SmartScreen 行为。
- 多显示器不同缩放比例下的运行时切换。
- 真实 DeepSeek 联网问答与 Windows 计划任务注册。

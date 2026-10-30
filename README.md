# 道源知识库

Windows 本地知识库与阅读器。把你有权保存的公众号文章导入本地 SQLite，提供离线阅读、全文检索、六维专题库、带原文证据的 AI 问答，以及 TXT / Word / PDF 导出。

> 本仓库只包含通用源代码。**不包含任何文章正文、图片、数据库、API Key、用户 Logo 或打包产物。** 采集或导入前请确认你拥有相应授权，并遵守微信公众平台规则与著作权法。本项目与腾讯、微信、DeepSeek 均无隶属关系。

## 功能

- **本地内容库**：文章按文集、章节号、发布日期归档，正文与段落存入 SQLite，支持 WAL、完整性检查和一致性备份。
- **离线阅读**：章节列表、上一篇/下一篇、阅读进度、书签、字号/行距/页宽调整。
- **全文检索**：FTS5 trigram 索引，长词直接命中；两字词在 trigram 下有限制，程序会用 LIKE 兜底并明确提示。
- **六维专题库**：人物、组织与势力、地点与场景、概念与设定、物件与技术、剧情与伏笔；目录树展示，每条实体保留原文出处。
- **零 Token 候选抽取**：本地规则从正文提取人物、地点、物品候选，人工审核后才进入阅读端。
- **道源智问**：先本地召回证据，再调用 DeepSeek；回答必须带 `[C1]` 形式的原文引用，程序会校验引用是否真实存在。支持多轮对话和 token 用量统计。
- **导出**：按当前筛选范围导出 TXT、DOCX、PDF。
- **两套程序**：普通用户端只包含阅读相关模块；维护端负责采集、审核、计划和备份。打包时通过模块排除实现隔离，而不是隐藏按钮。
- **保守采集**：默认每篇间隔 8–15 秒、每 20 篇暂停 3–5 分钟、单次最多 150 篇；遇到验证页 / 429 / 403 立即停止并冷却 2–4 小时。没有代理轮换、账号池或验证码绕过。

## 组成

| 程序 | 入口 | 用途 |
|---|---|---|
| 道源知识库 | `scripts/run_reader.py` | 阅读、检索、专题库、智问、导出 |
| 道源采集管理 | `scripts/run_admin.py` | 来源/文集、采集、手动导入、专题审核、计划、备份 |
| 命令行 | `scripts/run_cli.py` | 健康检查、采集、计划状态、备份恢复 |

## 环境

- Windows 10 / 11 64 位（当前只在 Windows 上验证）。
- Python 3.11+（开发机使用 Python 3.15）。
- PySide6 6.12.0。
- 构建 EXE 额外需要 PyInstaller 6.22.3 和 Pillow。
- 不需要 Node.js、浏览器自动化或额外的数据库服务。

## 快速开始

```powershell
git clone <your-repo-url>
cd <repo>
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

先配置文章来源，再启动维护端：

1. 打开 `src\daoyuan\collector\known.py`，把 `BIZ`、`ACCOUNT_NAME`、`ACCOUNT_ID` 换成你自己的公众号信息。
2. 在 `KNOWN_COLLECTIONS` 中登记文集，或先留空，启动后在维护端「来源/文集」页使用「手动登记新文集」。
3. 启动维护端：

```powershell
python scripts\run_admin.py
```

4. 在维护端初始化内容库，然后选择「手动/链接导入」或「采集」。
5. 启动阅读端：

```powershell
python scripts\run_reader.py
```

如果只使用手工粘贴导入，可以不配置自动采集，也不填写 `KNOWN_COLLECTIONS`；手动登记一个文集后即可导入。

## 配置

首次运行会在用户目录创建：

```text
%USERPROFILE%\DaoYuanData\
  config.toml
  content\knowledge.db
  state\user_state.db
  topics\topics.db
  logs\
  snapshots\
  exports\
  backups\
  secrets\
```

- 默认数据目录是 `%USERPROFILE%\DaoYuanData`，不在 Git 工作树内。
- 可以用环境变量 `DAOYUAN_CONFIG` 指向其他配置文件，或在 `[app].data_dir` 中改数据目录。
- `config.example.toml` 是完整示例；程序不会把 API Key 写进配置文件。
- DeepSeek API Key 只通过软件设置界面写入 Windows DPAPI 安全存储，保存在 `secrets\deepseek.key`。该文件已被 `.gitignore` 排除，不要提交、截图或发给他人。
- 修改配置后重启对应程序生效。

## AI 问答与数据边界

- 只有在你明确同意后，程序才会把**问题**和**本地召回的文章片段**发送给 DeepSeek。
- API Key 不会写入源码、`config.toml`、日志或导出文件。
- 回答中的引用编号会与本次证据逐一核对；不存在的编号会被标记为不可靠。
- 没有 Key 时仍可使用本地召回和本地候选，不会发起网络请求。
- 模型名称、接口地址和最大回答长度可在配置或设置界面调整。

## 采集与 IP 保护

默认值写在 `src\daoyuan\core\config.py` 的 `CollectorSettings` 中，并在 `validate_config` 中强制检查：

- `delay_min` 不得小于 3 秒；默认 8–15 秒。
- 每 `batch_size` 篇强制暂停，默认 20 篇、3–5 分钟。
- 单次运行最多 `max_fetch_per_run` 篇，默认 150；无人值守默认更保守。
- 冷却文件与请求间隔文件保存在数据目录，单篇链接采集和自动采集共用。
- 遇到验证页、429、403 立即停止，写入 2h/4h/8h 递增冷却；不会自动重试绕过。
- 不包含代理轮换、账号池、验证码识别或任何绕过访问控制的逻辑。

请只采集你有权访问和保存的内容，并控制频率。因使用方式产生的账号、IP 或版权风险由使用者自行承担。

## 构建 Windows EXE

```powershell
python -m PyInstaller --noconfirm --clean --distpath dist\reader --workpath build\reader packaging\dao_yuan_reader.spec
python -m PyInstaller --noconfirm --clean --distpath dist\admin --workpath build\admin packaging\dao_yuan_admin.spec
```

也可以运行 `packaging\build.ps1`。脚本只检查依赖，不会自动安装或升级软件包。详细说明见 `packaging\README.md`。

## 测试

```powershell
python -X utf8 scripts\run_tests.py -v
python -X utf8 scripts\ui_smoke.py
```

- `run_tests.py` 使用临时目录和合成数据，不访问网络。
- `ui_smoke.py` 使用 offscreen Qt 启动两套窗口，不读取真实内容库。
- `packaging\package_smoke.py` 对构建后的 EXE 做黑盒回归；默认写入系统临时目录。
- 更完整的测试说明见 `docs\TESTING.md`。

当前源码测试套件共 123 项，已在 Windows 11 + Python 3.15 + PySide6 6.12.0 下通过。

## 目录结构

```text
src/daoyuan/
  core/       配置、数据模型、错误、进程锁、计划、文本与时间工具
  collector/  公众号清单/正文采集、单篇链接、手动导入、冷却与限速
  storage/    SQLite 连接、迁移、仓储、个人状态、备份恢复
  reader/     阅读端数据服务
  topics/     六维目录、规则抽取、增量扫描、专题库存储
  ai/         DPAPI 密钥、DeepSeek 客户端、RAG 与引用核验
  export/     TXT / DOCX / PDF
  ui_common/  主题、品牌头、图标
  ui_reader/  普通用户端
  ui_admin/   维护端
  cli/        无界面命令行
scripts/      启动、测试、冒烟与截图脚本
packaging/    PyInstaller 规格、入口、版本资源与打包回归
tests/        合成数据单元测试
docs/         架构、测试与配置说明
```

## 数据与隐私

- 本仓库不附带文章数据。文章版权归原权利人，使用者应只导入自己有权保存的内容。
- 数据库、日志、导出、备份、API Key、用户 Logo 和个人配置均不应提交到 Git。
- `.gitignore` 已排除常见的数据、密钥、构建产物和私有素材；提交前仍建议执行 `git status` 和密钥扫描。
- 备份包含内容库、个人状态库和专题库；具体格式见 `docs\ARCHITECTURE.md`。

## 已知限制

- 两字词在 FTS5 trigram 下可能无法直接命中，程序会回退到 LIKE，速度较慢。
- 专题候选抽取是规则启发式，不能保证召回所有实体，最终需要人工审核。
- 采集依赖微信公开页面的结构；页面改版后解析规则可能需要更新。
- 当前未做代码签名，Windows SmartScreen 可能提示未知发布者。
- 未在全新无 Python 环境的 Windows 机器上做完整回归。

## 许可证

源代码采用 MIT License，见 `LICENSE`。

第三方组件（PySide6 / Qt、PyInstaller、Python、SQLite、Pillow 等）仍受各自许可证约束，详见 `THIRD_PARTY_NOTICES.md`。分发打包后的 EXE 时，请同时遵守 Qt 的 LGPLv3 等第三方条款。

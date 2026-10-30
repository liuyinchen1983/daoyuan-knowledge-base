# 架构说明

## 总览

程序是单机 Windows 桌面应用，数据全部落在本机 SQLite 文件中，不依赖自建服务器。

```text
公众号公开页面 / 手工粘贴
        │
        ▼
   collector  ──► content/knowledge.db ──► reader/service ──► 阅读端 UI
        │                  │
        │                  └──► topics/scanner ──► topics/topics.db ──► 专题树
        │
        └──► state/user_state.db（阅读进度、书签、问答记录、设置）

阅读端提问 ──► ai/rag 本地召回 ──► DeepSeek API ──► 引用核验 ──► 回答
```

## 模块

### `core`

- `config.py`：加载 TOML，提供默认数据目录、配置校验和 `CONFIG_TEMPLATE`。
- `models.py`：`ListingItem`、`ParsedArticle`、`ArticleSegment` 等数据结构。
- `errors.py`：配置、存储、采集、风控、AI、导出等错误类型。
- `lock.py`：Windows 命名互斥锁 `ProcessLock` 和 `RequestGate`，保证 GUI 与计划任务不会同时采集，所有微信请求串行发出。
- `schedule.py`：计划状态、到期计算、Windows 计划任务命令预览/注册。
- `text.py`、`timeutil.py`：章节号、标题日期、北京时间与格式转换。

### `collector`

- `known.py`：公众号和文集配置。仓库只提供占位值，使用前必须替换。
- `http.py`：仅用标准库的 HTTP 客户端；统一 User-Agent、超时、gzip、403/429 风控识别。
- `album.py`：读取合辑清单、分页、排序方向探测。
- `parser.py`：解析文章标题、作者、发布时间、正文段落和安全 HTML；识别验证页/删除页。
- `service.py`：清单 → 待采 → 正文 → 入库的编排，含限速、批次暂停、单次上限、停止事件。
- `single.py`：按单篇链接采集；校验链接属于登记的公众号，识别 `cur_album_id`，复用真实 `mid/idx` 入库。
- `manual.py`：人工粘贴导入；不联网，复用同一套正文分段、FTS 和专题扫描链路。
- `cooldown.py`：请求间隔与验证页冷却；默认写在配置的数据目录，自动采集和单篇采集共用。

### `storage`

- `db.py`：SQLite 连接、WAL、`foreign_keys`、`busy_timeout`、迁移与完整性检查。
- `migrations.py`：内容库、个人状态库、专题库的结构迁移。新增结构必须追加新版本，不能改旧迁移。
- `repositories.py`：来源、文集、文章、段落、清单等仓储。
- `state_db.py`：阅读进度、书签、问答会话/消息/引用、AI 用量和设置。
- `backup.py`：用 SQLite backup API 生成一致性备份；备份目录含 `knowledge.db`、`user_state.db`、`topics.db` 和 `manifest.json`，不含 API Key、日志或导出文件。

### `reader`

`ReaderService` 组合内容库和个人状态库，提供文集、章节列表、文章正文、相邻篇章、检索、导出数据装载和健康检查。

### `topics`

- `catalog.py`：六维展示目录（人物、组织与势力、地点与场景、概念与设定、物件与技术、剧情与伏笔）、类型标签、分组和配色。
- `extractor.py`：零 Token 规则抽取人物、地点、物品候选；保留原文句子、段落和字符位置。
- `scanner.py`：按文章正文哈希增量扫描；正文变化或抽取器版本变化时重扫；清理已删除文章的提及。
- `store.py`：专题实体、别名、提及、审核状态、章节笔记和标签。

### `ai`

- `key_store.py`：Windows DPAPI 加密保存 API Key；非 Windows 或 DPAPI 失败时拒绝保存，不退化为明文。
- `deepseek.py`：标准库 HTTP 客户端，调用兼容 OpenAI 风格的 `/chat/completions`。
- `rag.py`：本地召回、查询词提取、提示词构造、`[Cx]` 引用校验、依据等级提取。
- `service.py`：密钥读写、模型选择、用户同意、问答记录和 token 用量统计。

### `export`

- `service.py`：装载文章并分发 TXT / DOCX / PDF。
- `docx.py`：直接生成 OOXML，不依赖 `python-docx`。
- `pdf.py`：使用 Qt 的打印支持生成 PDF，保留段落和静态强调色。

### UI 与 CLI

- `ui_common`：主题、品牌头、图标、DPI/字体适配。
- `ui_reader`：普通用户端；页面包括首页、文库、检索、专题库、智问、设置。
- `ui_admin`：维护端；页面包括概览、来源/文集、采集、手动/链接导入、专题库、计划、维护。
- `cli`：健康检查、初始化、增量/全量采集、计划状态、备份和恢复。

打包时 `dao_yuan_reader.spec` 排除 `daoyuan.collector`、`daoyuan.ui_admin`、`daoyuan.cli`；`dao_yuan_admin.spec` 排除 `daoyuan.ui_reader`。这是真实的模块隔离，不是隐藏按钮。

## 数据目录

默认 `%USERPROFILE%\DaoYuanData`：

```text
config.toml
content/knowledge.db
state/user_state.db
topics/topics.db
logs/
snapshots/
exports/
backups/
secrets/deepseek.key
```

- 内容库、个人状态库和专题库都使用 WAL；备份时通过 SQLite backup API 取得一致快照。
- `topics.db` 是派生数据，但人工审核状态无法重建，所以纳入备份。
- `secrets/deepseek.key` 是 DPAPI 密文，不属于备份内容。
- 程序不会把运行数据写进源码目录或 Git 工作树。

## 采集并发与风控

1. `ProcessLock` 保证同一时间只有一个采集任务。
2. `RequestGate` 保证自动采集、单篇链接采集的 HTTP 请求串行。
3. `cooldown.py` 记录下一次允许请求时间；连点会被 `TooSoonError` 拒绝。
4. 验证页、429、403 触发 `RiskControlError`，写入 2h/4h/8h 递增冷却并停止当前批次。
5. 默认限速与批次暂停在 `CollectorSettings`，`validate_config` 拒绝过小的间隔。

## AI 数据流

1. `extract_terms` 从问题中提取检索词，去掉疑问词和公众号等停用短语。
2. `collect_evidence` 在本地内容库召回段落，组装成带编号的证据块。
3. 只有用户同意且配置了 Key 时，才把问题和证据发给 DeepSeek。
4. 回答返回后，`validate_citations` 核对 `[Cx]` 是否真实存在；无效引用不会当作可靠出处。
5. 问答与 token 用量写入个人状态库，不保存 API Key。

## 未覆盖范围

- 不是多用户 Web 服务，没有登录、权限和远程 API。
- 没有自动更新、遥测、远程配置。
- 没有代理池、账号池、验证码识别或访问控制绕过。

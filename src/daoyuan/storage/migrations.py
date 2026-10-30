# -*- coding: utf-8 -*-
"""数据库结构与版本迁移。每个迁移只追加，不修改已发布版本。"""

CONTENT_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    biz TEXT,
    account_name TEXT NOT NULL DEFAULT '',
    account_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'wechat_mp',
    verified_state TEXT NOT NULL DEFAULT 'unverified',
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_biz
    ON sources(biz) WHERE biz IS NOT NULL AND biz <> '';

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    album_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    sort_mode TEXT NOT NULL DEFAULT 'unknown',
    expected_count INTEGER,
    is_updating INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, album_id)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    msgid TEXT NOT NULL,
    itemidx TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    page_author TEXT,
    publish_ts INTEGER,
    publish_time_raw TEXT,
    publish_time_source TEXT,
    title_date TEXT,
    source_url TEXT,
    canonical_url TEXT,
    text_content TEXT,
    safe_html TEXT,
    text_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    first_collected_at TEXT,
    last_checked_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, msgid, itemidx)
);
CREATE INDEX IF NOT EXISTS ix_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS ix_articles_publish ON articles(publish_ts);
CREATE INDEX IF NOT EXISTS ix_articles_hash ON articles(text_hash);

CREATE TABLE IF NOT EXISTS article_collections (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    serial_no INTEGER,
    is_current INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(article_id, collection_id)
);
CREATE INDEX IF NOT EXISTS ix_article_collections_collection
    ON article_collections(collection_id, serial_no);

CREATE TABLE IF NOT EXISTS article_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    plain_text TEXT NOT NULL,
    safe_html_fragment TEXT,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end INTEGER NOT NULL DEFAULT 0,
    segment_hash TEXT,
    UNIQUE(article_id, seq_no)
);
CREATE INDEX IF NOT EXISTS ix_segments_article ON article_segments(article_id, seq_no);

CREATE TABLE IF NOT EXISTS article_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    text_hash TEXT NOT NULL,
    snapshot_path TEXT,
    content_text TEXT,
    captured_at TEXT NOT NULL,
    change_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_revisions_article ON article_revisions(article_id, id);

CREATE TABLE IF NOT EXISTS collection_listings (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    msgid TEXT NOT NULL,
    itemidx TEXT NOT NULL,
    list_order INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_scan_run_id INTEGER,
    PRIMARY KEY(collection_id, article_id)
);

CREATE TABLE IF NOT EXISTS collect_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    collection_id INTEGER,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    list_found INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    restricted_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_started ON collect_runs(started_at);

CREATE TABLE IF NOT EXISTS collect_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES collect_runs(id) ON DELETE CASCADE,
    article_id INTEGER,
    msgid TEXT,
    itemidx TEXT,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_run ON collect_items(run_id, status);

CREATE TABLE IF NOT EXISTS discover_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    album_id TEXT,
    biz TEXT,
    title TEXT,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    text_content,
    content='articles',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS trg_articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, text_content)
    VALUES (new.id, ifnull(new.title, ''), ifnull(new.text_content, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, text_content)
    VALUES ('delete', old.id, ifnull(old.title, ''), ifnull(old.text_content, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_articles_au AFTER UPDATE OF title, text_content ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, text_content)
    VALUES ('delete', old.id, ifnull(old.title, ''), ifnull(old.text_content, ''));
    INSERT INTO articles_fts(rowid, title, text_content)
    VALUES (new.id, ifnull(new.title, ''), ifnull(new.text_content, ''));
END;

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    plain_text,
    content='article_segments',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS trg_segments_ai AFTER INSERT ON article_segments BEGIN
    INSERT INTO segments_fts(rowid, plain_text)
    VALUES (new.id, ifnull(new.plain_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_segments_ad AFTER DELETE ON article_segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, plain_text)
    VALUES ('delete', old.id, ifnull(old.plain_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_segments_au AFTER UPDATE OF plain_text ON article_segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, plain_text)
    VALUES ('delete', old.id, ifnull(old.plain_text, ''));
    INSERT INTO segments_fts(rowid, plain_text)
    VALUES (new.id, ifnull(new.plain_text, ''));
END;
"""

STATE_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reading_progress (
    article_id INTEGER PRIMARY KEY,
    segment_id INTEGER,
    position INTEGER,
    percent REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    segment_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bookmarks_article ON bookmarks(article_id);

CREATE TABLE IF NOT EXISTS qa_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES qa_sessions(id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qa_messages_session ON qa_messages(session_id, seq_no);

CREATE TABLE IF NOT EXISTS qa_citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES qa_messages(id) ON DELETE CASCADE,
    citation_label TEXT NOT NULL,
    article_id INTEGER,
    segment_id INTEGER,
    snippet TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qa_citations_message ON qa_citations(message_id);
"""

CONTENT_V2 = """
CREATE TABLE IF NOT EXISTS deleted_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    msgid TEXT NOT NULL,
    itemidx TEXT NOT NULL,
    collection_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    deleted_at TEXT NOT NULL,
    snapshot_path TEXT,
    UNIQUE(source_id, msgid, itemidx, collection_id)
);
CREATE INDEX IF NOT EXISTS ix_deleted_articles_collection
    ON deleted_articles(collection_id, msgid, itemidx);
"""

CONTENT_MIGRATIONS = [
    (1, "初始内容库结构（来源/合集/文章/段落/版本/采集记录/FTS）", CONTENT_V1),
    (2, "删除文章墓碑表（防止自动采集把已删除文章重新收录）", CONTENT_V2),
]

STATE_V2 = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'ask',
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    session_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_usage_created ON ai_usage(created_at);
"""

STATE_MIGRATIONS = [
    (1, "初始个人状态库（进度/书签/设置/问答会话）", STATE_V1),
    (2, "AI Token 用量流水（按请求记录，支持今日/近 7 天/近 30 天统计）", STATE_V2),
]

TOPIC_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'rule',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(collection_id, kind, name)
);
CREATE INDEX IF NOT EXISTS ix_topic_entities_lookup
    ON topic_entities(collection_id, status, kind);

CREATE TABLE IF NOT EXISTS topic_aliases (
    entity_id INTEGER NOT NULL REFERENCES topic_entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY(entity_id, alias)
);

CREATE TABLE IF NOT EXISTS topic_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES topic_entities(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL,
    segment_id INTEGER,
    surface TEXT NOT NULL,
    quote TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, article_id, segment_id, quote)
);
CREATE INDEX IF NOT EXISTS ix_topic_mentions_entity
    ON topic_mentions(entity_id, id);
CREATE INDEX IF NOT EXISTS ix_topic_mentions_article
    ON topic_mentions(article_id);

CREATE TABLE IF NOT EXISTS topic_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

TOPIC_V2 = """
CREATE TABLE IF NOT EXISTS topic_article_scan (
    collection_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    text_hash TEXT NOT NULL DEFAULT '',
    extractor_version INTEGER NOT NULL DEFAULT 1,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY(collection_id, article_id)
);
CREATE INDEX IF NOT EXISTS ix_topic_scan_collection
    ON topic_article_scan(collection_id, scanned_at);
"""

TOPIC_V3 = """
ALTER TABLE topic_entities ADD COLUMN parent_id INTEGER;
ALTER TABLE topic_entities ADD COLUMN group_key TEXT NOT NULL DEFAULT '';
ALTER TABLE topic_entities ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE topic_entities ADD COLUMN meta_json TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS topic_article_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES topic_entities(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(article_id, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_topic_tags_article
    ON topic_article_tags(article_id, entity_id);
CREATE INDEX IF NOT EXISTS ix_topic_tags_entity
    ON topic_article_tags(entity_id, article_id);

CREATE TABLE IF NOT EXISTS topic_article_notes (
    article_id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_topic_notes_collection
    ON topic_article_notes(collection_id, article_id);
"""

TOPIC_MIGRATIONS = [
    (1, "专题库初始结构（实体/别名/原文出处/元数据）", TOPIC_V1),
    (2, "增量扫描状态（按文章正文哈希判断是否需要重新抽取）", TOPIC_V2),
    (3, "六维专题树：分组、排序、章节标签与章节摘要", TOPIC_V3),
]

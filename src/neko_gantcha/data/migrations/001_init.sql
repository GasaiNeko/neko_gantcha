-- 初期スキーマ。BEGIN / COMMIT と user_version の更新は db.migrate() が行う。
-- 日時は JST のタイムゾーンなし ISO 8601 文字列(例: 2026-11-16T05:00:00)。

CREATE TABLE categories (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL UNIQUE,
    color                TEXT    NOT NULL DEFAULT '#888888',
    daily_reset_time     TEXT    NOT NULL DEFAULT '00:00',          -- HH:MM
    weekly_reset_weekday INTEGER NOT NULL DEFAULT 0
                         CHECK (weekly_reset_weekday BETWEEN 0 AND 6), -- 0=月曜
    monthly_reset_day    INTEGER NOT NULL DEFAULT 1
                         CHECK (monthly_reset_day BETWEEN 1 AND 31),
    sort_order           INTEGER NOT NULL DEFAULT 0,
    archived             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    start_at    TEXT    NOT NULL,
    end_at      TEXT    NOT NULL,
    url         TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    CHECK (start_at <= end_at)
);
CREATE INDEX idx_events_period ON events(start_at, end_at);

CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    goal        TEXT    NOT NULL DEFAULT '',
    target_date TEXT,                                               -- YYYY-MM-DD
    event_id    INTEGER REFERENCES events(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'done', 'archived'))
);

CREATE TABLE tasks (
    id               INTEGER PRIMARY KEY,
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    title            TEXT    NOT NULL,
    note             TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'todo'
                     CHECK (status IN ('todo', 'doing', 'done')),
    priority         INTEGER NOT NULL DEFAULT 0,
    start_at         TEXT,
    due_at           TEXT,
    event_id         INTEGER REFERENCES events(id) ON DELETE SET NULL,
    event_relation   TEXT    CHECK (event_relation IN ('during', 'before')),
    project_id       INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    parent_task_id   INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    progress_current INTEGER,
    progress_target  INTEGER,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    completed_at     TEXT
);
CREATE INDEX idx_tasks_event   ON tasks(event_id);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_parent  ON tasks(parent_task_id);
CREATE INDEX idx_tasks_due     ON tasks(due_at);

CREATE TABLE recurrences (
    task_id      INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    freq         TEXT    NOT NULL CHECK (freq IN ('daily', 'weekly', 'monthly')),
    active_from  TEXT,
    active_until TEXT,
    event_id     INTEGER REFERENCES events(id) ON DELETE SET NULL
);

CREATE TABLE completions (
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    period_key   TEXT    NOT NULL,
    completed_at TEXT    NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1 CHECK (count > 0),
    PRIMARY KEY (task_id, period_key)
);
CREATE INDEX idx_completions_period ON completions(period_key);

-- 初期分類
INSERT INTO categories
    (name, color, daily_reset_time, weekly_reset_weekday, monthly_reset_day, sort_order)
VALUES
    ('ソシャゲA', '#3B82F6', '05:00', 0, 1, 0),
    ('ソシャゲB', '#EC4899', '05:00', 0, 1, 1),
    ('日常',      '#10B981', '00:00', 0, 1, 2);

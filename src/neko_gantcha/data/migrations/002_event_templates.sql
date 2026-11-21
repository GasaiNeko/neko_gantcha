-- イベントのテンプレート(フェーズ10)。
-- イベントと配下タスクの組を、日付を持たない形(イベント開始からの分数)で保存する。

CREATE TABLE event_templates (
    id               INTEGER PRIMARY KEY,
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name             TEXT    NOT NULL UNIQUE,                       -- テンプレート名(作るイベントの名前の初期値)
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes >= 0), -- イベントの長さ
    start_time       TEXT    NOT NULL DEFAULT '00:00',              -- いつもの開始時刻 HH:MM
    url              TEXT    NOT NULL DEFAULT '',
    note             TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL
);

CREATE TABLE template_tasks (
    id                     INTEGER PRIMARY KEY,
    template_id            INTEGER NOT NULL REFERENCES event_templates(id) ON DELETE CASCADE,
    parent_id              INTEGER REFERENCES template_tasks(id) ON DELETE CASCADE,
    category_id            INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    title                  TEXT    NOT NULL,
    note                   TEXT    NOT NULL DEFAULT '',
    priority               INTEGER NOT NULL DEFAULT 0,
    linked                 INTEGER NOT NULL DEFAULT 0,               -- イベントに紐付けるか
    event_relation         TEXT    CHECK (event_relation IN ('during', 'before')),
    start_offset_minutes   INTEGER,                                  -- イベント開始からの分数
    due_offset_minutes     INTEGER,
    progress_target        INTEGER,
    freq                   TEXT    CHECK (freq IN ('daily', 'weekly', 'monthly')),
    active_from_offset_minutes  INTEGER,
    active_until_offset_minutes INTEGER,
    sort_order             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_template_tasks_template ON template_tasks(template_id);
CREATE INDEX idx_template_tasks_parent   ON template_tasks(parent_id);

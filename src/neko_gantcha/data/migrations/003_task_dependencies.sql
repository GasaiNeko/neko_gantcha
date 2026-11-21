-- タスク間の前後関係(フェーズ11)。「predecessor を終えてから successor に取りかかる」。

CREATE TABLE task_dependencies (
    predecessor_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    successor_id   INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (predecessor_id, successor_id),
    CHECK (predecessor_id <> successor_id)
);
CREATE INDEX idx_task_dependencies_successor ON task_dependencies(successor_id);

-- テンプレートの中のタスクどうしの前後関係
CREATE TABLE template_dependencies (
    predecessor_id INTEGER NOT NULL REFERENCES template_tasks(id) ON DELETE CASCADE,
    successor_id   INTEGER NOT NULL REFERENCES template_tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (predecessor_id, successor_id),
    CHECK (predecessor_id <> successor_id)
);
CREATE INDEX idx_template_dependencies_successor ON template_dependencies(successor_id);

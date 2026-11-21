"""接続とマイグレーションのテスト。"""

from datetime import time
from typing import TYPE_CHECKING

import pytest

from neko_gantcha.data import db
from neko_gantcha.data.repositories import CategoryRepository

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

LATEST = 4  # migrations/ の最後の番号


def test_fresh_database_is_migrated_to_latest(conn: sqlite3.Connection) -> None:
    assert db.schema_version(conn) == LATEST
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "categories",
        "events",
        "projects",
        "tasks",
        "recurrences",
        "completions",
        "event_templates",
        "template_tasks",
    } <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_initial_categories(conn: sqlite3.Connection) -> None:
    categories = CategoryRepository(conn).list_all()
    assert [c.name for c in categories] == ["ソシャゲA", "ソシャゲB", "日常"]
    for game in categories[:2]:
        assert game.daily_reset_time == time(5, 0)
        assert game.weekly_reset_weekday == 0
        assert game.monthly_reset_day == 1
    assert categories[2].daily_reset_time == time(0, 0)


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 3


def test_database_file_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "neko_gantcha.db"
    first = db.open_database(path)
    first.execute("UPDATE categories SET color = '#000000' WHERE name = '日常'")
    first.commit()
    first.close()

    second = db.open_database(path)
    try:
        assert db.schema_version(second) == LATEST
        row = second.execute("SELECT color FROM categories WHERE name = '日常'").fetchone()
        assert row["color"] == "#000000"
    finally:
        second.close()


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("ALTER TABLE a ADD COLUMN x TEXT;", encoding="utf-8")
    (tmp_path / "README.txt").write_text("対象外", encoding="utf-8")
    connection = db.connect(":memory:")
    assert db.migrate(connection, tmp_path) == 2
    connection.execute("INSERT INTO a (id, x) VALUES (1, 'ok')")


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    (tmp_path / "002_bad.sql").write_text(
        "CREATE TABLE b (id INTEGER);\nINSERT INTO missing VALUES (1);", encoding="utf-8"
    )
    connection = db.connect(":memory:")
    with pytest.raises(db.MigrationError):
        db.migrate(connection, tmp_path)
    # 001 は残り、002 の途中までの変更は消える
    assert db.schema_version(connection) == 1
    names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert names == {"a"}


def test_newer_database_is_rejected(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(db.MigrationError):
        db.migrate(conn)


def test_non_sequential_migration_numbers_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id INTEGER);", encoding="utf-8")
    (tmp_path / "003_c.sql").write_text("CREATE TABLE c (id INTEGER);", encoding="utf-8")
    with pytest.raises(db.MigrationError):
        db.migrate(db.connect(":memory:"), tmp_path)

"""SQLite への接続とマイグレーション。

スキーマのバージョンは PRAGMA user_version に持ち、migrations/NNN_*.sql を番号順に適用する。
"""

import re
import sqlite3
from typing import TYPE_CHECKING

from neko_gantcha import paths

if TYPE_CHECKING:
    from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{3})_.+\.sql$")


class MigrationError(Exception):
    """マイグレーションを適用できなかった。"""


def default_migrations_dir() -> Path:
    """同梱のマイグレーションのフォルダを返す。

    Returns:
        migrations フォルダのパス。
    """
    return paths.resource_path("data", "migrations")


def connect(path: Path | str) -> sqlite3.Connection:
    """接続を開く。外部キーの制約を有効にし、行は列名で引けるようにする。

    Args:
        path: DB ファイルのパス。":memory:" を渡すとメモリ上の DB になる。

    Returns:
        開いた接続。
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """DB のスキーマのバージョンを返す。

    Args:
        conn: DB の接続。

    Returns:
        PRAGMA user_version の値。
    """
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migration_files(migrations_dir: Path) -> list[tuple[int, Path]]:
    """マイグレーションのファイルを番号順に返す。

    Returns:
        (番号, パス) の一覧。

    Raises:
        MigrationError: 番号が 1 からの連番になっていない場合。
    """
    found: list[tuple[int, Path]] = []
    for file in migrations_dir.iterdir():
        match = _MIGRATION_NAME.match(file.name)
        if match:
            found.append((int(match.group(1)), file))
    found.sort()
    versions = [version for version, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        msg = f"マイグレーションの番号が連番になっていません：{versions}"
        raise MigrationError(msg)
    return found


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> int:
    """未適用のマイグレーションを順に適用する。

    1ファイルを1トランザクションで適用する。失敗したファイルの変更は残さない。

    Args:
        conn: DB の接続。
        migrations_dir: マイグレーションのフォルダ。None なら同梱のもの。

    Returns:
        適用後のスキーマのバージョン。

    Raises:
        MigrationError: DB がアプリより新しいバージョンで作られている場合や、適用に失敗した場合。
    """
    files = _migration_files(migrations_dir or default_migrations_dir())
    current = schema_version(conn)
    latest = files[-1][0] if files else 0
    if current > latest:
        msg = (
            f"データベースが新しいバージョン({current})で作られています。"
            f"このアプリが扱えるのはバージョン {latest} までです。"
        )
        raise MigrationError(msg)
    for version, file in files:
        if version <= current:
            continue
        sql = file.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.rollback()
            msg = f"{file.name} の適用に失敗しました：{exc}"
            raise MigrationError(msg) from exc
        current = version
    return current


def open_database(path: Path | str, migrations_dir: Path | None = None) -> sqlite3.Connection:
    """接続を開き、最新のスキーマまでマイグレーションする。

    Args:
        path: DB ファイルのパス。":memory:" ならメモリ上の DB。
        migrations_dir: マイグレーションのフォルダ。None なら同梱のもの。

    Returns:
        開いた接続。
    """
    conn = connect(path)
    try:
        migrate(conn, migrations_dir)
    except Exception:
        conn.close()
        raise
    return conn

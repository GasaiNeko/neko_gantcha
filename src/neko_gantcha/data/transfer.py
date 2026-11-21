"""JSON へのエクスポートとインポート。

全テーブルの行をそのまま JSON に書き出す。インポートは今のデータをすべて置き換える
(別の PC への移行や、エクスポートした時点への復元に使う)。
"""

import json
import sqlite3
from typing import TYPE_CHECKING

from neko_gantcha.data.db import schema_version

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

FORMAT = "neko_gantcha"
FORMAT_VERSION = 1

# 外部キーの親から順に並べる(インポートはこの順で入れ、逆順で消す)
TABLES = (
    "categories",
    "events",
    "projects",
    "tasks",
    "recurrences",
    "completions",
    "event_templates",
    "template_tasks",
    "task_dependencies",
    "template_dependencies",
)
# 後のバージョンで増えたテーブル。古いファイルになければ空として読む
OPTIONAL_TABLES = frozenset(
    {"event_templates", "template_tasks", "task_dependencies", "template_dependencies"}
)

# エクスポートの中身(JSON のオブジェクト)と、テーブルの1行
type ExportData = dict[str, object]
type TableRow = dict[str, object]


class TransferError(Exception):
    """ファイルを読み込めない、または形式が合わない。"""


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """テーブルの列名を定義の順に返す。

    Returns:
        列名の一覧。
    """
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def export_data(conn: sqlite3.Connection, now: datetime) -> ExportData:
    """全テーブルの内容を JSON にできる dict にする。

    Args:
        conn: DB の接続。
        now: 現在の日時(エクスポートした日時として書く)。

    Returns:
        形式の情報と、テーブル名 → 行の一覧。
    """
    data: ExportData = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "schema_version": schema_version(conn),
        "exported_at": now.isoformat(timespec="seconds"),
    }
    for table in TABLES:
        columns = _columns(conn, table)
        order = "id" if "id" in columns else ", ".join(columns[:2])
        # テーブル名と列名は固定の一覧と PRAGMA から取ったもので、外からの入力を含まない
        sql = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"  # noqa: S608
        data[table] = [dict(zip(columns, row, strict=True)) for row in conn.execute(sql)]
    return data


def write_export(conn: sqlite3.Connection, path: Path, now: datetime) -> None:
    """全テーブルの内容を JSON のファイルに書き出す。

    Args:
        conn: DB の接続。
        path: 書き出すファイルのパス。
        now: 現在の日時。
    """
    text = json.dumps(export_data(conn, now), ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def _table_rows(data: ExportData, table: str) -> list[TableRow]:
    """エクスポートの中身から、テーブルの行の一覧を取り出す。

    Returns:
        行の一覧。

    Raises:
        TransferError: 行の一覧になっていない場合。
    """
    rows = data.get(table)
    if not isinstance(rows, list):
        msg = f"「{table}」のデータが正しくありません。"
        raise TransferError(msg)
    result: list[TableRow] = []
    for row in rows:
        if not isinstance(row, dict):
            msg = f"「{table}」のデータが正しくありません。"
            raise TransferError(msg)
        result.append({str(key): value for key, value in row.items()})
    return result


def read_export(path: Path) -> ExportData:
    """エクスポートしたファイルを読み、形式を確かめる。

    Args:
        path: 読み込むファイルのパス。

    Returns:
        エクスポートの中身。後から増えたテーブルがなければ空の一覧を入れる。

    Raises:
        TransferError: ファイルを読み込めない場合や、形式が合わない場合。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"ファイルを読み込めません：{exc}"
        raise TransferError(msg) from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        msg = "ねこがんちゃからエクスポートしたファイルではありません。"
        raise TransferError(msg)
    if data.get("format_version") != FORMAT_VERSION:
        msg = f"対応していない形式のバージョンです：{data.get('format_version')}"
        raise TransferError(msg)
    result: ExportData = {str(key): value for key, value in data.items()}
    for table in TABLES:
        if table in OPTIONAL_TABLES and table not in result:
            result[table] = []
        _table_rows(result, table)
    return result


def import_data(conn: sqlite3.Connection, data: ExportData) -> dict[str, int]:
    """今のデータをすべて消して data の内容に置き換える。

    1トランザクションで行い、途中で失敗したら何も変えない。
    エクスポート元のスキーマが古くて列が足りない場合は、列の既定値を使う。

    Args:
        conn: DB の接続。
        data: エクスポートの中身。

    Returns:
        テーブル名 → 取り込んだ行数。

    Raises:
        TransferError: 新しいバージョンのアプリで作られたファイルの場合や、取り込めない場合。
    """
    version = data.get("schema_version", 0)
    if not isinstance(version, int):
        msg = "スキーマのバージョンが正しくありません。"
        raise TransferError(msg)
    if version > schema_version(conn):
        msg = (
            "このファイルは新しいバージョンのねこがんちゃで作られています。"
            "アプリを更新してください。"
        )
        raise TransferError(msg)
    tables = {table: _table_rows(data, table) for table in TABLES}
    try:
        with conn:
            _replace_all(conn, tables)
    except sqlite3.Error as exc:
        msg = f"データを取り込めません：{exc}"
        raise TransferError(msg) from exc
    return {table: len(rows) for table, rows in tables.items()}


def _replace_all(conn: sqlite3.Connection, tables: dict[str, list[TableRow]]) -> None:
    """全テーブルの行を消し、tables の行を入れる。トランザクションは呼び出し側で張る。

    Raises:
        TransferError: 入れた行の参照関係が壊れている場合。
    """
    for table in reversed(TABLES):
        # テーブル名は固定の一覧から取ったもので、外からの入力を含まない
        conn.execute(f"DELETE FROM {table}")  # noqa: S608
    for table in TABLES:
        _insert_rows(conn, table, tables[table])
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        msg = "データの参照関係が壊れています。"
        raise TransferError(msg)


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[TableRow]) -> None:
    """テーブルに行を入れる。今のスキーマにない列は捨てる。"""
    known = set(_columns(conn, table))
    for row in rows:
        values = {key: value for key, value in row.items() if key in known}
        if not values:
            continue
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        # テーブル名と列名は今のスキーマにあるものだけで、値はプレースホルダで渡す
        sql = f"INSERT INTO {table} ({columns}) VALUES ({marks})"  # noqa: S608
        conn.execute(sql, tuple(values.values()))

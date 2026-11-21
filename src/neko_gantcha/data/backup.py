"""DB ファイルのバックアップ。

SQLite のオンラインバックアップで、開いたままの DB を1ファイルに写す。
自動バックアップは起動時に1日1回とり、古いものから消して世代数を保つ。
"""

import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

AUTO_KEEP = 14  # 自動バックアップを残す世代数
_AUTO_DASHES = 2  # 自動バックアップの名前の「-」の数(neko_gantcha-日付-時刻.db)
_PREFIX = "neko_gantcha-"
_NAME = re.compile(r"^neko_gantcha-(\d{8})-(\d{6})(?:-[a-z0-9-]+)?\.db$")


def backup_name(now: datetime, label: str = "") -> str:
    """バックアップのファイル名を作る。

    Args:
        now: とった日時。
        label: 手動や読み込み前などの印。空なら自動バックアップの名前になる。

    Returns:
        `neko_gantcha-20261116-120000.db` の形の名前。
    """
    suffix = f"-{label}" if label else ""
    return f"{_PREFIX}{now:%Y%m%d-%H%M%S}{suffix}.db"


def create_backup(
    conn: sqlite3.Connection, folder: Path, now: datetime, *, label: str = ""
) -> Path:
    """DB の写しを folder に作る。

    Args:
        conn: DB の接続。
        folder: バックアップのフォルダ。なければ作る。
        now: 現在の日時。
        label: 手動や読み込み前などの印。

    Returns:
        作ったファイルのパス。
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / backup_name(now, label)
    index = 1
    while target.exists():  # 同じ秒に2回とったときに上書きしない
        index += 1
        target = folder / backup_name(now, f"{label}{index}" if label else f"n{index}")
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def list_backups(folder: Path) -> list[Path]:
    """フォルダにあるバックアップを古い順に返す。

    Args:
        folder: バックアップのフォルダ。

    Returns:
        バックアップのパス。フォルダがなければ空。
    """
    if not folder.is_dir():
        return []
    found = [path for path in folder.iterdir() if _NAME.match(path.name)]
    return sorted(found, key=_sort_key)


def _sort_key(path: Path) -> tuple[str, str, str]:
    """日時の順に並べるためのキー。同じ日時なら名前の順。

    Returns:
        (日付, 時刻, ファイル名)。

    Raises:
        ValueError: バックアップの名前でない場合。
    """
    match = _NAME.match(path.name)
    if match is None:
        msg = f"バックアップの名前ではありません：{path.name}"
        raise ValueError(msg)
    return str(match.group(1)), str(match.group(2)), path.name


def _is_auto(path: Path) -> bool:
    """自動でとったバックアップか。

    Returns:
        印のないバックアップなら True。
    """
    match = _NAME.match(path.name)
    return match is not None and path.name.count("-") == _AUTO_DASHES


def prune_backups(folder: Path, *, keep: int = AUTO_KEEP) -> list[Path]:
    """自動バックアップを新しい keep 個だけ残して消す。手動のものは消さない。

    Args:
        folder: バックアップのフォルダ。
        keep: 残す世代数。

    Returns:
        消したファイルのパス。
    """
    autos = [path for path in list_backups(folder) if _is_auto(path)]
    removed = autos[: max(0, len(autos) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def auto_backup(
    conn: sqlite3.Connection, folder: Path, now: datetime, *, keep: int = AUTO_KEEP
) -> Path | None:
    """その日の自動バックアップがまだなければとり、古い世代を消す。

    Args:
        conn: DB の接続。
        folder: バックアップのフォルダ。
        now: 現在の日時。
        keep: 残す世代数。

    Returns:
        とったファイルのパス。その日の分がすでにあれば None。
    """
    today = f"{now:%Y%m%d}"
    if any(_sort_key(path)[0] == today and _is_auto(path) for path in list_backups(folder)):
        return None
    path = create_backup(conn, folder, now)
    prune_backups(folder, keep=keep)
    return path

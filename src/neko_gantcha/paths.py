"""データフォルダと同梱リソースのパス解決。

基準が2つあるので関数を分けている。

- データフォルダ: exe の隣(開発実行時はプロジェクトルート)。書き込みが要る。
- 同梱リソース: PyInstaller の展開先(_internal)内の neko_gantcha パッケージ相当。読み取り専用。
"""

import sys
from pathlib import Path

DATA_DIR_NAME = "data"
BACKUP_DIR_NAME = "backup"
DB_FILE_NAME = "neko_gantcha.db"


def is_frozen() -> bool:
    """PyInstaller で作った exe として動いているか。

    Returns:
        exe なら True。
    """
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """データフォルダを置く基準フォルダを返す。

    Returns:
        exe ならその隣、開発実行時はプロジェクトルート。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/neko_gantcha/paths.py から見たプロジェクトルート
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """データフォルダを返す。

    Returns:
        基準フォルダの下の data フォルダ。
    """
    return app_root() / DATA_DIR_NAME


def backup_dir() -> Path:
    """バックアップのフォルダを返す。

    Returns:
        データフォルダの下の backup フォルダ。
    """
    return data_dir() / BACKUP_DIR_NAME


def db_path() -> Path:
    """DB ファイルのパスを返す。

    Returns:
        データフォルダの下の DB ファイル。
    """
    return data_dir() / DB_FILE_NAME


def ensure_data_dir() -> Path:
    """データフォルダを作り、書き込めることを確かめる。

    作成や書き込みができなければ OSError がそのまま伝わる。

    Returns:
        データフォルダのパス。
    """
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_test"
    probe.write_text("", encoding="utf-8")
    probe.unlink()
    return path


def resource_root() -> Path:
    """同梱リソースの基準フォルダ(neko_gantcha パッケージの位置)を返す。

    Returns:
        exe なら展開先の neko_gantcha フォルダ、開発実行時はパッケージのフォルダ。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass:
        return Path(meipass) / "neko_gantcha"
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """同梱リソースのパスを返す。

    Args:
        *parts: 基準フォルダからのパスの要素。例: "ui", "resources", "style.qss"。

    Returns:
        同梱リソースのパス。
    """
    return resource_root().joinpath(*parts)

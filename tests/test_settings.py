"""アプリの設定と配色の切り替え(フェーズ12)のテスト。"""

import json
from typing import TYPE_CHECKING

from conftest import Recorder

from neko_gantcha.data import db, transfer
from neko_gantcha.data.repositories import SettingsRepository
from neko_gantcha.domain.models import Theme
from neko_gantcha.services.store import THEME_KEY, AppStore

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from conftest import Clock


def test_settings_repository_get_and_overwrite(conn: sqlite3.Connection) -> None:
    settings = SettingsRepository(conn)
    assert settings.get("theme") is None
    settings.set("theme", "dark")
    settings.set("theme", "light")
    assert settings.get("theme") == "light"
    assert conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0] == 1


def test_theme_defaults_to_system_and_survives_reopen(tmp_path: Path, clock: Clock) -> None:
    path = tmp_path / "neko_gantcha.db"
    conn = db.open_database(path)
    store = AppStore(conn, clock)
    assert store.theme() == Theme.SYSTEM
    changed = Recorder(store.theme_changed)
    store.set_theme(Theme.DARK)
    store.set_theme(Theme.DARK)  # 同じ値なら知らせない
    store.set_theme("light")  # コンボボックスから来る素の str も受ける
    assert changed.calls == [("dark",), ("light",)]
    conn.close()

    # 再起動後も保たれる
    conn = db.open_database(path)
    try:
        assert AppStore(conn, clock).theme() == Theme.LIGHT
    finally:
        conn.close()


def test_unknown_theme_value_falls_back_to_system(
    store: AppStore, conn: sqlite3.Connection
) -> None:
    SettingsRepository(conn).set(THEME_KEY, "sepia")
    assert store.theme() == Theme.SYSTEM


def test_theme_is_not_exported_and_kept_on_import(store: AppStore, tmp_path: Path) -> None:
    store.set_theme(Theme.DARK)
    path = tmp_path / "export.json"
    store.export_json(path)
    assert "app_settings" not in json.loads(path.read_text(encoding="utf-8"))

    store.set_theme(Theme.LIGHT)
    transfer.import_data(store._conn, transfer.read_export(path))
    assert store.theme() == Theme.LIGHT  # 端末ごとの好みはインポートで上書きしない

"""起動点。`python -m neko_gantcha` と NekoGantcha.exe の両方から呼ばれる。"""

import contextlib
import sqlite3
import sys
from pathlib import Path

import PySide6
from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from neko_gantcha import paths
from neko_gantcha.data import db
from neko_gantcha.services.store import AppStore
from neko_gantcha.ui.main_window import MainWindow
from neko_gantcha.ui.theme import apply_theme


def _load_stylesheet() -> str:
    qss = paths.resource_path("ui", "resources", "style.qss")
    try:
        return qss.read_text(encoding="utf-8")
    except OSError:
        return ""


def _install_translations(app: QApplication) -> None:
    """標準ダイアログ(色の選択、ファイルの選択など)のボタンや文言を日本語にする。"""
    folders = [
        Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)),
        Path(PySide6.__file__).resolve().parent / "translations",
    ]
    for name in ("qtbase_ja", "qt_ja"):
        for folder in folders:
            translator = QTranslator(app)
            if translator.load(QLocale(QLocale.Language.Japanese), name, "", str(folder)) or (
                translator.load(name, str(folder))
            ):
                app.installTranslator(translator)
                break


def main() -> int:
    """アプリを起動し、ウィンドウを閉じるまで動かす。

    Returns:
        終了コード。データフォルダや DB を開けなければ 1。
    """
    app = QApplication(sys.argv)
    app.setApplicationName("NekoGantcha")
    _install_translations(app)
    app.setWindowIcon(QIcon(str(paths.resource_path("ui", "resources", "neko_gantcha.ico"))))

    try:
        data_dir = paths.ensure_data_dir()
    except OSError as exc:
        QMessageBox.critical(
            None,
            "ねこがんちゃ",
            "データフォルダに書き込めないため起動できません。\n"
            "Program Files 以外の書き込み可能な場所に置いてください。\n\n"
            f"場所: {paths.data_dir()}\n原因: {exc}",
        )
        return 1

    try:
        conn = db.open_database(paths.db_path())
    except (db.MigrationError, sqlite3.Error, OSError) as exc:
        QMessageBox.critical(
            None,
            "ねこがんちゃ",
            f"データベースを開けないため起動できません。\n\n場所: {paths.db_path()}\n原因: {exc}",
        )
        return 1

    app.setStyleSheet(_load_stylesheet())
    store = AppStore(conn)
    apply_theme(store.theme())
    # バックアップに失敗しても起動は続ける。次回の起動でまた試す
    with contextlib.suppress(OSError, sqlite3.Error):
        store.auto_backup(paths.backup_dir())
    store.start_clock()
    window = MainWindow(store, data_dir)
    window.show()
    try:
        return app.exec()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

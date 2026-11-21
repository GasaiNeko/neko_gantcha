"""メインウィンドウ。左: 絞り込み / 中央: ToDo・カレンダー・ガント / 右: 詳細。"""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
)

from neko_gantcha import paths
from neko_gantcha.data.transfer import TransferError
from neko_gantcha.services.store import VIEW_CALENDAR, VIEW_GANTT, VIEW_TODO
from neko_gantcha.ui.calendar_view import CalendarView
from neko_gantcha.ui.detail_panel import DetailPanel
from neko_gantcha.ui.dialogs.settings_dialog import SettingsDialog
from neko_gantcha.ui.filter_panel import FilterPanel
from neko_gantcha.ui.gantt_view import GanttView
from neko_gantcha.ui.theme import apply_theme, reload_stylesheet
from neko_gantcha.ui.todo_view import TodoView

if TYPE_CHECKING:
    from neko_gantcha.services.store import AppStore


class MainWindow(QMainWindow):
    """メインウィンドウ。左に絞り込み、中央に3つのビュー、右に詳細を並べる。

    Args:
        store: データ操作の窓口。
        data_dir: データフォルダ。
    """

    def __init__(self, store: AppStore, data_dir: Path) -> None:
        super().__init__()
        self._store = store
        self._data_dir = data_dir
        self._backup_dir = data_dir / paths.BACKUP_DIR_NAME
        self.setWindowTitle("ねこがんちゃ")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)

        self.filter_panel = FilterPanel(store)
        self.todo_view = TodoView(store)
        self.calendar_view = CalendarView(store)
        self.gantt_view = GanttView(store)
        self.detail_panel = DetailPanel(store)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.todo_view, "ToDo")
        self.tabs.addTab(self.calendar_view, "カレンダー")
        self.tabs.addTab(self.gantt_view, "ガント")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.filter_panel)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([210, 710, 360])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        self._views = {
            VIEW_TODO: self.todo_view,
            VIEW_CALENDAR: self.calendar_view,
            VIEW_GANTT: self.gantt_view,
        }
        store.reveal_requested.connect(self._show_view)
        store.theme_changed.connect(apply_theme)
        # 設定画面での切り替えも、Windows 側の切り替えも、ここで受けて描き直す
        QApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)
        self._restyle_pending = False
        self._build_menu()

        self.statusBar().showMessage(f"データフォルダ: {data_dir}")
        self.todo_view.focus_input()

    # --- メニュー ---

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction("JSON にエクスポート…", self.export_json)
        file_menu.addAction("JSON からインポート…", self.import_json)
        file_menu.addSeparator()
        file_menu.addAction("今すぐバックアップ", self.backup_now)
        file_menu.addAction("バックアップのフォルダを開く", self.open_backup_dir)
        file_menu.addSeparator()
        file_menu.addAction("終了", QKeySequence("Ctrl+Q"), self.close)
        settings = self.menuBar().addAction("設定(&S)…")
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(self.open_settings)

    def open_settings(self) -> None:
        """設定画面を開く。"""
        SettingsDialog(self._store, self).exec()

    def export_json(self) -> None:
        """保存先を選ばせて、全データを JSON に書き出す。"""
        default = self._data_dir / f"neko_gantcha-{self._store.now():%Y%m%d}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "JSON にエクスポート", str(default), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._store.export_json(Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "エクスポート", f"書き出せませんでした。\n{exc}")
            return
        self.statusBar().showMessage(f"エクスポートしました: {path}", 10_000)

    def import_json(self) -> None:
        """ファイルを選ばせ、確認のうえで今のデータを JSON の内容に置き換える。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "JSON からインポート", str(self._data_dir), "JSON (*.json)"
        )
        if not path:
            return
        answer = QMessageBox.warning(
            self,
            "インポート",
            "今のデータをすべて、選んだファイルの内容に置き換えます。\n"
            "置き換える前の状態はバックアップのフォルダに保存します。\n\n続けますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            counts = self._store.import_json(Path(path), self._backup_dir)
        except (TransferError, OSError) as exc:
            QMessageBox.warning(self, "インポート", f"読み込めませんでした。\n{exc}")
            return
        QMessageBox.information(
            self,
            "インポート",
            f"読み込みました。タスク {counts.get('tasks', 0)} 件、"
            f"イベント {counts.get('events', 0)} 件、プロジェクト {counts.get('projects', 0)} 件。",
        )

    def backup_now(self) -> None:
        """今の DB をバックアップのフォルダに写す。"""
        try:
            path = self._store.backup_now(self._backup_dir)
        except OSError as exc:
            QMessageBox.warning(self, "バックアップ", f"バックアップできませんでした。\n{exc}")
            return
        self.statusBar().showMessage(f"バックアップしました: {path.name}", 10_000)

    def open_backup_dir(self) -> None:
        """バックアップのフォルダをエクスプローラーで開く。"""
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._backup_dir)))

    def _show_view(self, name: str) -> None:
        """「○○で表示」で指定されたビューのタブに切り替える。"""
        view = self._views.get(name)
        if view is not None:
            self.tabs.setCurrentWidget(view)

    # --- 配色 ---

    def _on_color_scheme_changed(self) -> None:
        """配色が変わったら、描き直しを1回にまとめて予約する。"""
        if self._restyle_pending:
            return
        self._restyle_pending = True
        QTimer.singleShot(0, self._restyle)

    def _restyle(self) -> None:
        """配色が変わったあと、スタイルシートとパレットから作った色を作り直す。"""
        self._restyle_pending = False
        app = QApplication.instance()
        if isinstance(app, QApplication):
            reload_stylesheet(app)
        self.todo_view.rebuild()
        self.calendar_view.refresh()
        self.gantt_view.refresh()

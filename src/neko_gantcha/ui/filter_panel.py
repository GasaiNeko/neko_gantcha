"""左ペイン: 分類とプロジェクトの絞り込み、イベントの一覧、完了済み表示の切り替え。"""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.models import ProjectStatus
from neko_gantcha.domain.todo import format_event_status
from neko_gantcha.services.store import VIEW_GANTT, ItemKind, ItemRef
from neko_gantcha.ui.dialogs.event_dialog import confirm_delete_event, duplicate_event, edit_event
from neko_gantcha.ui.dialogs.project_dialog import confirm_delete_project, edit_project
from neko_gantcha.ui.dialogs.template_dialog import create_from_template, save_as_template
from neko_gantcha.ui.widgets import color_icon, format_datetime

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QAction

    from neko_gantcha.domain.models import Category, Project
    from neko_gantcha.domain.project import Progress
    from neko_gantcha.services.store import AppStore

_ROLE_ID = Qt.ItemDataRole.UserRole
_PROJECT_ROWS = 4  # プロジェクト一覧の高さ(この行数を超えたらスクロール)


def _title(text: str) -> QLabel:
    """ペインの見出しを作る。

    Returns:
        見出しのラベル。
    """
    label = QLabel(text)
    label.setObjectName("paneTitle")
    return label


def _filter_list() -> QListWidget:
    """絞り込みの一覧を作る。

    Returns:
        一覧のウィジェット。
    """
    widget = QListWidget()
    widget.setObjectName("filterList")
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return widget


def _fit_height(widget: QListWidget, rows: int) -> None:
    """一覧の高さを、指定した行数がちょうど見える高さにする。"""
    height = sum(widget.sizeHintForRow(row) for row in range(rows))
    widget.setFixedHeight(height + 2 * widget.frameWidth() + 4)


def _project_item(
    project: Project, stats: Progress | None, category: Category | None
) -> QListWidgetItem:
    """プロジェクトの一覧の1行を作る。

    Returns:
        名前、完了率、目標日を出す行。
    """
    percent = stats.percent_text() if stats is not None else "—"
    target = f"{project.target_date:%m/%d}まで" if project.target_date is not None else "目標日なし"
    item = QListWidgetItem(f"{project.name}\n{percent}  {target}")
    tips = [project.name]
    if project.goal:
        tips.append(project.goal)
    if stats is not None:
        tips.append(f"完了 {stats.done}/{stats.total}")
    item.setToolTip("\n".join(tips))
    if category is not None:
        item.setIcon(color_icon(category.color))
    if project.status is ProjectStatus.DONE:
        item.setForeground(Qt.GlobalColor.gray)
    item.setData(_ROLE_ID, project.id)
    return item


def _run_menu(menu: QMenu, actions: dict[QAction, Callable[[], object]], pos: QPoint) -> None:
    """メニューを出し、選ばれた項目の処理を行う。何も選ばなければ何もしない。"""
    chosen = menu.exec(pos)
    action = actions.get(chosen)
    if action is not None:
        action()


class FilterPanel(QWidget):
    """左ペイン。分類とプロジェクトの絞り込み、イベントの一覧を出す。

    Args:
        store: データ操作の窓口。
        parent: 親のウィジェット。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._building = False
        self._events_pending = False
        self._projects_pending = False
        self._show_done = QCheckBox("完了・終了済みを表示")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 4, 8)
        self._build_categories(layout)
        layout.addSpacing(6)
        self._build_projects(layout)
        layout.addSpacing(6)
        self._build_events(layout)
        layout.addWidget(self._show_done)
        self._show_done.toggled.connect(lambda checked: store.set_filter(show_done=checked))

        store.categories_changed.connect(self._rebuild_categories)
        store.categories_changed.connect(self._schedule_events)
        store.categories_changed.connect(self._schedule_projects)
        store.events_changed.connect(self._schedule_events)
        store.filter_changed.connect(self._schedule_events)
        store.filter_changed.connect(self._schedule_projects)
        store.filter_changed.connect(self._sync_category)
        store.projects_changed.connect(self._schedule_projects)
        store.tasks_changed.connect(self._schedule_projects)  # 完了率が変わる
        store.minute_ticked.connect(self._schedule_events)
        store.selection_changed.connect(self._on_selection_changed)
        self._rebuild_categories()
        self.rebuild_projects()
        self.rebuild_events()

    def _build_categories(self, layout: QVBoxLayout) -> None:
        """分類の見出しと一覧を並べる。"""
        self._categories = _filter_list()
        layout.addWidget(_title("分類"))
        layout.addWidget(self._categories)
        self._categories.currentItemChanged.connect(self._on_category_changed)

    def _build_projects(self, layout: QVBoxLayout) -> None:
        """プロジェクトの見出し、追加ボタン、一覧を並べる。"""
        add_project = QToolButton()
        add_project.setText("+")
        add_project.setToolTip("プロジェクトを追加")
        header = QHBoxLayout()
        header.addWidget(_title("プロジェクト"), 1)
        header.addWidget(add_project)
        self._projects = _filter_list()
        self._projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._projects.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addLayout(header)
        layout.addWidget(self._projects)

        self._projects.currentItemChanged.connect(self._on_project_changed)
        self._projects.itemDoubleClicked.connect(self._edit_project_item)
        self._projects.customContextMenuRequested.connect(self._show_project_menu)
        add_project.clicked.connect(self._add_project)

    def _build_events(self, layout: QVBoxLayout) -> None:
        """イベントの見出し、一覧、追加ボタンを並べる。"""
        self._events = _filter_list()
        self._events.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._events.setTextElideMode(Qt.TextElideMode.ElideRight)
        add_event = QPushButton("イベントを追加…")
        from_template = QPushButton("テンプレートから…")
        from_template.setToolTip("保存したテンプレートから、イベントと配下のタスクを作る")
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        buttons.addWidget(add_event, 1)
        buttons.addWidget(from_template, 1)
        layout.addWidget(_title("イベント"))
        layout.addWidget(self._events, 1)
        layout.addLayout(buttons)

        self._events.currentItemChanged.connect(self._on_event_changed)
        self._events.itemDoubleClicked.connect(self._edit_event_item)
        self._events.customContextMenuRequested.connect(self._show_event_menu)
        add_event.clicked.connect(self._add_event)
        from_template.clicked.connect(lambda: create_from_template(self, self._store))

    # --- 分類 ---

    def _rebuild_categories(self) -> None:
        """分類の一覧を作り直す。"""
        self._building = True
        self._categories.clear()
        everything = QListWidgetItem("すべて")
        everything.setData(_ROLE_ID, None)
        self._categories.addItem(everything)
        selected_row = 0
        for row, category in enumerate(self._store.categories(), start=1):
            item = QListWidgetItem(color_icon(category.color), category.name)
            item.setData(_ROLE_ID, category.id)
            self._categories.addItem(item)
            if category.id == self._store.filter.category_id:
                selected_row = row
        self._categories.setCurrentRow(selected_row)
        self._building = False
        # 分類は全件が見える高さにし、残りをイベント一覧に使う
        _fit_height(self._categories, self._categories.count())

    def _sync_category(self) -> None:
        """プロジェクトを選んで分類の絞り込みが変わったときに、分類の選択を合わせる。"""
        target = self._store.filter.category_id
        for row in range(self._categories.count()):
            if self._categories.item(row).data(_ROLE_ID) == target:
                if self._categories.currentRow() != row:
                    self._building = True
                    self._categories.setCurrentRow(row)
                    self._building = False
                return

    def _on_category_changed(self, current: QListWidgetItem | None) -> None:
        """分類を選んだら絞り込みを変える。"""
        if self._building or current is None:
            return
        self._store.set_filter(category_id=current.data(_ROLE_ID))

    # --- プロジェクト ---

    def _schedule_projects(self) -> None:
        """プロジェクトの一覧の作り直しを1回にまとめて予約する。"""
        if self._projects_pending:
            return
        self._projects_pending = True
        QTimer.singleShot(0, self.rebuild_projects)

    def rebuild_projects(self) -> None:
        """プロジェクトの一覧を作り直す。"""
        self._projects_pending = False
        store = self._store
        categories = {c.saved_id: c for c in store.categories(include_archived=True)}
        progress = store.project_progress()
        self._building = True
        self._projects.clear()
        everything = QListWidgetItem("すべて")
        everything.setData(_ROLE_ID, None)
        self._projects.addItem(everything)
        selected_row = 0
        for row, project in enumerate(store.projects(), start=1):
            stats = progress.get(project.saved_id)
            category = categories.get(project.category_id)
            self._projects.addItem(_project_item(project, stats, category))
            if project.id == store.filter.project_id:
                selected_row = row
        self._projects.setCurrentRow(selected_row)
        self._building = False
        # 少ないときは全件が見える高さ、多いときは数行ぶんでスクロール
        _fit_height(self._projects, min(self._projects.count(), _PROJECT_ROWS))

    def _on_project_changed(self, current: QListWidgetItem | None) -> None:
        """プロジェクトを選んだら絞り込みを変え、そのプロジェクトを選択する。"""
        if self._building or current is None:
            return
        project_id = current.data(_ROLE_ID)
        self._store.set_filter(project_id=project_id)
        if project_id is not None:
            self._store.select(ItemRef(ItemKind.PROJECT, project_id))

    def _add_project(self) -> None:
        """プロジェクトを追加し、それで絞り込む。"""
        project = edit_project(self, self._store)
        if project is not None:
            self._store.set_filter(project_id=project.id)
            self._store.select(ItemRef(ItemKind.PROJECT, project.saved_id))

    def _edit_project_item(self, item: QListWidgetItem) -> None:
        """ダブルクリックしたプロジェクトを編集する。"""
        project_id = item.data(_ROLE_ID)
        if project_id is not None:
            edit_project(self, self._store, project_id)

    def _show_project_menu(self, pos: QPoint) -> None:
        """プロジェクトの一覧の右クリックメニューを出す。"""
        item = self._projects.itemAt(pos)
        project_id = item.data(_ROLE_ID) if item is not None else None
        menu = QMenu(self)
        actions: dict[QAction, Callable[[], object]] = {}
        if project_id is not None:
            ref = ItemRef(ItemKind.PROJECT, int(project_id))
            actions[menu.addAction("編集…")] = lambda: edit_project(self, self._store, ref.id)
            actions[menu.addAction("ガントで表示")] = lambda: self._store.reveal(ref, VIEW_GANTT)
            menu.addSeparator()
            actions[menu.addAction("削除…")] = lambda: confirm_delete_project(
                self, self._store, ref.id
            )
            menu.addSeparator()
        actions[menu.addAction("プロジェクトを追加…")] = self._add_project
        _run_menu(menu, actions, self._projects.viewport().mapToGlobal(pos))

    # --- イベント ---

    def _schedule_events(self) -> None:
        """イベントの一覧の作り直しを1回にまとめて予約する。"""
        if self._events_pending:
            return
        self._events_pending = True
        QTimer.singleShot(0, self.rebuild_events)

    def rebuild_events(self) -> None:
        """イベントの一覧を作り直す。"""
        self._events_pending = False
        store = self._store
        now = store.now()
        categories = {c.saved_id: c for c in store.categories(include_archived=True)}
        self._building = True
        self._events.clear()
        for event in store.events():
            category = categories.get(event.category_id)
            period = f"{event.start_at:%m/%d}〜{event.end_at:%m/%d}"
            status = (
                "開催前" if now < event.start_at else "開催中" if now < event.end_at else "終了"
            )
            item = QListWidgetItem(f"{event.name}\n{period} {status}")
            item.setToolTip(
                f"{event.name}\n{format_datetime(event.start_at)} 〜 "
                f"{format_datetime(event.end_at)}\n{format_event_status(event, now)}"
            )
            if category is not None:
                item.setIcon(color_icon(category.color))
            item.setData(_ROLE_ID, event.id)
            if event.end_at <= now:
                item.setForeground(Qt.GlobalColor.gray)
            self._events.addItem(item)
        self._select_event(store.selected)
        self._building = False

    def _select_event(self, ref: ItemRef | None) -> None:
        """一覧で、選択中のイベントの行を選ぶ。イベント以外なら選択を外す。"""
        target = ref.id if ref is not None and ref.kind is ItemKind.EVENT else None
        for row in range(self._events.count()):
            if self._events.item(row).data(_ROLE_ID) == target:
                self._events.setCurrentRow(row)
                return
        self._events.setCurrentRow(-1)

    def _on_selection_changed(self, ref: ItemRef | None) -> None:
        """ほかの画面で選択が変わったら、一覧の選択を合わせる。"""
        if self._events_pending:
            return
        was_building = self._building
        self._building = True
        self._select_event(ref)
        self._building = was_building

    def _on_event_changed(self, current: QListWidgetItem | None) -> None:
        """一覧でイベントを選んだら、そのイベントを選択する。"""
        if self._building or current is None:
            return
        self._store.select(ItemRef(ItemKind.EVENT, current.data(_ROLE_ID)))

    def _add_event(self) -> None:
        """イベントを追加して選択する。"""
        event = edit_event(self, self._store)
        if event is not None:
            self._store.select(ItemRef(ItemKind.EVENT, event.saved_id))

    def _edit_event_item(self, item: QListWidgetItem) -> None:
        """ダブルクリックしたイベントを編集する。"""
        edit_event(self, self._store, item.data(_ROLE_ID))

    def _show_event_menu(self, pos: QPoint) -> None:
        """イベントの一覧の右クリックメニューを出す。"""
        item = self._events.itemAt(pos)
        if item is None:
            return
        event_id = int(item.data(_ROLE_ID))
        store = self._store
        menu = QMenu(self)
        actions: dict[QAction, Callable[[], object]] = {
            menu.addAction("編集…"): lambda: edit_event(self, store, event_id),
            menu.addAction("複製…"): lambda: duplicate_event(self, store, event_id),
            menu.addAction("テンプレートに保存…"): lambda: save_as_template(self, store, event_id),
        }
        menu.addSeparator()
        actions[menu.addAction("削除…")] = lambda: confirm_delete_event(self, store, event_id)
        _run_menu(menu, actions, self._events.viewport().mapToGlobal(pos))

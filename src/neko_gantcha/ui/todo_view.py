"""ToDo リスト。セクションごとにタスクを並べ、チェックと回数の増減ができる。

サブタスクは親の行の下に字下げして並べる。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.models import TaskStatus
from neko_gantcha.domain.todo import Section, format_row_remaining
from neko_gantcha.services.store import VIEW_CALENDAR, VIEW_GANTT, ItemKind, ItemRef
from neko_gantcha.ui.dialogs.dependency_dialog import add_dependency_interactive
from neko_gantcha.ui.widgets import color_icon, format_datetime, mix_colors

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QAction

    from neko_gantcha.domain.models import Category, Project
    from neko_gantcha.domain.project import Progress
    from neko_gantcha.domain.todo import SectionData, TodoRow
    from neko_gantcha.services.store import AppStore

_ROLE_TASK_ID = Qt.ItemDataRole.UserRole
_ROLE_SECTION = Qt.ItemDataRole.UserRole + 1

_COL_TITLE, _COL_PROGRESS, _COL_DUE, _COL_CATEGORY = range(4)

_SECTION_TITLES = {
    Section.DAILY: "今日の日課",
    Section.WEEKLY: "今週の週課",
    Section.MONTHLY: "今月の月課",
    Section.ACTIVE_EVENT: "開催中イベントのタスク",
    Section.DATED: "期限つき",
    Section.STANDING: "恒常",
    Section.DONE: "完了済み",
}

_RECURRING_SECTIONS = (Section.DAILY, Section.WEEKLY, Section.MONTHLY)

_COLOR_OVERDUE = QColor("#E53935")
_COLOR_MUTED = QColor("#9E9E9E")
_MINUS = chr(0x2212)  # 数学のマイナス記号。ハイフンより「+」と幅がそろう


@dataclass(frozen=True, slots=True)
class _RowContext:
    """行を作るのに使う、タスク以外の情報。

    Attributes:
        now: 現在の日時。
        categories: 分類 id → 分類。
        projects: プロジェクト id → プロジェクト。
        subtasks: 親タスク id → サブタスクの完了数。
    """

    now: datetime
    categories: Mapping[int, Category]
    projects: Mapping[int, Project]
    subtasks: Mapping[int, Progress]


def _title_text(row: TodoRow, subtasks: Mapping[int, Progress]) -> str:
    """タスク名の欄の文字列を作る。

    Returns:
        優先度の印、着手中の印、サブタスクの完了数をつけたタスク名。
    """
    task = row.task
    title = task.title
    if task.status is TaskStatus.DOING:
        title = f"▶ {title}"
    if task.priority > 0:
        title = f"{'!' * task.priority} {title}"
    progress = subtasks.get(task.saved_id)
    if progress is not None:
        title = f"{title}({progress.done}/{progress.total})"
    return title


def _due_tooltip(row: TodoRow) -> str:
    """期限の欄のツールチップを作る。

    Returns:
        次のリセット、開始、期限、完了の日時を並べた文字列。
    """
    tips: list[str] = []
    if row.is_recurring and row.period is not None:
        tips.append(f"次のリセット: {format_datetime(row.period.end)}")
    if not row.is_recurring and row.start is not None:
        tips.append(f"開始: {format_datetime(row.start)}")
    if not row.is_recurring and row.due is not None:
        tips.append(f"期限: {format_datetime(row.due)}")
    if row.task.completed_at is not None:
        tips.append(f"完了: {format_datetime(row.task.completed_at)}")
    return "\n".join(tips)


def _category_labels(row: TodoRow, ctx: _RowContext) -> list[str]:
    """分類の欄に並べる名前を集める。

    Returns:
        分類、イベント、プロジェクトの名前。
    """
    task = row.task
    category = ctx.categories.get(task.category_id)
    labels = [category.name] if category is not None else []
    if row.event is not None:
        labels.append(row.event.name)
    project = ctx.projects.get(task.project_id) if task.project_id is not None else None
    if project is not None:
        labels.append(project.name)
    return labels


def _toggle[T](items: set[T], key: T, *, collapsed: bool) -> None:
    """折りたたみの状態を覚える。"""
    if collapsed:
        items.add(key)
    else:
        items.discard(key)


def _children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    """直下の子の項目を返す。

    Returns:
        子の項目の一覧。
    """
    found = (item.child(index) for index in range(item.childCount()))
    return [child for child in found if child is not None]


def _task_id_of(item: QTreeWidgetItem | None) -> int | None:
    """行のタスクの id を返す。

    Returns:
        タスクの id。タスクの行でなければ None。
    """
    task_id = None if item is None else item.data(_COL_TITLE, _ROLE_TASK_ID)
    return None if task_id is None else int(task_id)


def _run_menu(menu: QMenu, actions: dict[QAction, Callable[[], object]], pos: QPoint) -> None:
    """メニューを出し、選ばれた項目の処理を行う。何も選ばなければ何もしない。"""
    chosen = menu.exec(pos)
    action = actions.get(chosen)
    if action is not None:
        action()


class TodoView(QWidget):
    """ToDo のビュー。上に1行追加の入力欄、下にセクションごとの一覧を出す。

    Args:
        store: データ操作の窓口。
        parent: 親のウィジェット。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._building = False
        self._rebuild_pending = False
        self._rows: dict[int, TodoRow] = {}
        # 折りたたんだセクションと親タスク。作り直しても状態を保つ
        self._collapsed_sections: set[Section] = set()
        self._collapsed_tasks: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(self._build_input())
        layout.addWidget(self._build_tree(), 1)
        QShortcut(QKeySequence("Ctrl+N"), self, self.focus_input)

        store.tasks_changed.connect(self._schedule_rebuild)
        store.events_changed.connect(self._schedule_rebuild)
        store.projects_changed.connect(self._schedule_rebuild)
        store.filter_changed.connect(self._schedule_rebuild)
        store.minute_ticked.connect(self._schedule_rebuild)
        store.periods_changed.connect(self._schedule_rebuild)
        store.categories_changed.connect(self._on_categories_changed)
        store.filter_changed.connect(self._sync_category_combo)
        store.selection_changed.connect(self._on_selection_changed)

        self._reload_categories()
        self.rebuild()

    def _build_input(self) -> QHBoxLayout:
        """1行追加の入力欄を作る。

        Returns:
            分類の選択、タイトルの入力、追加ボタンを並べたレイアウト。
        """
        self._category_combo = QComboBox()
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("タスクを追加(Enter で確定)")
        self._title_edit.setClearButtonEnabled(True)
        add_button = QPushButton("追加")
        add_row = QHBoxLayout()
        add_row.addWidget(self._category_combo)
        add_row.addWidget(self._title_edit, 1)
        add_row.addWidget(add_button)
        add_button.clicked.connect(self._add_from_input)
        self._title_edit.returnPressed.connect(self._add_from_input)
        return add_row

    def _build_tree(self) -> QTreeWidget:
        """タスクの一覧を作る。

        Returns:
            一覧のウィジェット。
        """
        self._tree = QTreeWidget()
        self._tree.setObjectName("todoTree")
        self._tree.setHeaderLabels(["タスク", "進捗", "期限", "分類"])
        self._tree.setRootIsDecorated(True)
        self._tree.setItemsExpandable(True)
        self._tree.setExpandsOnDoubleClick(False)  # ダブルクリックは誤操作になりやすいので矢印だけ
        self._tree.setIndentation(18)  # サブタスクの字下げ
        self._tree.setAllColumnsShowFocus(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(_COL_PROGRESS, 170)
        header.setSectionResizeMode(_COL_DUE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_CATEGORY, QHeaderView.ResizeMode.ResizeToContents)

        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemExpanded.connect(lambda item: self._remember_fold(item, collapsed=False))
        self._tree.itemCollapsed.connect(lambda item: self._remember_fold(item, collapsed=True))
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self._tree, self._delete_current)
        return self._tree

    # --- 追加 ---

    def focus_input(self) -> None:
        """タスクの入力欄にフォーカスを移す。"""
        self._title_edit.setFocus()

    def _reload_categories(self) -> None:
        """追加先の分類の選択肢を作り直す。"""
        current = self._category_combo.currentData()
        self._category_combo.clear()
        for category in self._store.categories():
            self._category_combo.addItem(color_icon(category.color), category.name, category.id)
        index = self._category_combo.findData(current)
        self._category_combo.setCurrentIndex(max(index, 0))

    def _on_categories_changed(self) -> None:
        """分類が変わったら、選択肢と一覧を作り直す。"""
        self._reload_categories()
        self._schedule_rebuild()

    def _sync_category_combo(self) -> None:
        """左で分類を絞り込んだら、追加先の分類もそれに合わせる。"""
        index = self._category_combo.findData(self._store.filter.category_id)
        if index >= 0:
            self._category_combo.setCurrentIndex(index)

    def _add_from_input(self) -> None:
        """入力欄のタイトルでタスクを追加して選択する。"""
        title = self._title_edit.text().strip()
        category_id = self._category_combo.currentData()
        if not title or category_id is None:
            return
        task = self._store.add_task(title, category_id)
        self._title_edit.clear()
        self._store.select(ItemRef(ItemKind.TASK, task.saved_id))

    # --- 一覧の構築 ---

    def _schedule_rebuild(self) -> None:
        """再構築をイベントループに回す。クリック処理の途中で行が作り直されるのを避ける。"""
        if self._rebuild_pending:
            return
        self._rebuild_pending = True
        QTimer.singleShot(0, self.rebuild)

    def rebuild(self) -> None:
        """一覧を作り直す。選択、スクロール位置、折りたたみの状態は保つ。"""
        self._rebuild_pending = False
        store = self._store
        ctx = _RowContext(
            now=store.now(),
            categories={c.saved_id: c for c in store.categories(include_archived=True)},
            projects=store.projects_by_id(),
            subtasks=store.subtask_progress(),
        )
        scroll = self._tree.verticalScrollBar().value()
        self._building = True
        self._tree.clear()
        self._rows = {}
        items: dict[int, QTreeWidgetItem] = {}
        section_bg = QBrush(self._section_background())
        for data in store.todo_sections():
            self._add_section(data, ctx, items, background=section_bg)
        # 子を作り終えてから開閉を決める。子のない項目は開閉できないため
        for task_id, item in items.items():
            if item.childCount():
                item.setExpanded(task_id not in self._collapsed_tasks)
        self._select_item(store.selected)
        self._building = False
        self._tree.verticalScrollBar().setValue(scroll)

    def _add_section(
        self,
        data: SectionData,
        ctx: _RowContext,
        items: dict[int, QTreeWidgetItem],
        *,
        background: QBrush,
    ) -> None:
        """セクションの見出しと、その下の行・子・孫…を入れ子で並べる。"""
        if data.section in _RECURRING_SECTIONS:
            summary = f"{data.done}/{data.total}"  # 達成数/全体
        else:
            summary = str(len(data.rows))
        header = QTreeWidgetItem(self._tree, [f"{_SECTION_TITLES[data.section]}({summary})"])
        header.setFlags(Qt.ItemFlag.ItemIsEnabled)
        header.setFirstColumnSpanned(True)
        font = header.font(_COL_TITLE)
        font.setBold(True)
        header.setFont(_COL_TITLE, font)
        header.setBackground(_COL_TITLE, background)
        header.setData(_COL_TITLE, _ROLE_SECTION, data.section.value)
        pending = [(header, row) for row in reversed(data.rows)]
        while pending:
            parent, row = pending.pop()
            task_id = row.task.saved_id
            self._rows[task_id] = row
            item = self._add_task_item(parent, row, ctx)
            items[task_id] = item
            children = data.children.get(task_id, [])
            pending.extend((item, child) for child in reversed(children))
        header.setExpanded(data.section not in self._collapsed_sections)

    def _section_background(self) -> QColor:
        """見出し行の背景色を作る。ライト・ダークどちらでも読めるよう、パレットから作る。

        Returns:
            背景色。
        """
        palette = self._tree.palette()
        return mix_colors(
            palette.color(QPalette.ColorRole.Base), palette.color(QPalette.ColorRole.Text), 0.12
        )

    def _add_task_item(
        self, parent: QTreeWidgetItem, row: TodoRow, ctx: _RowContext
    ) -> QTreeWidgetItem:
        """タスクの行を作る。

        Returns:
            作った行。
        """
        task = row.task
        done = row.done
        item = QTreeWidgetItem(parent)
        item.setData(_COL_TITLE, _ROLE_TASK_ID, task.id)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(_COL_TITLE, Qt.CheckState.Checked if done else Qt.CheckState.Unchecked)
        item.setText(_COL_TITLE, _title_text(row, ctx.subtasks))
        if not done:
            item.setText(_COL_DUE, format_row_remaining(row, ctx.now))
            if not row.is_recurring and row.due is not None and row.due < ctx.now:
                item.setForeground(_COL_DUE, QBrush(_COLOR_OVERDUE))
        item.setToolTip(_COL_DUE, _due_tooltip(row))
        category = ctx.categories.get(task.category_id)
        if category is not None:
            item.setIcon(_COL_CATEGORY, color_icon(category.color))
        item.setText(_COL_CATEGORY, " / ".join(_category_labels(row, ctx)))
        if done:
            font = item.font(_COL_TITLE)
            font.setStrikeOut(True)
            item.setFont(_COL_TITLE, font)
            for column in (_COL_TITLE, _COL_CATEGORY):
                item.setForeground(column, QBrush(_COLOR_MUTED))
        if row.target is not None:
            self._tree.setItemWidget(item, _COL_PROGRESS, self._progress_widget(row, row.target))
        return item

    def _progress_widget(self, row: TodoRow, target: int) -> QWidget:
        """回数つきタスクの、増減ボタンと進捗バーを作る。

        Returns:
            ボタンとバーを並べたウィジェット。
        """
        widget = QWidget()
        minus = QToolButton()
        minus.setText(_MINUS)
        minus.setToolTip("1 減らす")
        plus = QToolButton()
        plus.setText("+")
        plus.setToolTip("1 増やす")
        bar = QProgressBar()
        bar.setRange(0, target)
        bar.setValue(row.current or 0)
        bar.setFormat("%v / %m")
        bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.setFixedHeight(18)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        layout.addWidget(minus)
        layout.addWidget(bar, 1)
        layout.addWidget(plus)
        task_id = row.task.saved_id
        minus.clicked.connect(lambda: self._store.adjust_progress(task_id, -1))
        plus.clicked.connect(lambda: self._store.adjust_progress(task_id, +1))
        return widget

    def _remember_fold(self, item: QTreeWidgetItem, *, collapsed: bool) -> None:
        """セクションや親タスクを開閉したら、その状態を覚える。"""
        if self._building:
            return
        section = item.data(_COL_TITLE, _ROLE_SECTION)
        task_id = item.data(_COL_TITLE, _ROLE_TASK_ID)
        if section is not None:
            _toggle(self._collapsed_sections, Section(section), collapsed=collapsed)
        elif task_id is not None:
            _toggle(self._collapsed_tasks, int(task_id), collapsed=collapsed)

    # --- 選択の同期 ---

    def _find_item(self, task_id: int) -> QTreeWidgetItem | None:
        """タスクの行を探す。

        Returns:
            タスクの行。なければ None。
        """
        tops = (self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount()))
        pending = [item for item in tops if item is not None]
        while pending:
            item = pending.pop()
            if item.data(_COL_TITLE, _ROLE_TASK_ID) == task_id:
                return item
            pending.extend(_children(item))
        return None

    def _expand_ancestors(self, item: QTreeWidgetItem) -> None:
        """折りたたまれたセクションや親の中にあれば開き、開いた状態として覚える。"""
        ancestor = item.parent()
        while ancestor is not None:
            if not ancestor.isExpanded():
                ancestor.setExpanded(True)
                self._collapsed_tasks.discard(ancestor.data(_COL_TITLE, _ROLE_TASK_ID))
                section = ancestor.data(_COL_TITLE, _ROLE_SECTION)
                if section is not None:
                    self._collapsed_sections.discard(Section(section))
            ancestor = ancestor.parent()

    def _select_item(self, ref: ItemRef | None) -> None:
        """選択中のタスクの行を選ぶ。タスク以外なら選択を外す。"""
        is_task = ref is not None and ref.kind is ItemKind.TASK
        item = self._find_item(ref.id) if ref is not None and is_task else None
        if item is None:
            self._tree.setCurrentIndex(QModelIndex())
            return
        self._expand_ancestors(item)
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)

    def _on_selection_changed(self, ref: ItemRef | None) -> None:
        """ほかの画面で選択が変わったら、一覧の選択を合わせる。"""
        if self._rebuild_pending:
            return  # 直後の再構築で反映される
        was_building = self._building
        self._building = True
        self._select_item(ref)
        self._building = was_building

    def _on_current_changed(self, current: QTreeWidgetItem | None) -> None:
        """一覧でタスクを選んだら、そのタスクを選択する。"""
        if self._building or current is None:
            return
        task_id = current.data(_COL_TITLE, _ROLE_TASK_ID)
        if task_id is not None:
            self._store.select(ItemRef(ItemKind.TASK, task_id))

    # --- 操作 ---

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """チェックを付け外ししたら、タスクの完了状態を変える。"""
        if self._building or column != _COL_TITLE:
            return
        task_id = item.data(_COL_TITLE, _ROLE_TASK_ID)
        if task_id is not None:
            checked = item.checkState(_COL_TITLE) == Qt.CheckState.Checked
            self._store.set_task_done(task_id, done=checked)

    def _show_context_menu(self, pos: QPoint) -> None:
        """タスクの右クリックメニューを出す。"""
        task_id = _task_id_of(self._tree.itemAt(pos))
        row = None if task_id is None else self._rows.get(task_id)
        if task_id is None or row is None:
            return
        store = self._store
        ref = ItemRef(ItemKind.TASK, task_id)
        done = row.done
        menu = QMenu(self)
        actions: dict[QAction, Callable[[], object]] = {
            menu.addAction("未完了に戻す" if done else "完了にする"): lambda: store.set_task_done(
                task_id, done=not done
            ),
            menu.addAction("カレンダーで表示"): lambda: store.reveal(ref, VIEW_CALENDAR),
        }
        if store.shown_in_gantt(ref):
            actions[menu.addAction("ガントで表示")] = lambda: store.reveal(ref, VIEW_GANTT)
        if not row.is_recurring:
            menu.addSeparator()
            actions[menu.addAction("サブタスクを追加…")] = lambda: self._add_subtask(task_id)
            actions[menu.addAction("前のタスクを追加…")] = lambda: add_dependency_interactive(
                self, store, task_id, as_predecessor=True
            )
            actions[menu.addAction("後のタスクを追加…")] = lambda: add_dependency_interactive(
                self, store, task_id, as_predecessor=False
            )
        menu.addSeparator()
        actions[menu.addAction("削除…")] = lambda: confirm_delete_task(self, store, task_id)
        _run_menu(menu, actions, self._tree.viewport().mapToGlobal(pos))

    def _add_subtask(self, parent_id: int) -> None:
        """タイトルを入力させてサブタスクを追加し、選択する。"""
        parent = self._store.task(parent_id)
        if parent is None:
            return
        title, accepted = QInputDialog.getText(
            self, "サブタスクの追加", f"「{parent.title}」のサブタスクのタイトル"
        )
        if accepted and title.strip():
            task = self._store.add_subtask(parent_id, title)
            self._store.select(ItemRef(ItemKind.TASK, task.saved_id))

    def _delete_current(self) -> None:
        """選んでいるタスクを、確認してから削除する。"""
        task_id = _task_id_of(self._tree.currentItem())
        if task_id is not None:
            confirm_delete_task(self, self._store, task_id)


def confirm_delete_task(parent: QWidget, store: AppStore, task_id: int) -> bool:
    """確認してからタスクを削除する。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        task_id: 削除するタスクの id。

    Returns:
        削除したら True。
    """
    task = store.task(task_id)
    if task is None:
        return False
    children = store.descendant_count(task_id)
    extra = f"\nサブタスク {children} 件(孫以下を含む)も一緒に削除されます。" if children else ""
    answer = QMessageBox.question(
        parent,
        "タスクの削除",
        f"「{task.title}」を削除しますか?{extra}\nこの操作は元に戻せません。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    store.delete_task(task_id)
    return True

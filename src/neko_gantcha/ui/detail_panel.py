"""右ペイン: 選択中のタスク、イベント、プロジェクトの詳細と編集。

タスクは、入力を確定した時点(Enter、フォーカス移動、選択肢の変更)で自動的に保存する。
イベントとプロジェクトは概要と配下タスクを表示し、編集はダイアログで行う。
"""

from dataclasses import replace
from datetime import datetime, time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.models import EventRelation, Freq, ProjectStatus, TaskStatus
from neko_gantcha.domain.project import tree_order
from neko_gantcha.domain.todo import format_event_status
from neko_gantcha.services.store import VIEW_GANTT, ItemKind, ItemRef
from neko_gantcha.ui.dialogs.dependency_dialog import add_dependency_interactive
from neko_gantcha.ui.dialogs.event_dialog import confirm_delete_event, duplicate_event, edit_event
from neko_gantcha.ui.dialogs.project_dialog import confirm_delete_project, edit_project
from neko_gantcha.ui.dialogs.template_dialog import save_as_template
from neko_gantcha.ui.todo_view import confirm_delete_task
from neko_gantcha.ui.widgets import (
    NoteEdit,
    color_icon,
    format_datetime,
    from_qdatetime,
    to_qdatetime,
)

if TYPE_CHECKING:
    from neko_gantcha.domain.models import Event, Project, Task
    from neko_gantcha.domain.todo import TodoRow
    from neko_gantcha.services.store import AppStore

_STATUS_LABELS = [
    (TaskStatus.TODO, "未着手"),
    (TaskStatus.DOING, "進行中"),
    (TaskStatus.DONE, "完了"),
]
_PRIORITY_LABELS = [(0, "通常"), (1, "高"), (2, "最高")]
_FREQ_LABELS = [(None, "なし"), (Freq.DAILY, "毎日"), (Freq.WEEKLY, "毎週"), (Freq.MONTHLY, "毎月")]
_RELATION_LABELS = [(EventRelation.DURING, "イベント中"), (EventRelation.BEFORE, "イベントまで")]
_FREQ_SHORT = {Freq.DAILY: "毎日", Freq.WEEKLY: "毎週", Freq.MONTHLY: "毎月"}
_PROJECT_STATUS_LABELS = {
    ProjectStatus.ACTIVE: "進行中",
    ProjectStatus.DONE: "完了",
    ProjectStatus.ARCHIVED: "アーカイブ",
}

# イベントのページでタスクを追加するときの種類
_KIND_DURING, _KIND_BEFORE, _KIND_DAILY = "during", "before", "daily"

_PAGE_EMPTY, _PAGE_TASK, _PAGE_EVENT, _PAGE_PROJECT = range(4)
_ROLE_ID = Qt.ItemDataRole.UserRole
_ROLE_DEP = Qt.ItemDataRole.UserRole + 1  # 前後関係の (前のタスクの id, 後のタスクの id)


def _title_label() -> QLabel:
    """ページの上の、項目の名前を出すラベルを作る。

    Returns:
        名前のラベル。
    """
    label = QLabel()
    label.setObjectName("itemTitle")
    label.setWordWrap(True)
    return label


def _pane_title(text: str) -> QLabel:
    """ページの中の見出しを作る。

    Returns:
        見出しのラベル。
    """
    label = QLabel(text)
    label.setObjectName("paneTitle")
    return label


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    """ボタンを左寄せで横に並べる。

    Returns:
        ボタンを並べたレイアウト。
    """
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


def _datetime_row() -> tuple[QCheckBox, QDateTimeEdit, QHBoxLayout]:
    """日時の入力欄と、使うかどうかのチェックボックスを作る。

    Returns:
        (チェックボックス, 日時の入力欄, 両方を並べたレイアウト)。
    """
    checkbox = QCheckBox()
    edit = QDateTimeEdit()
    edit.setDisplayFormat("yyyy/MM/dd HH:mm")
    edit.setCalendarPopup(True)
    edit.setKeyboardTracking(False)
    row = QHBoxLayout()
    row.addWidget(checkbox)
    row.addWidget(edit, 1)
    return checkbox, edit, row


def _task_info(task: Task, row: TodoRow) -> str:
    """タスクのページの下に出す、日時の情報を作る。

    Returns:
        次のリセット、実際の開始・期限、作成、完了の日時を並べた文字列。
    """
    info: list[str] = []
    if row.is_recurring and row.period is not None:
        info.append(f"次のリセット: {format_datetime(row.period.end)}")
    elif not row.is_recurring and row.event is not None:
        if row.start is not None:
            info.append(f"実際の開始: {format_datetime(row.start)}")
        if row.due is not None:
            info.append(f"実際の期限: {format_datetime(row.due)}")
    info.append(f"作成: {format_datetime(task.created_at)}")
    if task.completed_at is not None:
        info.append(f"完了: {format_datetime(task.completed_at)}")
    return "\n".join(info)


def _freq_label(row: TodoRow) -> str | None:
    """繰り返しタスクの頻度の短い表示を返す。

    Returns:
        `毎日` などの文字列。繰り返しでなければ None。
    """
    if row.recurrence is None:
        return None
    return _FREQ_SHORT[row.recurrence.freq]


class DetailPanel(QWidget):
    """右ペイン。選択中の項目の詳細を出し、タスクはその場で編集できる。

    Args:
        store: データ操作の窓口。
        parent: 親のウィジェット。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._task: Task | None = None
        self._freq: Freq | None = None
        self._count = 0  # 繰り返しタスクの、現在の期間の達成回数
        self._event: Event | None = None
        self._project: Project | None = None
        self._has_children = False
        self._loading = False

        placeholder = QLabel("タスクやイベントを選択すると、ここに詳細が表示されます。")
        placeholder.setObjectName("placeholder")
        placeholder.setWordWrap(True)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._stack = QStackedWidget()
        self._stack.addWidget(placeholder)
        self._stack.addWidget(self._build_task_page())
        self._stack.addWidget(self._build_event_page())
        self._stack.addWidget(self._build_project_page())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 8, 8)
        layout.addWidget(_pane_title("詳細"))
        layout.addWidget(self._stack, 1)

        store.selection_changed.connect(self._refresh)
        store.tasks_changed.connect(self._refresh)
        store.events_changed.connect(self._refresh)
        store.projects_changed.connect(self._refresh)
        store.periods_changed.connect(self._refresh)
        store.categories_changed.connect(self._reload_categories)
        self._reload_categories()

    # --- タスクのページ ---

    def _build_task_page(self) -> QWidget:
        """タスクのページを作る。

        Returns:
            タスクのページ。
        """
        self._note = NoteEdit()
        self._note.setPlaceholderText("メモ")
        self._info = QLabel()
        self._info.setObjectName("info")
        self._info.setWordWrap(True)
        delete_button = QPushButton("このタスクを削除…")
        # サブタスクなら親タスクへのリンク
        self._parent_button = QPushButton()
        self._parent_button.setToolTip("親タスクを開く")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._parent_button)
        layout.addLayout(self._build_task_form())
        layout.addWidget(self._note, 1)
        layout.addWidget(self._build_subtask_box())
        layout.addWidget(self._build_dependency_box())
        layout.addWidget(self._info)
        layout.addWidget(delete_button)

        self._title.editingFinished.connect(self._commit)
        for combo in (
            self._category,
            self._freq_combo,
            self._event_combo,
            self._relation,
            self._project_combo,
            self._status,
            self._priority,
        ):
            combo.activated.connect(self._on_choice)
        for checkbox in (self._has_start, self._has_due, self._has_progress):
            checkbox.toggled.connect(self._on_choice)
        self._start.dateTimeChanged.connect(self._commit)
        self._due.dateTimeChanged.connect(self._commit)
        self._progress_current.valueChanged.connect(self._commit)
        self._progress_target.valueChanged.connect(self._commit)
        self._note.editing_finished.connect(self._commit)
        delete_button.clicked.connect(self._delete_task)
        self._parent_button.clicked.connect(self._open_parent)
        return page

    def _build_task_form(self) -> QFormLayout:
        """タスクの項目の入力欄を作る。

        Returns:
            入力欄を並べたフォーム。
        """
        self._title = QLineEdit()
        self._category = QComboBox()
        self._freq_combo = QComboBox()
        for freq, label in _FREQ_LABELS:
            self._freq_combo.addItem(label, freq.value if freq is not None else None)
        self._event_combo = QComboBox()
        self._relation = QComboBox()
        for relation, label in _RELATION_LABELS:
            self._relation.addItem(label, relation.value)
        event_row = QHBoxLayout()
        event_row.addWidget(self._event_combo, 1)
        event_row.addWidget(self._relation)
        self._project_combo = QComboBox()
        self._status = QComboBox()
        for status, label in _STATUS_LABELS:
            self._status.addItem(label, status.value)
        self._priority = QComboBox()
        for value, label in _PRIORITY_LABELS:
            self._priority.addItem(label, value)
        self._has_start, self._start, start_row = _datetime_row()
        self._has_due, self._due, due_row = _datetime_row()

        form = QFormLayout()
        form.addRow("タイトル", self._title)
        form.addRow("分類", self._category)
        form.addRow("繰り返し", self._freq_combo)
        form.addRow("イベント", event_row)
        form.addRow("プロジェクト", self._project_combo)
        form.addRow("状態", self._status)
        form.addRow("優先度", self._priority)
        form.addRow("開始", start_row)
        form.addRow("期限", due_row)
        form.addRow("進捗", self._build_progress_row())
        return form

    def _build_progress_row(self) -> QHBoxLayout:
        """回数管理の入力欄を作る。

        Returns:
            チェックボックスと現在値・目標の入力欄を並べたレイアウト。
        """
        self._has_progress = QCheckBox("回数で管理")
        self._progress_current = QSpinBox()
        self._progress_current.setRange(0, 99999)
        self._progress_current.setKeyboardTracking(False)
        self._progress_target = QSpinBox()
        self._progress_target.setRange(1, 99999)
        self._progress_target.setKeyboardTracking(False)
        row = QHBoxLayout()
        row.addWidget(self._has_progress)
        row.addWidget(self._progress_current)
        row.addWidget(QLabel("/"))
        row.addWidget(self._progress_target)
        row.addStretch(1)
        return row

    def _build_subtask_box(self) -> QWidget:
        """サブタスクの一覧と追加欄を作る。繰り返しでないタスクだけに出す。

        Returns:
            サブタスクの欄。
        """
        self._subtask_box = QWidget()
        self._subtask_title = _pane_title("サブタスク")
        self._subtask_list = QListWidget()
        self._subtask_list.setObjectName("subtaskList")
        self._subtask_list.setMaximumHeight(120)
        self._subtask_input = QLineEdit()
        self._subtask_input.setPlaceholderText("サブタスクを追加(Enter で確定)")
        box_layout = QVBoxLayout(self._subtask_box)
        box_layout.setContentsMargins(0, 4, 0, 0)
        box_layout.setSpacing(4)
        box_layout.addWidget(self._subtask_title)
        box_layout.addWidget(self._subtask_list)
        box_layout.addWidget(self._subtask_input)
        self._subtask_list.itemChanged.connect(self._on_subtask_checked)
        self._subtask_list.itemDoubleClicked.connect(self._open_list_task)
        self._subtask_input.returnPressed.connect(self._add_subtask)
        return self._subtask_box

    def _build_dependency_box(self) -> QWidget:
        """前後関係(このタスクの前に終えるタスクと、後に続くタスク)の欄を作る。

        Returns:
            前後関係の欄。
        """
        self._dep_box = QWidget()
        self._dep_title = _pane_title("前後関係")
        self._dep_list = QListWidget()
        self._dep_list.setObjectName("subtaskList")
        self._dep_list.setMaximumHeight(84)
        self._dep_list.setToolTip("ダブルクリックでそのタスクを開く")
        add_before = QPushButton("前を追加…")
        add_before.setToolTip("このタスクより先に終えるタスクを選ぶ")
        add_after = QPushButton("後を追加…")
        add_after.setToolTip("このタスクを終えてから取りかかるタスクを選ぶ")
        self._dep_remove = QPushButton("外す")
        dep_layout = QVBoxLayout(self._dep_box)
        dep_layout.setContentsMargins(0, 4, 0, 0)
        dep_layout.setSpacing(4)
        dep_layout.addWidget(self._dep_title)
        dep_layout.addWidget(self._dep_list)
        dep_layout.addLayout(_button_row(add_before, add_after, self._dep_remove))
        add_before.clicked.connect(lambda: self._add_dependency(as_predecessor=True))
        add_after.clicked.connect(lambda: self._add_dependency(as_predecessor=False))
        self._dep_remove.clicked.connect(self._remove_dependency)
        self._dep_list.itemDoubleClicked.connect(self._open_list_task)
        self._dep_list.currentItemChanged.connect(
            lambda current: self._dep_remove.setEnabled(current is not None)
        )
        return self._dep_box

    def _reload_categories(self) -> None:
        """分類の選択肢を作り直す。"""
        self._loading = True
        self._category.clear()
        for category in self._store.categories(include_archived=True):
            self._category.addItem(color_icon(category.color), category.name, category.id)
        if self._task is not None:
            self._category.setCurrentIndex(self._category.findData(self._task.category_id))
        self._loading = False

    def _reload_event_choices(self, task: Task) -> None:
        """同じ分類の、終了していないイベントを選択肢にする(紐付け済みのものは常に含める)。"""
        now = self._store.now()
        self._event_combo.clear()
        self._event_combo.addItem("なし", None)
        for event in self._store.events_by_id().values():
            linked = event.id == task.event_id
            if linked or (event.category_id == task.category_id and event.end_at > now):
                label = f"{event.name}({event.start_at:%m/%d}〜{event.end_at:%m/%d})"
                self._event_combo.addItem(label, event.id)
        self._event_combo.setCurrentIndex(max(self._event_combo.findData(task.event_id), 0))

    def _reload_project_choices(self, task: Task) -> None:
        """同じ分類の進行中のプロジェクトを選択肢にする(所属中のものは常に含める)。"""
        self._project_combo.clear()
        self._project_combo.addItem("なし", None)
        for project in self._store.projects_by_id().values():
            linked = project.id == task.project_id
            active = project.status is ProjectStatus.ACTIVE
            if linked or (project.category_id == task.category_id and active):
                self._project_combo.addItem(project.name, project.id)
        index = self._project_combo.findData(task.project_id)
        self._project_combo.setCurrentIndex(max(index, 0))

    def _reload_subtasks(self, task: Task) -> None:
        """直下のサブタスクの一覧を作り直す。"""
        children = self._store.subtasks(task.saved_id)
        self._has_children = bool(children)
        # 一覧は直下のサブタスクだけ。さらにサブタスクを持つものは末端の完了数を添える
        nested = self._store.subtask_progress() if children else {}
        self._subtask_list.blockSignals(True)
        self._subtask_list.clear()
        for child in children:
            title = child.title
            stats = nested.get(child.saved_id)
            if stats is not None:
                title = f"{title}({stats.done}/{stats.total})"
            item = QListWidgetItem(title)
            item.setData(_ROLE_ID, child.id)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            done = child.status is TaskStatus.DONE
            item.setCheckState(Qt.CheckState.Checked if done else Qt.CheckState.Unchecked)
            if done:
                item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip("ダブルクリックで開く")
            self._subtask_list.addItem(item)
        self._subtask_list.blockSignals(False)
        # 見出しの完了数は ToDo の表示と同じく、孫以下を含めた末端のサブタスクで数える
        progress = nested.get(task.saved_id)
        self._subtask_title.setText(
            f"サブタスク({progress.done}/{progress.total})"
            if progress is not None
            else "サブタスク"
        )

    def _reload_dependencies(self, task: Task) -> None:
        """前後関係の一覧を作り直す。"""
        store = self._store
        self._dep_list.clear()
        for before in store.predecessors(task.saved_id):
            done = before.status is TaskStatus.DONE
            item = QListWidgetItem(f"← 前: {before.title}{'' if done else '(未完了)'}")
            item.setData(_ROLE_ID, before.id)
            item.setData(_ROLE_DEP, (before.id, task.id))
            if done:
                item.setForeground(Qt.GlobalColor.gray)
            self._dep_list.addItem(item)
        for after in store.successors(task.saved_id):
            item = QListWidgetItem(f"→ 後: {after.title}")
            item.setData(_ROLE_ID, after.id)
            item.setData(_ROLE_DEP, (task.id, after.id))
            self._dep_list.addItem(item)
        waiting = len(store.waiting_on(task.saved_id))
        self._dep_title.setText(
            f"前後関係(前のタスク {waiting} 件が未完了)" if waiting else "前後関係"
        )
        self._dep_list.setVisible(self._dep_list.count() > 0)
        self._dep_remove.setEnabled(False)

    def _add_dependency(self, *, as_predecessor: bool) -> None:
        """相手のタスクを選ばせて前後関係を加える。"""
        if self._task is not None:
            add_dependency_interactive(
                self, self._store, self._task.saved_id, as_predecessor=as_predecessor
            )

    def _remove_dependency(self) -> None:
        """一覧で選んだ前後関係を外す。"""
        item = self._dep_list.currentItem()
        # PySide6 の型定義は None を返さないことになっているが、選択がなければ None が返る
        if item is not None:  # ty: ignore[redundant-condition-strict]
            predecessor_id, successor_id = item.data(_ROLE_DEP)
            self._store.remove_dependency(predecessor_id, successor_id)

    def _show_task(self, task: Task) -> None:
        """タスクのページに切り替え、入力欄に値を入れる。"""
        row = self._store.todo_row(task.saved_id)
        if row is None:
            return
        recurrence = row.recurrence
        self._task = task
        self._freq = recurrence.freq if recurrence is not None else None
        self._count = row.count

        self._loading = True
        self._fill_task_form(task, row)
        if self._note.toPlainText() != task.note:
            self._note.setPlainText(task.note)
        parent_id = task.parent_task_id
        parent = self._store.task(parent_id) if parent_id is not None else None
        self._parent_button.setVisible(parent is not None)
        if parent is not None:
            self._parent_button.setText(f"↑ 親タスク: {parent.title}")
        self._reload_subtasks(task)
        # 繰り返しタスクはサブタスクを持てない。サブタスクの下にはさらに作れる
        self._subtask_box.setVisible(recurrence is None)
        # 繰り返しタスクは前後関係も持てない
        self._reload_dependencies(task)
        self._dep_box.setVisible(recurrence is None)
        self._info.setText(_task_info(task, row))
        self._update_enabled()
        self._loading = False
        self._stack.setCurrentIndex(_PAGE_TASK)

    def _fill_task_form(self, task: Task, row: TodoRow) -> None:
        """タスクの項目の入力欄に値を入れる。"""
        now = self._store.now()
        self._title.setText(task.title)
        self._category.setCurrentIndex(self._category.findData(task.category_id))
        freq_value = self._freq.value if self._freq is not None else None
        self._freq_combo.setCurrentIndex(self._freq_combo.findData(freq_value))
        self._reload_event_choices(task)
        self._reload_project_choices(task)
        relation = task.event_relation or EventRelation.DURING
        self._relation.setCurrentIndex(self._relation.findData(relation.value))
        self._status.setCurrentIndex(self._status.findData(task.status.value))
        self._priority.setCurrentIndex(max(self._priority.findData(task.priority), 0))

        self._has_start.setChecked(task.start_at is not None)
        self._start.setDateTime(to_qdatetime(task.start_at or _day_at(now, time(0, 0))))
        self._has_due.setChecked(task.due_at is not None)
        self._due.setDateTime(to_qdatetime(task.due_at or _day_at(now, time(23, 59))))

        self._has_progress.setChecked(task.progress_target is not None)
        self._progress_target.setValue(task.progress_target or 1)
        self._progress_current.setMaximum(task.progress_target or 1)
        self._progress_current.setValue(row.current or 0)

    def _update_enabled(self) -> None:
        """選んだ内容に合わせて、使えない入力欄を無効にする。"""
        recurring = self._freq_combo.currentData() is not None
        has_event = self._event_combo.currentData() is not None
        is_subtask = self._task is not None and self._task.parent_task_id is not None
        # サブタスクと、サブタスクを持つタスクは繰り返しにできない
        self._freq_combo.setEnabled(not is_subtask and not self._has_children)
        # サブタスクの分類とプロジェクトは親に合わせる
        self._category.setEnabled(not is_subtask)
        self._project_combo.setEnabled(not is_subtask)
        before = has_event and self._relation.currentData() == EventRelation.BEFORE.value
        self._relation.setEnabled(has_event and not recurring)
        self._status.setEnabled(not recurring)
        self._has_start.setEnabled(not recurring)
        self._start.setEnabled(not recurring and self._has_start.isChecked())
        # 「イベントまで」の期限は常にイベント開始日時なので、個別には設定できない
        self._has_due.setEnabled(not recurring and not before)
        self._due.setEnabled(not recurring and not before and self._has_due.isChecked())
        self._progress_current.setEnabled(self._has_progress.isChecked())
        self._progress_target.setEnabled(self._has_progress.isChecked())
        self._has_progress.setText("期間ごとの回数" if recurring else "回数で管理")

    def _on_choice(self) -> None:
        """選択肢を変えたら、入力欄の有効・無効を合わせて保存する。"""
        if self._loading:
            return
        self._update_enabled()
        self._commit()

    def _commit(self) -> None:
        """入力内容でタスクを保存する。変わっていなければ何もしない。"""
        task = self._task
        if self._loading or task is None:
            return
        freq_value = self._freq_combo.currentData()
        freq = Freq(freq_value) if freq_value else None
        event_id = self._event_combo.currentData()
        target = self._progress_target.value() if self._has_progress.isChecked() else None
        entered = None if target is None else min(self._progress_current.value(), target)
        updated = replace(
            task,
            title=self._title.text().strip() or task.title,
            category_id=self._category.currentData(),
            status=TaskStatus(self._status.currentData()),
            priority=self._priority.currentData(),
            start_at=from_qdatetime(self._start.dateTime())
            if self._has_start.isChecked()
            else None,
            due_at=from_qdatetime(self._due.dateTime()) if self._has_due.isChecked() else None,
            event_id=event_id,
            event_relation=EventRelation(self._relation.currentData()) if event_id else None,
            project_id=self._project_combo.currentData(),
            progress_target=target,
            note=self._note.toPlainText(),
        )
        was_recurring = self._freq is not None
        if not was_recurring and freq is None:
            updated.progress_current = entered
        task_changed = updated != task or freq != self._freq
        if not self._title.text().strip():
            self._title.setText(task.title)
        task_id = task.saved_id
        # 保存後の内容は tasks_changed 経由の _refresh でフォームに反映される
        if task_changed:
            self._store.update_task(updated, freq=freq)
        # 繰り返しタスクの回数は、期間ごとの達成記録として別に保存する
        if was_recurring and freq is not None and entered is not None and entered != self._count:
            self._store.set_completion_count(task_id, entered)

    def _delete_task(self) -> None:
        """表示中のタスクを、確認してから削除する。"""
        if self._task is not None:
            confirm_delete_task(self, self._store, self._task.saved_id)

    def _open_parent(self) -> None:
        """親タスクを選択する。"""
        if self._task is not None and self._task.parent_task_id is not None:
            self._store.select(ItemRef(ItemKind.TASK, self._task.parent_task_id))

    def _open_list_task(self, item: QListWidgetItem) -> None:
        """一覧の項目のタスクを選択する。"""
        self._store.select(ItemRef(ItemKind.TASK, item.data(_ROLE_ID)))

    def _on_subtask_checked(self, item: QListWidgetItem) -> None:
        """サブタスクのチェックを付け外ししたら、完了状態を変える。"""
        checked = item.checkState() == Qt.CheckState.Checked
        task_id = item.data(_ROLE_ID)
        # 保存すると一覧が作り直されるので、チェックの処理が終わってから保存する
        QTimer.singleShot(0, lambda: self._store.set_task_done(task_id, done=checked))

    def _add_subtask(self) -> None:
        """入力欄のタイトルでサブタスクを追加する。"""
        title = self._subtask_input.text().strip()
        if not title or self._task is None:
            return
        self._subtask_input.clear()
        self._store.add_subtask(self._task.saved_id, title)

    # --- イベントのページ ---

    def _build_event_page(self) -> QWidget:
        """イベントのページを作る。

        Returns:
            イベントのページ。
        """
        self._event_name = _title_label()
        self._event_summary = QLabel()
        self._event_summary.setWordWrap(True)
        self._event_summary.setTextFormat(Qt.TextFormat.RichText)
        self._event_summary.setOpenExternalLinks(True)
        self._event_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._event_tasks = QListWidget()
        self._event_tasks.setObjectName("filterList")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._event_name)
        layout.addWidget(self._event_summary)
        layout.addLayout(self._build_event_buttons())
        layout.addSpacing(8)
        layout.addWidget(_pane_title("このイベントのタスク"))
        layout.addWidget(self._event_tasks, 1)
        layout.addLayout(self._build_event_task_input())
        self._event_tasks.itemClicked.connect(self._open_list_task)
        return page

    def _build_event_buttons(self) -> QHBoxLayout:
        """イベントの編集・複製・テンプレートへの保存・削除のボタンを作る。

        Returns:
            ボタンを並べたレイアウト。
        """
        edit_button = QPushButton("編集…")
        duplicate_button = QPushButton("複製…")
        duplicate_button.setToolTip("次回の同じイベントを、タスクごと日付をずらして作る")
        template_button = QPushButton("テンプレートに保存…")
        template_button.setToolTip(
            "イベントと配下のタスクの組を、日付を持たないひな形として保存する"
            "(左のイベント欄の「テンプレートから…」で使う)"
        )
        delete_button = QPushButton("削除…")
        edit_button.clicked.connect(self._edit_event)
        duplicate_button.clicked.connect(self._duplicate_event)
        template_button.clicked.connect(self._save_template)
        delete_button.clicked.connect(self._delete_event)
        return _button_row(edit_button, duplicate_button, template_button, delete_button)

    def _build_event_task_input(self) -> QHBoxLayout:
        """イベントのタスクを追加する入力欄を作る。

        Returns:
            タイトルの入力欄と種類の選択を並べたレイアウト。
        """
        self._event_task_title = QLineEdit()
        self._event_task_title.setPlaceholderText("タスクを追加(Enter で確定)")
        self._event_task_kind = QComboBox()
        self._event_task_kind.addItem("イベント中", _KIND_DURING)
        self._event_task_kind.addItem("イベントまで", _KIND_BEFORE)
        self._event_task_kind.addItem("期間中の日課", _KIND_DAILY)
        add_row = QHBoxLayout()
        add_row.addWidget(self._event_task_title, 1)
        add_row.addWidget(self._event_task_kind)
        self._event_task_title.returnPressed.connect(self._add_event_task)
        return add_row

    def _show_event(self, event: Event) -> None:
        """イベントのページに切り替え、概要と配下タスクを出す。"""
        store = self._store
        self._event = event
        category = store.category(event.category_id)
        self._event_name.setText(event.name)
        lines = [
            f"分類: {_escape(category.name) if category is not None else ''}",
            f"期間: {format_datetime(event.start_at)} 〜 {format_datetime(event.end_at)}",
            f"状況: {format_event_status(event, store.now())}",
        ]
        if event.url:
            url = _escape(event.url)
            lines.append(f'URL: <a href="{url}">{url}</a>')
        if event.note:
            lines.append(_escape(event.note).replace("\n", "<br>"))
        self._event_summary.setText("<br>".join(lines))

        self._event_tasks.clear()
        for task in store.event_tasks(event.saved_id):
            row = store.todo_row(task.saved_id)
            if row is None:
                continue
            kind = _freq_label(row)
            if kind is None:
                before = task.event_relation is EventRelation.BEFORE
                kind = "イベントまで" if before else "イベント中"
            progress = f"  {row.current}/{row.target}" if row.target is not None else ""
            item = QListWidgetItem(f"{'✓' if row.done else '・'} {task.title}{progress}  [{kind}]")
            item.setData(_ROLE_ID, task.id)
            if row.done:
                item.setForeground(Qt.GlobalColor.gray)
            self._event_tasks.addItem(item)
        self._stack.setCurrentIndex(_PAGE_EVENT)

    def _edit_event(self) -> None:
        """表示中のイベントを編集する。"""
        if self._event is not None:
            edit_event(self, self._store, self._event.id)

    def _delete_event(self) -> None:
        """表示中のイベントを、確認してから削除する。"""
        if self._event is not None:
            confirm_delete_event(self, self._store, self._event.saved_id)

    def _duplicate_event(self) -> None:
        """表示中のイベントを複製する。"""
        if self._event is not None:
            duplicate_event(self, self._store, self._event.saved_id)

    def _save_template(self) -> None:
        """表示中のイベントをテンプレートとして保存する。"""
        if self._event is not None:
            save_as_template(self, self._store, self._event.saved_id)

    def _add_event_task(self) -> None:
        """入力欄のタイトルで、表示中のイベントのタスクを追加する。"""
        title = self._event_task_title.text().strip()
        event = self._event
        if not title or event is None:
            return
        kind = self._event_task_kind.currentData()
        self._event_task_title.clear()
        if kind == _KIND_DAILY:
            self._store.add_task(title, event.category_id, event_id=event.id, freq=Freq.DAILY)
        else:
            relation = EventRelation(kind)
            self._store.add_task(
                title, event.category_id, event_id=event.id, event_relation=relation
            )

    # --- プロジェクトのページ ---

    def _build_project_page(self) -> QWidget:
        """プロジェクトのページを作る。

        Returns:
            プロジェクトのページ。
        """
        self._project_name = _title_label()
        self._project_summary = QLabel()
        self._project_summary.setWordWrap(True)
        self._project_bar = QProgressBar()
        self._project_bar.setFixedHeight(18)
        self._project_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._project_goal = QLabel()
        self._project_goal.setWordWrap(True)
        edit_button = QPushButton("編集…")
        gantt_button = QPushButton("ガントで表示")
        delete_button = QPushButton("削除…")
        self._project_tasks = QListWidget()
        self._project_tasks.setObjectName("filterList")
        self._project_task_title = QLineEdit()
        self._project_task_title.setPlaceholderText("タスクを追加(Enter で確定)")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in (
            self._project_name,
            self._project_summary,
            self._project_bar,
            self._project_goal,
        ):
            layout.addWidget(widget)
        layout.addLayout(_button_row(edit_button, gantt_button, delete_button))
        layout.addSpacing(8)
        layout.addWidget(_pane_title("このプロジェクトのタスク"))
        layout.addWidget(self._project_tasks, 1)
        layout.addWidget(self._project_task_title)

        edit_button.clicked.connect(self._edit_project)
        gantt_button.clicked.connect(self._show_project_in_gantt)
        delete_button.clicked.connect(self._delete_project)
        self._project_tasks.itemClicked.connect(self._open_list_task)
        self._project_task_title.returnPressed.connect(self._add_project_task)
        return page

    def _show_project(self, project: Project) -> None:
        """プロジェクトのページに切り替え、概要と配下タスクを出す。"""
        store = self._store
        self._project = project
        category = store.category(project.category_id)
        event = store.get_event(project.event_id) if project.event_id is not None else None
        stats = store.project_progress().get(project.saved_id)
        self._project_name.setText(project.name)
        lines = [f"分類: {category.name if category is not None else ''}"]
        if project.target_date is not None:
            lines.append(f"目標日: {project.target_date:%Y/%m/%d}")
        else:
            lines.append("目標日: なし")
        if event is not None:
            lines.append(f"イベント: {event.name}({event.start_at:%m/%d}〜{event.end_at:%m/%d})")
        lines.append(f"状態: {_PROJECT_STATUS_LABELS[project.status]}")
        self._project_summary.setText("\n".join(lines))
        if stats is not None:
            self._project_bar.setRange(0, max(1, stats.total))
            self._project_bar.setValue(stats.done)
            self._project_bar.setFormat(f"完了 {stats.done}/{stats.total}({stats.percent_text()})")
        else:
            self._project_bar.setRange(0, 1)
            self._project_bar.setValue(0)
            self._project_bar.setFormat("タスクなし")
        self._project_goal.setText(project.goal)
        self._project_goal.setVisible(bool(project.goal))

        self._project_tasks.clear()
        # サブタスクは親のすぐ後に、深さに応じて字下げして並べる
        for task, depth in tree_order(store.project_tasks(project.saved_id)):
            row = store.todo_row(task.saved_id)
            if row is None:
                continue
            indent = "      " * depth
            freq = _freq_label(row)
            kind = f"  [{freq}]" if freq is not None else ""
            item = QListWidgetItem(f"{indent}{'✓' if row.done else '・'} {task.title}{kind}")
            item.setData(_ROLE_ID, task.id)
            if row.done:
                item.setForeground(Qt.GlobalColor.gray)
            self._project_tasks.addItem(item)
        self._stack.setCurrentIndex(_PAGE_PROJECT)

    def _edit_project(self) -> None:
        """表示中のプロジェクトを編集する。"""
        if self._project is not None:
            edit_project(self, self._store, self._project.id)

    def _show_project_in_gantt(self) -> None:
        """表示中のプロジェクトをガントで表示する。"""
        if self._project is not None:
            self._store.reveal(ItemRef(ItemKind.PROJECT, self._project.saved_id), VIEW_GANTT)

    def _delete_project(self) -> None:
        """表示中のプロジェクトを、確認してから削除する。"""
        if self._project is not None:
            confirm_delete_project(self, self._store, self._project.saved_id)

    def _add_project_task(self) -> None:
        """入力欄のタイトルで、表示中のプロジェクトのタスクを追加する。"""
        title = self._project_task_title.text().strip()
        if not title or self._project is None:
            return
        self._project_task_title.clear()
        self._store.add_task(title, self._project.category_id, project_id=self._project.id)

    # --- 表示の切り替え ---

    def _refresh(self, *_args: object) -> None:
        """選択中の項目を読み直して表示する。"""
        ref = self._store.selected
        self._task = None
        self._event = None
        self._project = None
        kind = ref.kind if ref is not None else None
        task = self._store.task(ref.id) if ref is not None and kind is ItemKind.TASK else None
        event = (
            self._store.get_event(ref.id) if ref is not None and kind is ItemKind.EVENT else None
        )
        project = (
            self._store.project(ref.id) if ref is not None and kind is ItemKind.PROJECT else None
        )
        if task is not None:
            self._show_task(task)
        elif event is not None:
            self._show_event(event)
        elif project is not None:
            self._show_project(project)
        else:
            self._stack.setCurrentIndex(_PAGE_EMPTY)


def _day_at(now: datetime, at: time) -> datetime:
    """今日の指定した時刻を返す。

    Returns:
        今日の日付と at を合わせた日時。
    """
    return datetime.combine(now.date(), at)


def _escape(text: str) -> str:
    """HTML に入れる文字列の記号を置き換える。

    Returns:
        置き換えた文字列。
    """
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )

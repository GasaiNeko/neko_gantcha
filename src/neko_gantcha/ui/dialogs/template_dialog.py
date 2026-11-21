"""イベントのテンプレート: イベントからの保存と、テンプレートからのイベント作成(と管理)。"""

from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.duplicate import EventPlan
from neko_gantcha.domain.models import EventRelation, Freq
from neko_gantcha.domain.template import describe_offset, suggest_start, template_tree
from neko_gantcha.services.store import ItemKind, ItemRef, TemplateNameError
from neko_gantcha.ui.dialogs.event_dialog import _datetime_edit
from neko_gantcha.ui.widgets import color_icon, from_qdatetime, to_qdatetime

if TYPE_CHECKING:
    from neko_gantcha.domain.models import Event, EventTemplate, TemplateTask
    from neko_gantcha.services.store import AppStore

_ROLE_ID = Qt.ItemDataRole.UserRole
_FREQ_LABELS = {Freq.DAILY: "日課", Freq.WEEKLY: "週課", Freq.MONTHLY: "月課"}
_RELATION_LABELS = {EventRelation.DURING: "イベント中", EventRelation.BEFORE: "イベントまで"}


def save_as_template(parent: QWidget, store: AppStore, event_id: int) -> EventTemplate | None:
    """イベントを配下タスクごとテンプレートとして保存する。同じ名前があれば上書きを確かめる。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        event_id: もとにするイベントの id。

    Returns:
        保存したテンプレート。やめたら None。
    """
    event = store.get_event(event_id)
    if event is None:
        return None
    task_count = len(store.event_tasks(event_id))
    name, accepted = QInputDialog.getText(
        parent,
        "テンプレートに保存",
        f"「{event.name}」と配下のタスク {task_count} 件(サブタスクを含む)を、\n"
        "日付を持たないひな形として保存します。テンプレート名:",
        text=event.name,
    )
    name = name.strip()
    if not accepted or not name:
        return None
    try:
        template = store.save_template_from_event(event_id, name)
    except TemplateNameError:
        answer = QMessageBox.question(
            parent,
            "テンプレートに保存",
            f"「{name}」というテンプレートはすでにあります。上書きしますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        template = store.save_template_from_event(event_id, name, replace=True)
    count = store.template_task_counts().get(template.id, 0)
    QMessageBox.information(
        parent,
        "テンプレートに保存",
        f"テンプレート「{template.name}」を保存しました(タスク {count} 件)。\n"
        "左のイベント欄の「テンプレートから…」で、好きな日程のイベントを作れます。",
    )
    return template


def describe_task(task: TemplateTask) -> str:
    """テンプレートの中のタスク1件の説明を作る。

    Args:
        task: テンプレートの中のタスク。

    Returns:
        `編成  [イベントまで / 期限: 開始の2日前]` の形の文字列。
    """
    parts: list[str] = []
    if task.freq is not None:
        parts.append(_FREQ_LABELS[task.freq])
    elif task.linked and task.event_relation is not None:
        parts.append(_RELATION_LABELS[task.event_relation])
    if task.start_offset is not None:
        parts.append(f"開始: {describe_offset(task.start_offset)}")
    if task.due_offset is not None:
        parts.append(f"期限: {describe_offset(task.due_offset)}")
    if task.progress_target is not None:
        parts.append(f"{task.progress_target}回")
    return f"{task.title}  [{' / '.join(parts)}]" if parts else task.title


class TemplateEventDialog(QDialog):
    """テンプレートを選び、名前と日程を決めてイベントを作る。名前の変更と削除もここで行う。

    Args:
        store: データ操作の窓口。
        parent: ダイアログの親。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._template: EventTemplate | None = None
        self.setWindowTitle("テンプレートからイベントを作成")
        self.setMinimumSize(720, 440)

        body = QHBoxLayout()
        body.addLayout(self._build_list(), 2)
        body.addWidget(self._build_pages(), 3)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("作成")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)

        self._templates.currentItemChanged.connect(self._on_template_changed)
        self._start.dateTimeChanged.connect(self._follow_start)
        self.reload()

    def _build_list(self) -> QVBoxLayout:
        """左側のテンプレートの一覧と、名前の変更・削除のボタンを作る。

        Returns:
            一覧とボタンを並べたレイアウト。
        """
        self._templates = QListWidget()
        self._templates.setObjectName("templateList")
        rename = QPushButton("名前を変更…")
        delete = QPushButton("削除…")
        self._rename, self._delete = rename, delete
        left_buttons = QHBoxLayout()
        left_buttons.addWidget(rename)
        left_buttons.addWidget(delete)
        left = QVBoxLayout()
        title = QLabel("テンプレート")
        title.setObjectName("paneTitle")
        left.addWidget(title)
        left.addWidget(self._templates, 1)
        left.addLayout(left_buttons)
        rename.clicked.connect(self._rename_current)
        delete.clicked.connect(self._delete_current)
        return left

    def _build_pages(self) -> QStackedWidget:
        """右側の、選んだテンプレートの内容と、テンプレートがないときの案内を作る。

        Returns:
            内容と案内を切り替えるウィジェット。
        """
        self._name = QLineEdit()
        self._category = QLabel()
        self._start = _datetime_edit()
        self._end = _datetime_edit()
        self._tasks = QListWidget()
        self._tasks.setObjectName("templateTasks")
        self._tasks.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        info = QLabel(
            "タスクの日付は、イベントの開始日時からのずれのまま作ります。"
            "完了状態と進捗は初期状態です。"
        )
        info.setObjectName("info")
        info.setWordWrap(True)
        form = QFormLayout()
        form.addRow("名前", self._name)
        form.addRow("分類", self._category)
        form.addRow("開始", self._start)
        form.addRow("終了", self._end)
        detail = QWidget()
        right = QVBoxLayout(detail)
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(form)
        right.addWidget(QLabel("作られるタスク"))
        right.addWidget(self._tasks, 1)
        right.addWidget(info)

        empty = QLabel(
            "テンプレートはまだありません。\n\n"
            "イベントの右クリックメニューか、詳細パネルの「テンプレートに保存…」で、"
            "イベントと配下のタスクの組をテンプレートにできます。"
        )
        empty.setObjectName("info")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._pages = QStackedWidget()
        self._pages.addWidget(detail)
        self._pages.addWidget(empty)
        return self._pages

    # --- 一覧 ---

    def reload(self, select_id: int | None = None) -> None:
        """テンプレートの一覧を読み直す。

        Args:
            select_id: 選ぶテンプレートの id。None なら今選んでいるもののまま。
        """
        current = select_id if select_id is not None else self._current_id()
        counts = self._store.template_task_counts()
        categories = {c.saved_id: c for c in self._store.categories(include_archived=True)}
        self._templates.blockSignals(True)
        self._templates.clear()
        for template in self._store.templates():
            item = QListWidgetItem(f"{template.name}(タスク {counts.get(template.id, 0)} 件)")
            item.setData(_ROLE_ID, template.id)
            category = categories.get(template.category_id)
            if category is not None:
                item.setIcon(color_icon(category.color))
            self._templates.addItem(item)
            if template.id == current:
                self._templates.setCurrentItem(item)
        if self._templates.currentItem() is None and self._templates.count():
            self._templates.setCurrentRow(0)
        self._templates.blockSignals(False)
        self._on_template_changed(self._templates.currentItem())

    def _current_id(self) -> int | None:
        """一覧で選んでいるテンプレートの id。

        Returns:
            テンプレートの id。選んでいなければ None。
        """
        item = self._templates.currentItem()
        # PySide6 の型定義は None を返さないことになっているが、選択がなければ None が返る
        return None if item is None else int(item.data(_ROLE_ID))  # ty: ignore[redundant-condition-strict]

    def _on_template_changed(self, item: QListWidgetItem | None) -> None:
        template = None if item is None else self._store.template(int(item.data(_ROLE_ID)))
        self._template = template
        has = template is not None
        self._pages.setCurrentIndex(0 if has else 1)
        for widget in (self._ok, self._rename, self._delete):
            widget.setEnabled(has)
        if template is None:
            return
        category = self._store.category(template.category_id)
        self._category.setText(category.name if category is not None else "")
        self._name.setText(template.name)
        start = suggest_start(template, self._store.now().date())
        self._start.blockSignals(True)
        self._start.setDateTime(to_qdatetime(start))
        self._start.blockSignals(False)
        self._end.setDateTime(to_qdatetime(start + template.duration))
        self._tasks.clear()
        for task, depth in template_tree(self._store.template_tasks(template.saved_id)):
            self._tasks.addItem(f"{'      ' * depth}・ {describe_task(task)}")
        if self._tasks.count() == 0:
            self._tasks.addItem("(タスクなし)")

    def _follow_start(self) -> None:
        if self._template is not None:
            start = from_qdatetime(self._start.dateTime())
            self._end.setDateTime(to_qdatetime(start + self._template.duration))

    # --- テンプレートの管理 ---

    def _rename_current(self) -> None:
        template = self._template
        if template is None:
            return
        name, accepted = QInputDialog.getText(
            self, "テンプレート名の変更", "新しいテンプレート名", text=template.name
        )
        if not accepted or not name.strip():
            return
        try:
            self._store.rename_template(template.saved_id, name)
        except TemplateNameError:
            QMessageBox.warning(
                self, self.windowTitle(), f"「{name.strip()}」というテンプレートはすでにあります。"
            )
            return
        self.reload(template.id)

    def _delete_current(self) -> None:
        template = self._template
        if template is None:
            return
        answer = QMessageBox.question(
            self,
            "テンプレートの削除",
            f"テンプレート「{template.name}」を削除しますか?\n"
            "これまでに作ったイベントとタスクはそのまま残ります。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._store.delete_template(template.saved_id)
            self.reload()

    # --- 確定 ---

    @override
    def accept(self) -> None:
        """テンプレートと日時を確かめてから閉じる。"""
        if self._template is None:
            return
        if self._end.dateTime() < self._start.dateTime():
            QMessageBox.warning(self, self.windowTitle(), "終了は開始より後の日時にしてください。")
            self._end.setFocus()
            return
        super().accept()

    def plan(self) -> EventPlan:
        """入力した名前と日程を返す。

        Returns:
            新しいイベントの名前と日程。
        """
        return EventPlan(
            self._name.text().strip(),
            from_qdatetime(self._start.dateTime()),
            from_qdatetime(self._end.dateTime()),
        )

    @property
    def template_id(self) -> int | None:
        """選んだテンプレートの id。

        Returns:
            テンプレートの id。選んでいなければ None。
        """
        return self._template.id if self._template is not None else None


def create_from_template(parent: QWidget, store: AppStore) -> Event | None:
    """テンプレートからイベントを作るダイアログを開き、作ったらそのイベントを選択する。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。

    Returns:
        作ったイベント。キャンセルしたら None。
    """
    dialog = TemplateEventDialog(store, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.template_id is None:
        return None
    plan = dialog.plan()
    event = store.create_event_from_template(
        dialog.template_id, name=plan.name, start_at=plan.start_at, end_at=plan.end_at
    )
    store.select(ItemRef(ItemKind.EVENT, event.saved_id))
    return event

"""イベントの追加・編集・複製のダイアログと、削除の確認。"""

from datetime import timedelta
from typing import TYPE_CHECKING, override

from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from neko_gantcha.domain.duplicate import EventPlan
from neko_gantcha.domain.models import Event
from neko_gantcha.services.store import ItemKind, ItemRef
from neko_gantcha.ui.widgets import color_icon, from_qdatetime, to_qdatetime

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from neko_gantcha.services.store import AppStore


class EventDialog(QDialog):
    # 入力欄を作って並べるだけの処理で、分けるとかえって追いにくくなる
    """イベントの追加・編集ダイアログ。

    Args:
        store: データ操作の窓口。
        event: 編集するイベント。None なら追加。
        parent: ダイアログの親。
    """

    def __init__(  # noqa: PLR0915
        self, store: AppStore, event: Event | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._event = event
        self.setWindowTitle("イベントの編集" if event else "イベントの追加")
        self.setMinimumWidth(420)

        self._name = QLineEdit()
        self._category = QComboBox()
        for category in store.categories(include_archived=event is not None):
            self._category.addItem(color_icon(category.color), category.name, category.id)
        self._start = _datetime_edit()
        self._end = _datetime_edit()
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://…")
        self._note = QPlainTextEdit()
        self._note.setFixedHeight(90)

        if event is not None:
            self._name.setText(event.name)
            self._category.setCurrentIndex(self._category.findData(event.category_id))
            self._start.setDateTime(to_qdatetime(event.start_at))
            self._end.setDateTime(to_qdatetime(event.end_at))
            self._url.setText(event.url)
            self._note.setPlainText(event.note)
        else:
            index = self._category.findData(store.filter.category_id)
            self._category.setCurrentIndex(max(index, 0))
            start = store.now().replace(minute=0, second=0)
            self._start.setDateTime(to_qdatetime(start))
            self._end.setDateTime(to_qdatetime(start + timedelta(days=7)))

        form = QFormLayout()
        form.addRow("名前", self._name)
        form.addRow("分類", self._category)
        form.addRow("開始", self._start)
        form.addRow("終了", self._end)
        form.addRow("URL", self._url)
        form.addRow("メモ", self._note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._name.setFocus()

    @override
    def accept(self) -> None:
        """名前と日時を確かめてから閉じる。"""
        if not self._name.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "名前を入力してください。")
            self._name.setFocus()
            return
        if self._end.dateTime() < self._start.dateTime():
            QMessageBox.warning(self, self.windowTitle(), "終了は開始より後の日時にしてください。")
            self._end.setFocus()
            return
        super().accept()

    def result_event(self) -> Event:
        """入力内容を反映したイベントを作る(保存はしない)。

        Returns:
            入力内容のイベント。
        """
        return Event(
            id=self._event.id if self._event is not None else None,
            category_id=self._category.currentData(),
            name=self._name.text().strip(),
            start_at=from_qdatetime(self._start.dateTime()),
            end_at=from_qdatetime(self._end.dateTime()),
            url=self._url.text().strip(),
            note=self._note.toPlainText(),
        )


def _datetime_edit() -> QDateTimeEdit:
    edit = QDateTimeEdit()
    edit.setDisplayFormat("yyyy/MM/dd HH:mm")
    edit.setCalendarPopup(True)
    return edit


def edit_event(parent: QWidget, store: AppStore, event_id: int | None = None) -> Event | None:
    """ダイアログを開いてイベントを追加・編集する。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        event_id: 編集するイベントの id。None なら追加。

    Returns:
        保存したイベント。キャンセルしたら None。
    """
    event = store.get_event(event_id) if event_id is not None else None
    dialog = EventDialog(store, event, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return store.save_event(dialog.result_event())


class DuplicateEventDialog(QDialog):
    """イベントを配下タスクごと複製する。終了は開始に合わせて同じ長さでずらす。

    Args:
        store: データ操作の窓口。
        event: 複製元のイベント。
        parent: ダイアログの親。
    """

    def __init__(self, store: AppStore, event: Event, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("イベントの複製")
        self.setMinimumWidth(420)
        self._event = event
        self._duration = event.end_at - event.start_at

        self._name = QLineEdit(event.name)
        self._start = _datetime_edit()
        self._end = _datetime_edit()
        start = store.suggest_duplicate_start(event)
        self._start.setDateTime(to_qdatetime(start))
        self._end.setDateTime(to_qdatetime(start + self._duration))
        task_count = len(store.event_tasks(event.saved_id))
        info = QLabel(
            f"元のイベント: {event.start_at:%Y/%m/%d %H:%M} 〜 {event.end_at:%Y/%m/%d %H:%M}\n"
            + (
                f"配下のタスク {task_count} 件も、開始日時の差だけ日付をずらして複製します。"
                "完了状態と進捗は初期化します。"
                if task_count
                else "配下のタスクはありません。"
            )
        )
        info.setObjectName("info")
        info.setWordWrap(True)

        form = QFormLayout()
        form.addRow("名前", self._name)
        form.addRow("開始", self._start)
        form.addRow("終了", self._end)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("複製")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(buttons)

        self._start.dateTimeChanged.connect(self._follow_start)
        self._start.setFocus()

    def _follow_start(self) -> None:
        """開始を変えたら、同じ長さになるよう終了を合わせる。"""
        self._end.setDateTime(to_qdatetime(from_qdatetime(self._start.dateTime()) + self._duration))

    @override
    def accept(self) -> None:
        """日時を確かめてから閉じる。"""
        if self._end.dateTime() < self._start.dateTime():
            QMessageBox.warning(self, self.windowTitle(), "終了は開始より後の日時にしてください。")
            self._end.setFocus()
            return
        super().accept()

    def plan(self) -> EventPlan:
        """入力した名前と日程を返す。

        Returns:
            新しいイベントの名前と日程。名前が空なら元の名前。
        """
        return EventPlan(
            self._name.text().strip() or self._event.name,
            from_qdatetime(self._start.dateTime()),
            from_qdatetime(self._end.dateTime()),
        )


def duplicate_event(parent: QWidget, store: AppStore, event_id: int) -> Event | None:
    """複製ダイアログを開き、確定したら複製したイベントを選択する。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        event_id: 複製元のイベントの id。

    Returns:
        複製したイベント。キャンセルしたら None。
    """
    event = store.get_event(event_id)
    if event is None:
        return None
    dialog = DuplicateEventDialog(store, event, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    plan = dialog.plan()
    new_event = store.duplicate_event(
        event_id, name=plan.name, start_at=plan.start_at, end_at=plan.end_at
    )
    store.select(ItemRef(ItemKind.EVENT, new_event.saved_id))
    return new_event


def confirm_delete_event(parent: QWidget, store: AppStore, event_id: int) -> bool:
    """確認してからイベントを削除する。配下タスクがあれば扱いを選ばせる。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        event_id: 削除するイベントの id。

    Returns:
        削除したら True。
    """
    event = store.get_event(event_id)
    if event is None:
        return False
    task_count = len(store.event_tasks(event_id))
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("イベントの削除")
    if task_count == 0:
        box.setText(f"「{event.name}」を削除しますか?\nこの操作は元に戻せません。")
        delete_all = box.addButton("削除", QMessageBox.ButtonRole.DestructiveRole)
        keep_tasks = None
    else:
        box.setText(
            f"「{event.name}」を削除します。紐付くタスクが {task_count} 件あります。\n"
            "タスクも一緒に削除しますか?"
        )
        box.setInformativeText(
            "タスクを残すと、イベントとの紐付けだけが外れます。"
            "イベント期間限定の日課は、常設の日課になります。\nこの操作は元に戻せません。"
        )
        delete_all = box.addButton("タスクも削除", QMessageBox.ButtonRole.DestructiveRole)
        keep_tasks = box.addButton("タスクは残す", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is delete_all:
        store.delete_event(event_id, delete_tasks=True)
        return True
    if keep_tasks is not None and clicked is keep_tasks:
        store.delete_event(event_id, delete_tasks=False)
        return True
    return False

"""プロジェクトの追加・編集ダイアログと、削除の確認。"""

from datetime import date
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from neko_gantcha.domain.models import Project, ProjectStatus
from neko_gantcha.ui.widgets import color_icon

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from neko_gantcha.services.store import AppStore

_STATUS_LABELS = [
    (ProjectStatus.ACTIVE, "進行中"),
    (ProjectStatus.DONE, "完了"),
    (ProjectStatus.ARCHIVED, "アーカイブ(一覧に出さない)"),
]


class ProjectDialog(QDialog):
    # 入力欄を作って並べるだけの処理で、分けるとかえって追いにくくなる
    """プロジェクトの追加・編集ダイアログ。

    Args:
        store: データ操作の窓口。
        project: 編集するプロジェクト。None なら追加。
        parent: ダイアログの親。
    """

    def __init__(  # noqa: PLR0915
        self, store: AppStore, project: Project | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._project = project
        self.setWindowTitle("プロジェクトの編集" if project else "プロジェクトの追加")
        self.setMinimumWidth(420)

        self._name = QLineEdit()
        self._name.setPlaceholderText("例: 次の協力バトルまでに編成を完成")
        self._category = QComboBox()
        for category in store.categories(include_archived=project is not None):
            self._category.addItem(color_icon(category.color), category.name, category.id)
        self._goal = QPlainTextEdit()
        self._goal.setPlaceholderText("目標(何ができたら完了か)")
        self._goal.setFixedHeight(70)
        self._has_target = QCheckBox()
        self._target = QDateEdit()
        self._target.setDisplayFormat("yyyy/MM/dd")
        self._target.setCalendarPopup(True)
        target_row = QHBoxLayout()
        target_row.addWidget(self._has_target)
        target_row.addWidget(self._target, 1)
        self._event = QComboBox()
        self._status = QComboBox()
        for status, label in _STATUS_LABELS:
            self._status.addItem(label, status.value)

        today = store.now().date()
        if project is not None:
            self._name.setText(project.name)
            self._category.setCurrentIndex(self._category.findData(project.category_id))
            self._goal.setPlainText(project.goal)
            self._has_target.setChecked(project.target_date is not None)
            target = project.target_date or today
            self._status.setCurrentIndex(self._status.findData(project.status.value))
        else:
            index = self._category.findData(store.filter.category_id)
            self._category.setCurrentIndex(max(index, 0))
            target = today
            self._status.setEnabled(False)
        self._target.setDate(QDate(target.year, target.month, target.day))
        self._reload_events()
        self._update_enabled()

        form = QFormLayout()
        form.addRow("名前", self._name)
        form.addRow("分類", self._category)
        form.addRow("目標", self._goal)
        form.addRow("目標日", target_row)
        form.addRow("イベント", self._event)
        form.addRow("状態", self._status)

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

        self._category.currentIndexChanged.connect(self._reload_events)
        self._has_target.toggled.connect(self._update_enabled)
        self._name.setFocus()

    def _reload_events(self) -> None:
        """同じ分類の、終了していないイベントを選択肢にする。

        選択中のものは終了していても含める。
        """
        current = self._event.currentData() if self._event.count() else None
        if current is None and self._project is not None:
            current = self._project.event_id
        now = self._store.now()
        category_id = self._category.currentData()
        self._event.clear()
        self._event.addItem("なし", None)
        for event in self._store.events_by_id().values():
            if event.category_id != category_id:
                continue
            if event.id == current or event.end_at > now:
                label = f"{event.name}({event.start_at:%m/%d}〜{event.end_at:%m/%d})"
                self._event.addItem(label, event.id)
        self._event.setCurrentIndex(max(self._event.findData(current), 0))
        self._event.setToolTip("目標日がなければ、このイベントの開始を締め切りにします。")

    def _update_enabled(self) -> None:
        self._target.setEnabled(self._has_target.isChecked())

    @override
    def accept(self) -> None:
        """名前が空でなければ閉じる。"""
        if not self._name.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "名前を入力してください。")
            self._name.setFocus()
            return
        super().accept()

    def result_project(self) -> Project:
        """入力内容を反映したプロジェクトを作る(保存はしない)。

        Returns:
            入力内容のプロジェクト。
        """
        picked = self._target.date()
        target = date(picked.year(), picked.month(), picked.day())
        if not self._has_target.isChecked():
            target = None
        return Project(
            id=self._project.id if self._project is not None else None,
            category_id=self._category.currentData(),
            name=self._name.text().strip(),
            goal=self._goal.toPlainText().strip(),
            target_date=target,
            event_id=self._event.currentData(),
            status=ProjectStatus(self._status.currentData()),
        )


def edit_project(parent: QWidget, store: AppStore, project_id: int | None = None) -> Project | None:
    """ダイアログを開いてプロジェクトを追加・編集する。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        project_id: 編集するプロジェクトの id。None なら追加。

    Returns:
        保存したプロジェクト。キャンセルしたら None。
    """
    project = store.project(project_id) if project_id is not None else None
    dialog = ProjectDialog(store, project, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return store.save_project(dialog.result_project())


def confirm_delete_project(parent: QWidget, store: AppStore, project_id: int) -> bool:
    """確認してからプロジェクトを削除する。タスクは残る。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        project_id: 削除するプロジェクトの id。

    Returns:
        削除したら True。
    """
    project = store.project(project_id)
    if project is None:
        return False
    count = len(store.project_tasks(project_id))
    detail = ""
    if count:
        detail = f"\n属するタスク {count} 件は残り、プロジェクトとの紐付けだけが外れます。"
    answer = QMessageBox.question(
        parent,
        "プロジェクトの削除",
        f"「{project.name}」を削除しますか?{detail}\nこの操作は元に戻せません。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    store.delete_project(project_id)
    return True

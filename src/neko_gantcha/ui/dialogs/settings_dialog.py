"""設定画面。配色と、分類の追加・名前・色・リセット時刻・並び順・アーカイブ・削除。

入力を確定した時点で保存する(保存ボタンは置かない)。
"""

from dataclasses import replace
from datetime import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
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
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.models import Category, Theme
from neko_gantcha.ui.theme import THEME_LABELS
from neko_gantcha.ui.widgets import color_icon

if TYPE_CHECKING:
    from neko_gantcha.services.store import AppStore

_ROLE_ID = Qt.ItemDataRole.UserRole
WEEKDAYS = ["月曜", "火曜", "水曜", "木曜", "金曜", "土曜", "日曜"]


class SettingsDialog(QDialog):
    """設定画面。配色と分類を編集する。

    Args:
        store: データ操作の窓口。
        parent: ダイアログの親。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._category: Category | None = None
        self._loading = False
        self.setWindowTitle("設定")
        self.setMinimumSize(560, 380)

        body = QHBoxLayout()
        body.addLayout(self._build_list(), 2)
        body.addWidget(self._build_form(), 3)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_theme_row())
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        self.reload()

    def _build_list(self) -> QVBoxLayout:
        """左側の分類の一覧と、追加・並べ替え・削除のボタンを作る。

        Returns:
            一覧とボタンを並べたレイアウト。
        """
        title = QLabel("分類")
        title.setObjectName("paneTitle")
        self._list = QListWidget()
        self._list.setObjectName("filterList")
        add = QPushButton("追加…")
        up = QToolButton()
        up.setText("↑")
        up.setToolTip("上へ")
        down = QToolButton()
        down.setText("↓")
        down.setToolTip("下へ")
        self._delete = QPushButton("削除…")
        list_buttons = QHBoxLayout()
        for button in (add, up, down):
            list_buttons.addWidget(button)
        list_buttons.addStretch(1)
        list_buttons.addWidget(self._delete)
        left = QVBoxLayout()
        left.addWidget(title)
        left.addWidget(self._list, 1)
        left.addLayout(list_buttons)

        self._list.currentItemChanged.connect(self._on_current_changed)
        add.clicked.connect(self._add)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(+1))
        self._delete.clicked.connect(self._remove)
        return left

    def _build_form(self) -> QWidget:
        """右側の、選んだ分類の入力欄を作る。

        Returns:
            入力欄を並べたウィジェット。
        """
        self._name = QLineEdit()
        self._color = QPushButton()
        self._color.setToolTip("色を選ぶ")
        self._daily = QTimeEdit()
        self._daily.setDisplayFormat("HH:mm")
        self._daily.setKeyboardTracking(False)
        self._weekday = QComboBox()
        self._weekday.addItems(WEEKDAYS)
        self._monthly = QSpinBox()
        self._monthly.setRange(1, 31)
        self._monthly.setSuffix(" 日")
        self._monthly.setKeyboardTracking(False)
        self._archived = QCheckBox("アーカイブ(一覧や追加先に出さない)")
        note = QLabel(
            "リセット時刻は、日課・週課・月課の区切りに使います。"
            "月のリセット日がない月(31日など)は月末にリセットします。"
        )
        note.setObjectName("info")
        note.setWordWrap(True)
        self._usage = QLabel()
        self._usage.setObjectName("info")

        form = QFormLayout()
        form.addRow("名前", self._name)
        form.addRow("色", self._color)
        form.addRow("毎日のリセット", self._daily)
        form.addRow("毎週のリセット", self._weekday)
        form.addRow("毎月のリセット", self._monthly)
        form.addRow("", self._archived)
        self._form = QWidget()
        right = QVBoxLayout(self._form)
        right.setContentsMargins(12, 0, 0, 0)
        right.addLayout(form)
        right.addWidget(note)
        right.addStretch(1)
        right.addWidget(self._usage)

        self._name.editingFinished.connect(self._commit)
        self._color.clicked.connect(self._choose_color)
        self._daily.timeChanged.connect(self._commit)
        self._weekday.activated.connect(self._commit)
        self._monthly.valueChanged.connect(self._commit)
        self._archived.toggled.connect(self._commit)
        return self._form

    def _build_theme_row(self) -> QHBoxLayout:
        """配色の選択欄を作る。

        Returns:
            配色の選択欄を並べたレイアウト。
        """
        self._theme = QComboBox()
        for theme, label in THEME_LABELS.items():
            self._theme.addItem(label, theme.value)
        self._theme.setCurrentIndex(self._theme.findData(self._store.theme().value))
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("配色"))
        theme_row.addWidget(self._theme)
        theme_row.addStretch(1)
        self._theme.activated.connect(self._change_theme)
        return theme_row

    def _change_theme(self) -> None:
        self._store.set_theme(Theme(self._theme.currentData()))

    # --- 一覧 ---

    def reload(self, select_id: int | None = None) -> None:
        """分類の一覧を読み直す。

        Args:
            select_id: 選ぶ分類の id。None なら今選んでいる分類のまま。
        """
        if select_id is None and self._category is not None:
            select_id = self._category.id
        self._loading = True
        self._list.clear()
        current_row = 0
        for row, category in enumerate(self._store.categories(include_archived=True)):
            label = category.name + ("(アーカイブ)" if category.archived else "")
            item = QListWidgetItem(color_icon(category.color), label)
            item.setData(_ROLE_ID, category.id)
            if category.archived:
                item.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(item)
            if category.id == select_id:
                current_row = row
        self._loading = False
        if self._list.count():
            self._list.setCurrentRow(current_row)
            self._show(self._list.currentItem().data(_ROLE_ID))
        else:
            self._category = None
            self._form.setEnabled(False)

    def _on_current_changed(self, current: QListWidgetItem | None) -> None:
        if self._loading or current is None:
            return
        self._show(current.data(_ROLE_ID))

    def _show(self, category_id: int) -> None:
        category = self._store.category(category_id)
        self._category = category
        self._form.setEnabled(category is not None)
        if category is None:
            return
        self._loading = True
        self._name.setText(category.name)
        self._set_color_button(category.color)
        reset = category.daily_reset_time
        self._daily.setTime(QTime(reset.hour, reset.minute))
        self._weekday.setCurrentIndex(category.weekly_reset_weekday)
        self._monthly.setValue(category.monthly_reset_day)
        self._archived.setChecked(category.archived)
        usage = self._store.category_usage(category.saved_id)
        self._usage.setText(f"この分類のイベント・プロジェクト・タスク・テンプレート: {usage} 件")
        self._delete.setEnabled(usage == 0)
        self._delete.setToolTip(
            "" if usage == 0 else "使用中の分類は削除できません。隠すときはアーカイブにします。"
        )
        self._loading = False

    def _set_color_button(self, color: str) -> None:
        self._color.setIcon(color_icon(color, 14))
        self._color.setText(color.upper())

    # --- 編集 ---

    def _commit(self) -> None:
        if self._loading or self._category is None:
            return
        qtime = self._daily.time()
        updated = replace(
            self._category,
            name=self._name.text().strip() or self._category.name,
            daily_reset_time=time(qtime.hour(), qtime.minute()),
            weekly_reset_weekday=self._weekday.currentIndex(),
            monthly_reset_day=self._monthly.value(),
            archived=self._archived.isChecked(),
        )
        self._save(updated)

    def _save(self, category: Category) -> None:
        if category == self._category:
            return
        try:
            saved = self._store.save_category(category)
        except ValueError as exc:
            QMessageBox.warning(self, "設定", _message(exc))
            if self._category is not None:
                self._show(self._category.saved_id)
            return
        self.reload(saved.id)

    def _choose_color(self) -> None:
        if self._category is None:
            return
        color = QColorDialog.getColor(QColor(self._category.color), self, "分類の色")
        if color.isValid():
            self._save(replace(self._category, color=color.name().upper()))

    def _add(self) -> None:
        name, accepted = QInputDialog.getText(self, "分類の追加", "分類の名前")
        if not accepted or not name.strip():
            return
        try:
            category = self._store.save_category(Category(name=name, color="#8B5CF6"))
        except ValueError as exc:
            QMessageBox.warning(self, "分類の追加", _message(exc))
            return
        self.reload(category.id)
        self._name.setFocus()

    def _move(self, offset: int) -> None:
        if self._category is not None:
            self._store.move_category(self._category.saved_id, offset)
            self.reload(self._category.id)

    def _remove(self) -> None:
        category = self._category
        if category is None:
            return
        answer = QMessageBox.question(
            self,
            "分類の削除",
            f"分類「{category.name}」を削除しますか?\nこの操作は元に戻せません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._store.delete_category(category.saved_id)
        except ValueError as exc:
            QMessageBox.warning(self, "分類の削除", _message(exc))
            return
        self._category = None
        self.reload()


def _message(exc: ValueError) -> str:
    """ストアのエラーを画面向けの文にする。

    Returns:
        「。」で終わる文。
    """
    text = str(exc)
    return text if text.endswith("。") else f"{text}。"

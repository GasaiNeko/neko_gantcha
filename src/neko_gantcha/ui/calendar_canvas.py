"""カレンダーの自作描画ビュー(月グリッド、週表示)で共通に使う部品。

描画時に項目と日付の矩形を記録しておき、クリック判定はその矩形で行う。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPalette, QPen
from PySide6.QtWidgets import QWidget

from neko_gantcha.services.store import ItemKind, ItemRef
from neko_gantcha.ui.widgets import mix_colors

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QContextMenuEvent, QFont, QMouseEvent, QPainter

    from neko_gantcha.domain.calendar import BandSegment
    from neko_gantcha.domain.models import Category
    from neko_gantcha.domain.todo import TodoRow


WEEKDAYS = "月火水木金土日"
COLOR_DONE = QColor("#43A047")
COLOR_MUTED = QColor("#9E9E9E")
COLOR_SATURDAY = QColor("#4F8FE8")
COLOR_SUNDAY = QColor("#E5686A")
COLOR_NOW = QColor("#E53935")
SATURDAY_COL = 5
SUNDAY_COL = 6
ENDED_ALPHA = 110  # 終了したイベントの帯の不透明度
_WHITE = QColor("#FFFFFF")
_NO_CATEGORY_COLOR = "#888888"


def weekday_color(weekday: int, text: QColor) -> QColor:
    """曜日の見出しの色を返す。土曜は青、日曜は赤。

    Args:
        weekday: 曜日(datetime.weekday() と同じ 0=月曜〜6=日曜)。
        text: 平日の文字の色。

    Returns:
        曜日の色。
    """
    if weekday == SATURDAY_COL:
        return COLOR_SATURDAY
    if weekday == SUNDAY_COL:
        return COLOR_SUNDAY
    return text


@dataclass(frozen=True, slots=True)
class CanvasState:
    """描画ビューに渡す、表示内容のほかの情報。

    Attributes:
        categories: 分類 id → 分類。
        now: 現在の日時。
        selected_day: 選択中の日。
        selected: 選択中の項目。
    """

    categories: Mapping[int, Category]
    now: datetime
    selected_day: date | None
    selected: ItemRef | None

    def category_color(self, category_id: int) -> QColor:
        """分類の色を返す。

        Args:
            category_id: 分類の id。

        Returns:
            分類の色。分類がなければ灰色。
        """
        category = self.categories.get(category_id)
        return QColor(category.color if category is not None else _NO_CATEGORY_COLOR)


@dataclass(frozen=True, slots=True)
class PaintStyle:
    """描画に使う色と字。ライト・ダークのどちらでも読めるよう、パレットから作る。

    Attributes:
        base: 背景の色。
        text: 文字の色。
        highlight: 選択の色。
        highlighted_text: 選択の上の文字の色。
        small: 小さい字。
        metrics: 小さい字の寸法。
    """

    base: QColor
    text: QColor
    highlight: QColor
    highlighted_text: QColor
    small: QFont
    metrics: QFontMetrics

    def mix(self, ratio: float) -> QColor:
        """背景に文字の色を混ぜた色を返す。罫線や薄い文字に使う。

        Args:
            ratio: 文字の色を混ぜる割合(0〜1)。

        Returns:
            混ぜた色。
        """
        return mix_colors(self.base, self.text, ratio)


def paint_style(widget: QWidget) -> PaintStyle:
    """ウィジェットのパレットと字から描画の設定を作る。

    Args:
        widget: 描画するウィジェット。

    Returns:
        描画に使う色と字。
    """
    palette = widget.palette()
    small = widget.font()
    small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
    return PaintStyle(
        base=palette.color(QPalette.ColorRole.Base),
        text=palette.color(QPalette.ColorRole.Text),
        highlight=palette.color(QPalette.ColorRole.Highlight),
        highlighted_text=palette.color(QPalette.ColorRole.HighlightedText),
        small=small,
        metrics=QFontMetrics(small),
    )


class CalendarCanvas(QWidget):
    """クリックで日付と項目を選べる描画ビューの土台。

    day_double_clicked には、日付(終日)か日時(時間軸の上)を渡す。

    Args:
        parent: 親のウィジェット。
    """

    day_clicked = Signal(object)  # date
    item_clicked = Signal(object)  # ItemRef
    day_double_clicked = Signal(object)  # date | datetime
    context_requested = Signal(object, object)  # ItemRef | None, QPoint(グローバル座標)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item_hits: list[tuple[QRect, ItemRef]] = []
        self._day_hits: list[tuple[QRect, date]] = []
        self._state: CanvasState | None = None

    # --- クリック判定(描画時に記録した矩形を使う) ---

    def item_at(self, pos: QPoint) -> ItemRef | None:
        """位置にある項目を返す。

        Args:
            pos: ウィジェットの中の位置。

        Returns:
            いちばん上に描かれた項目。なければ None。
        """
        for rect, ref in reversed(self._item_hits):
            if rect.contains(pos):
                return ref
        return None

    def day_at(self, pos: QPoint) -> date | None:
        """位置にある日付を返す。

        Args:
            pos: ウィジェットの中の位置。

        Returns:
            日付。なければ None。
        """
        for rect, day in self._day_hits:
            if rect.contains(pos):
                return day
        return None

    def item_rects(self, ref: ItemRef) -> list[QRect]:
        """指定した項目が描かれている矩形を返す(テストと確認用)。

        Args:
            ref: 項目。

        Returns:
            矩形の一覧。
        """
        return [rect for rect, hit in self._item_hits if hit == ref]

    def double_click_target(self, pos: QPoint) -> date | datetime | None:
        """ダブルクリックで追加するタスクの期限を返す。既定ではその日。

        Args:
            pos: ウィジェットの中の位置。

        Returns:
            日付か日時。日付の外なら None。
        """
        return self.day_at(pos)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        day = self.day_at(pos)
        if day is not None:
            self.day_clicked.emit(day)
        ref = self.item_at(pos)
        if ref is not None:
            self.item_clicked.emit(ref)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self.item_at(pos) is not None:
            return
        target = self.double_click_target(pos)
        if target is not None:
            self.day_double_clicked.emit(target)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        day = self.day_at(event.pos())
        if day is not None:
            self.day_clicked.emit(day)
        ref = self.item_at(event.pos())
        if ref is not None:
            self.item_clicked.emit(ref)
        self.context_requested.emit(ref, event.globalPos())

    # --- 共通の描画 ---

    def _draw_event_band(
        self, painter: QPainter, style: PaintStyle, segment: BandSegment, *, rect: QRect
    ) -> None:
        """イベントの帯を描き、クリック判定に記録する。"""
        state = self._state
        if state is None:
            return
        event = segment.event
        color = state.category_color(event.category_id)
        if event.end_at <= state.now:
            color.setAlpha(ENDED_ALPHA)  # 終了したイベントは薄く
        ref = ItemRef(ItemKind.EVENT, event.saved_id)
        self._item_hits.append((rect, ref))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 4, 4)
        if ref == state.selected:
            painter.setPen(QPen(style.text, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
        painter.setPen(_WHITE)
        text_rect = rect.adjusted(6, 0, -4, 0)
        label = style.metrics.elidedText(event.name, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label
        )

    def _draw_task_label(
        self, painter: QPainter, style: PaintStyle, row: TodoRow, *, rect: QRect
    ) -> None:
        """期限ラベル1行(分類の色の点とタスク名)を描き、クリック判定に記録する。"""
        state = self._state
        if state is None:
            return
        ref = ItemRef(ItemKind.TASK, row.task.saved_id)
        self._item_hits.append((rect, ref))
        selected = ref == state.selected
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(style.highlight)
            painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(state.category_color(row.task.category_id))
        painter.drawEllipse(rect.left() + 4, rect.center().y() - 3, 7, 7)
        if selected:
            painter.setPen(style.highlighted_text)
        else:
            painter.setPen(COLOR_MUTED if row.done else style.text)
        text_rect = rect.adjusted(15, 0, -2, 0)
        title = ("✓ " if row.done else "") + row.task.title
        label = style.metrics.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label
        )


def keep_selected_visible(
    rows: list[TodoRow], shown: list[TodoRow], selected: ItemRef | None
) -> list[TodoRow]:
    """選択中のタスクが入りきらない側にあるなら、最後の1行と入れ替えて見せる。

    Args:
        rows: その日の期限ラベルすべて。
        shown: 入りきる分のラベル。
        selected: 選択中の項目。

    Returns:
        出すラベル。
    """
    if selected is None or selected.kind is not ItemKind.TASK:
        return shown
    hidden = [row for row in rows[len(shown) :] if row.task.id == selected.id]
    if not hidden:
        return shown
    return [*shown[:-1], hidden[0]]

"""カレンダーの週表示。終日欄と時間軸からなる。

上の終日欄には日付、日課の達成数、イベントの帯、23:59 期限のタスクを出し、
下の時間軸には1日の中に収まるイベントと、時刻つきの期限のタスクを出す。
配置の計算は domain/calendar.py の build_week が行い、ここでは描画とクリック判定だけをする。
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from neko_gantcha.services.store import ItemKind, ItemRef
from neko_gantcha.ui.calendar_canvas import (
    COLOR_DONE,
    COLOR_MUTED,
    COLOR_NOW,
    ENDED_ALPHA,
    WEEKDAYS,
    CalendarCanvas,
    keep_selected_visible,
    paint_style,
    weekday_color,
)
from neko_gantcha.ui.widgets import mix_colors

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

    from neko_gantcha.domain.calendar import TimedBlock, WeekView
    from neko_gantcha.domain.todo import TodoRow
    from neko_gantcha.ui.calendar_canvas import CanvasState, PaintStyle

GUTTER = 48  # 左端の時刻の欄の幅
HOUR_H = 40  # 時間軸の1時間の高さ
HOURS = 24
_DATE_H = 42  # 終日欄の、曜日と日付の行の高さ
_LANE_H = 18  # イベントの帯1段の高さ
_LABEL_H = 17  # 期限ラベル1行の高さ
_MAX_LABEL_ROWS = 3  # 終日欄に出す期限ラベルの最大行数(超えた分は「他 N 件」)
_SLOT_MINUTES = 30  # ダブルクリックで追加するときの時刻の刻み
_WIDE_COLUMN = 90  # これより広い列では、日課の達成数に「日課」と書き添える
_WHITE = QColor("#FFFFFF")


def column_edges(width: int) -> list[int]:
    """7日ぶんの列の左右の x 座標を返す。左端の時刻の欄を除いた幅を7等分する。

    Args:
        width: ウィジェットの幅。

    Returns:
        列の境目の x 座標(8個)。
    """
    return [GUTTER + round(i * (width - GUTTER) / 7) for i in range(8)]


class WeekHeader(CalendarCanvas):
    """週表示の上部(終日欄)。高さは帯の段数とラベルの行数に合わせて変わる。

    Args:
        parent: 親のウィジェット。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: WeekView | None = None
        self._lanes = 0
        self._label_rows = 0
        self.setFixedHeight(_DATE_H + 8)

    def set_data(self, view: WeekView, state: CanvasState) -> None:
        """表示する内容を受け取り、高さを合わせて描き直す。

        Args:
            view: 週表示の内容。
            state: 分類、現在時刻、選択などの情報。
        """
        self._view = view
        self._state = state
        self._lanes = max((s.lane for s in view.bands), default=-1) + 1
        self._label_rows = min(_MAX_LABEL_ROWS, max(len(rows) for rows in view.all_day))
        self.setFixedHeight(_DATE_H + self._lanes * _LANE_H + self._label_rows * _LABEL_H + 8)
        self.update()

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        self._item_hits = []
        self._day_hits = []
        view, state = self._view, self._state
        if view is None or state is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        style = paint_style(self)
        xs = column_edges(self.width())
        painter.fillRect(self.rect(), style.base)
        if self._lanes or self._label_rows:
            painter.setFont(style.small)
            painter.setPen(style.mix(0.45))
            painter.drawText(
                QRect(0, _DATE_H, GUTTER - 4, _LANE_H),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "終日",
            )
        self._draw_days(painter, style, view)
        painter.setFont(style.small)
        for segment in view.bands:
            left = xs[segment.first_col] + (3 if segment.starts_here else 0)
            right = xs[segment.last_col + 1] - (3 if segment.ends_here else 0)
            rect = QRect(left, _DATE_H + segment.lane * _LANE_H, right - left, _LANE_H - 3)
            self._draw_event_band(painter, style, segment, rect=rect)
        top = _DATE_H + self._lanes * _LANE_H
        for col, rows in enumerate(view.all_day):
            area = QRect(xs[col], top, xs[col + 1] - xs[col], self.height() - top)
            self._draw_labels(painter, style, rows, area=area)
        self._draw_lines(painter, style, state)
        painter.end()

    def _draw_days(self, painter: QPainter, style: PaintStyle, view: WeekView) -> None:
        """曜日と日付、日課の達成数を描く。"""
        xs = column_edges(self.width())
        today = self._state.now.date() if self._state is not None else None
        big = self.font()
        big.setPointSizeF(big.pointSizeF() + 3)
        for col, cell in enumerate(view.days):
            rect = QRect(xs[col], 0, xs[col + 1] - xs[col], self.height())
            self._day_hits.append((rect, cell.day))
            painter.setFont(style.small)
            weekday = cell.day.weekday()
            painter.setPen(weekday_color(weekday, style.text))
            painter.drawText(
                QRect(rect.left() + 6, 2, 24, 16), Qt.AlignmentFlag.AlignLeft, WEEKDAYS[weekday]
            )
            number_rect = QRect(rect.left() + 4, 17, 30, 22)
            painter.setFont(big)
            if cell.day == today:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(style.highlight)
                painter.drawRoundedRect(number_rect, 11, 11)
                painter.setPen(style.highlighted_text)
            else:
                painter.setPen(weekday_color(weekday, style.text))
            painter.drawText(number_rect, Qt.AlignmentFlag.AlignCenter, str(cell.day.day))
            if cell.daily_total:
                complete = cell.daily_done == cell.daily_total
                painter.setFont(style.small)
                painter.setPen(COLOR_DONE if complete else style.mix(0.45))
                prefix = "日課 " if rect.width() >= _WIDE_COLUMN else ""
                painter.drawText(
                    QRect(rect.left(), 20, rect.width() - 6, 18),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{prefix}{cell.daily_done}/{cell.daily_total}",
                )

    def _draw_lines(self, painter: QPainter, style: PaintStyle, state: CanvasState) -> None:
        """罫線と、選択中の日の枠を描く。"""
        width, height = self.width(), self.height()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(style.mix(0.18), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for x in column_edges(width)[1:-1]:
            painter.drawLine(x, 0, x, height)
        painter.drawLine(0, height - 1, width, height - 1)
        for rect, day in self._day_hits:
            if day == state.selected_day:
                painter.setPen(QPen(style.highlight, 2))
                painter.drawRect(rect.adjusted(1, 1, -1, -2))

    def _draw_labels(
        self, painter: QPainter, style: PaintStyle, rows: list[TodoRow], *, area: QRect
    ) -> None:
        """1日ぶんの、23:59 期限のタスクのラベルを描く。入りきらない分は件数で出す。"""
        capacity = self._label_rows
        shown = rows if len(rows) <= capacity else rows[: capacity - 1]
        selected = self._state.selected if self._state is not None else None
        if len(rows) > len(shown):
            shown = keep_selected_visible(rows, shown, selected)
        y = area.top()
        for row in shown:
            rect = QRect(area.left() + 2, y, area.width() - 4, _LABEL_H - 1)
            self._draw_task_label(painter, style, row, rect=rect)
            y += _LABEL_H
        rest = len(rows) - len(shown)
        if rest > 0:
            painter.setPen(style.mix(0.45))
            painter.drawText(
                QRect(area.left() + 6, y, area.width() - 8, _LABEL_H - 1),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"他 {rest} 件",
            )


@dataclass(frozen=True, slots=True)
class _BlockLook:
    """時間軸の項目1件の見た目。

    Attributes:
        ref: 項目。
        fill: 塗りの色。
        pen: 文字の色。
        mark: タスクの左端に付ける分類の色。イベントなら None。
        title: 題名。
        span: 時刻の表示。
    """

    ref: ItemRef
    fill: QColor
    pen: QColor
    mark: QColor | None
    title: str
    span: str


class TimeGrid(CalendarCanvas):
    """週表示の時間軸(0:00〜24:00)。スクロール領域の中に置く。

    Args:
        parent: 親のウィジェット。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: WeekView | None = None
        self.setFixedHeight(HOURS * HOUR_H + 1)

    def set_data(self, view: WeekView, state: CanvasState) -> None:
        """表示する内容を受け取り、描き直す。

        Args:
            view: 週表示の内容。
            state: 分類、現在時刻、選択などの情報。
        """
        self._view = view
        self._state = state
        self.update()

    @staticmethod
    def y_of(moment: datetime) -> int:
        """その日の 0:00 からの位置を返す。

        Args:
            moment: 日時。

        Returns:
            縦の位置(ピクセル)。
        """
        midnight = datetime.combine(moment.date(), time(0, 0))
        return round((moment - midnight).total_seconds() / 3600 * HOUR_H)

    def block_rect(self, block: TimedBlock) -> QRect:
        """項目を描く矩形を返す。

        Args:
            block: 時間軸の項目。

        Returns:
            重なりの列を考えた矩形。
        """
        xs = column_edges(self.width())
        left, right = xs[block.col] + 2, xs[block.col + 1] - 3
        lane_w = (right - left) / block.lanes
        x = round(left + block.lane * lane_w)
        top = self.y_of(block.start)
        end = block.end
        bottom = HOURS * HOUR_H if end.date() > block.start.date() else self.y_of(end)
        return QRect(x, top + 1, max(8, round(lane_w) - 2), max(10, bottom - top - 2))

    @override
    def double_click_target(self, pos: QPoint) -> datetime | None:
        """クリックした30分の枠の終わりを期限にする(枠の中にタスクが置かれる)。

        Args:
            pos: ウィジェットの中の位置。

        Returns:
            期限の日時。日付の外なら None。
        """
        day = self.day_at(pos)
        if day is None:
            return None
        minutes = max(0, min(HOURS * 60 - 1, int(pos.y() / HOUR_H * 60)))
        slot = minutes // _SLOT_MINUTES * _SLOT_MINUTES
        due = datetime.combine(day, time(0, 0)) + timedelta(minutes=slot + _SLOT_MINUTES)
        return min(due, datetime.combine(day, time(23, 59)))

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        self._item_hits = []
        self._day_hits = []
        view, state = self._view, self._state
        if view is None or state is None:
            return
        painter = QPainter(self)
        style = paint_style(self)
        painter.fillRect(self.rect(), style.base)
        self._draw_days(painter, style, view)
        self._draw_hours(painter, style)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for block in view.blocks:
            self._draw_block(painter, style, block)
        today = state.now.date()
        if view.first_day <= today <= view.last_day:
            xs = column_edges(self.width())
            col = (today - view.first_day).days
            y = self.y_of(state.now)
            painter.setPen(QPen(COLOR_NOW, 2))
            painter.drawLine(xs[col], y, xs[col + 1], y)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(COLOR_NOW)
            painter.drawEllipse(QPoint(xs[col], y), 4, 4)
        painter.end()

    def _draw_days(self, painter: QPainter, style: PaintStyle, view: WeekView) -> None:
        """日ごとの列の背景を塗り、クリック判定に記録する。今日と選択中の日は色を変える。"""
        xs = column_edges(self.width())
        state = self._state
        today = state.now.date() if state is not None else None
        selected_day = state.selected_day if state is not None else None
        for col, cell in enumerate(view.days):
            rect = QRect(xs[col], 0, xs[col + 1] - xs[col], self.height())
            self._day_hits.append((rect, cell.day))
            if cell.day == today:
                tint = QColor(style.highlight)
                tint.setAlpha(28)
                painter.fillRect(rect, tint)
            elif cell.day == selected_day:
                painter.fillRect(rect, style.mix(0.04))

    def _draw_hours(self, painter: QPainter, style: PaintStyle) -> None:
        """時刻の罫線と目盛り、列の罫線を描く。"""
        width, height = self.width(), self.height()
        line = style.mix(0.16)
        painter.setFont(style.small)
        for hour in range(HOURS):
            y = hour * HOUR_H
            painter.setPen(QPen(line, 1))
            painter.drawLine(GUTTER, y, width, y)
            painter.setPen(QPen(style.mix(0.07), 1, Qt.PenStyle.DotLine))
            painter.drawLine(GUTTER, y + HOUR_H // 2, width, y + HOUR_H // 2)
            if hour:
                painter.setPen(style.mix(0.45))
                painter.drawText(
                    QRect(0, y - 8, GUTTER - 6, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{hour}:00",
                )
        painter.setPen(QPen(line, 1))
        for x in column_edges(width)[:-1]:
            painter.drawLine(x, 0, x, height)
        painter.drawLine(0, height - 1, width, height - 1)

    def _look(self, block: TimedBlock, style: PaintStyle) -> _BlockLook | None:
        """項目の色と文字を決める。

        Returns:
            項目の見た目。描けない項目なら None。
        """
        state = self._state
        if state is None:
            return None
        event, row = block.event, block.row
        if event is not None:
            color = state.category_color(event.category_id)
            if event.end_at <= state.now:
                color.setAlpha(ENDED_ALPHA)
            span = f"{event.start_at:%H:%M}〜{event.end_at:%H:%M}"
            ref = ItemRef(ItemKind.EVENT, event.saved_id)
            return _BlockLook(ref, color, _WHITE, None, event.name, span)
        if row is None or row.due is None:
            return None
        color = state.category_color(row.task.category_id)
        own_start = row.start is not None and row.start == block.start
        span = f"{block.start:%H:%M}〜{row.due:%H:%M}" if own_start else f"期限 {row.due:%H:%M}"
        title = ("✓ " if row.done else "") + row.task.title
        ref = ItemRef(ItemKind.TASK, row.task.saved_id)
        pen = COLOR_MUTED if row.done else style.text
        return _BlockLook(ref, mix_colors(style.base, color, 0.28), pen, color, title, span)

    def _draw_block(self, painter: QPainter, style: PaintStyle, block: TimedBlock) -> None:
        """時間軸の項目を1件描き、クリック判定に記録する。"""
        look = self._look(block, style)
        if look is None:
            return
        rect = self.block_rect(block)
        self._item_hits.append((rect, look.ref))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(look.fill)
        painter.drawRoundedRect(rect, 4, 4)
        if look.mark is not None:
            painter.setBrush(look.mark)  # タスクは左端に分類の色の印
            painter.drawRoundedRect(QRect(rect.left(), rect.top(), 4, rect.height()), 2, 2)
        if self._state is not None and look.ref == self._state.selected:
            painter.setPen(QPen(style.text, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(look.pen)
        inner = rect.adjusted(7, 1, -3, -1)
        line_h = style.metrics.height()
        elide = Qt.TextElideMode.ElideRight
        if inner.height() >= line_h * 2:
            # 2行以上入るなら、時刻と題名を分けて出す
            painter.drawText(
                QRect(inner.left(), inner.top(), inner.width(), line_h),
                Qt.AlignmentFlag.AlignLeft,
                style.metrics.elidedText(look.span, elide, inner.width()),
            )
            body = QRect(inner.left(), inner.top() + line_h, inner.width(), inner.height() - line_h)
            painter.drawText(
                body, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, look.title
            )
        else:
            painter.drawText(
                inner,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                style.metrics.elidedText(f"{look.title}  {look.span}", elide, inner.width()),
            )
        painter.restore()


class WeekPanel(QWidget):
    """週表示の全体。終日欄は固定し、時間軸だけを縦にスクロールする。

    Args:
        parent: 親のウィジェット。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = WeekHeader()
        self.grid = TimeGrid()
        self.scroll_area = QScrollArea()
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self.grid)
        self._view: WeekView | None = None

        # 終日欄の列を時間軸の列とそろえるため、右にスクロールバーの幅だけ余白をとる
        header_row = QHBoxLayout()
        bar_width = self.scroll_area.verticalScrollBar().sizeHint().width()
        header_row.setContentsMargins(0, 0, bar_width, 0)
        header_row.addWidget(self.header)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header_row)
        layout.addWidget(self.scroll_area, 1)
        self.setMinimumSize(420, 320)

    @property
    def canvases(self) -> tuple[CalendarCanvas, CalendarCanvas]:
        """クリックを受ける描画ビュー。

        Returns:
            (終日欄, 時間軸)。
        """
        return self.header, self.grid

    def set_data(self, view: WeekView, state: CanvasState) -> None:
        """表示する内容を終日欄と時間軸に渡す。

        Args:
            view: 週表示の内容。
            state: 分類、現在時刻、選択などの情報。
        """
        self._view = view
        self.header.set_data(view, state)
        self.grid.set_data(view, state)

    def scroll_to_hour(self, hour: float) -> None:
        """時間軸を、指定した時刻が上端に来るようにスクロールする。

        Args:
            hour: 時刻(時。小数で分も表す)。
        """
        self.scroll_area.verticalScrollBar().setValue(round(hour * HOUR_H))

    def ensure_visible(self, ref: ItemRef | None) -> bool:
        """時間軸に置かれた項目なら、見える位置までスクロールする。

        Args:
            ref: 項目。

        Returns:
            時間軸に置かれていれば True。
        """
        if ref is None or self._view is None:
            return False
        block = next((b for b in self._view.blocks if _block_ref(b) == ref), None)
        if block is None:
            return False
        # 端に半分だけ見えるより分かりやすいよう、見えていなければ縦の中央に出す
        rect = self.grid.block_rect(block)
        bar = self.scroll_area.verticalScrollBar()
        visible_h = self.scroll_area.viewport().height()
        if not (bar.value() <= rect.top() and rect.bottom() <= bar.value() + visible_h):
            bar.setValue(rect.center().y() - visible_h // 2)
        return True


def _block_ref(block: TimedBlock) -> ItemRef | None:
    """時間軸の項目が指す項目を返す。

    Returns:
        イベントかタスク。どちらでもなければ None。
    """
    if block.event is not None:
        return ItemRef(ItemKind.EVENT, block.event.saved_id)
    if block.row is not None:
        return ItemRef(ItemKind.TASK, block.row.task.saved_id)
    return None

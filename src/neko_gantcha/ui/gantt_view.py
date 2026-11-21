"""ガントチャート。左に 分類 > イベント > タスク のツリー、右に時間軸と帯を描く。

右側は QGraphicsView。日付の見出しと左のツリーは別ウィジェットにして、
スクロール量だけを右側に合わせている(縦横に動かしても見出しが固定されるようにするため)。
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPalette,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.gantt import DragMode, RowKind, drag_modes, dragged_range, row_index
from neko_gantcha.domain.models import EventRelation
from neko_gantcha.services.store import VIEW_CALENDAR, VIEW_TODO, ItemKind, ItemRef
from neko_gantcha.ui.calendar_canvas import SATURDAY_COL, weekday_color
from neko_gantcha.ui.dialogs.dependency_dialog import add_dependency_interactive
from neko_gantcha.ui.dialogs.project_dialog import edit_project
from neko_gantcha.ui.widgets import format_datetime, mix_colors

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import (
        QAction,
        QContextMenuEvent,
        QKeyEvent,
        QMouseEvent,
        QPaintEvent,
        QShowEvent,
        QWheelEvent,
    )
    from PySide6.QtWidgets import QStyleOptionGraphicsItem

    from neko_gantcha.domain.gantt import GanttData, GanttLink, GanttRow
    from neko_gantcha.domain.models import Category, Dependency
    from neko_gantcha.services.store import AppStore

ROW_H = 28
HEADER_H = 44
_EVENT_BAR_H = 16
_PROJECT_BAR_H = 12
_PROJECT_TEXT_W = 56  # 帯の右に出す完了率の幅
_EDGE = 6  # 帯の端をつかんで伸縮できる幅(ピクセル)
_TASK_BAR_H = 8
_PX_PER_DAY = {"day": 28.0, "week": 8.0}
_PREP_ALPHA = 90  # 準備(イベントまで)と期限なしのタスクの帯の不透明度
_BAR_ALPHA = 150  # 進捗のあるタスクの帯の、未達成ぶんの不透明度
_DONE_EVENT_ALPHA = 140
_NO_CATEGORY_COLOR = "#888"

_COLOR_TODAY = QColor("#E53935")
_COLOR_DONE = QColor("#9E9E9E")
_WHITE = QColor("#FFFFFF")


def _ref_of(row: GanttRow) -> ItemRef | None:
    """行が指す項目を返す。

    Returns:
        タスク、イベント、プロジェクト。見出しの行なら None。
    """
    ref = None
    if row.kind is RowKind.TASK and row.task is not None:
        ref = ItemRef(ItemKind.TASK, row.task.saved_id)
    elif row.kind is RowKind.EVENT and row.event is not None:
        ref = ItemRef(ItemKind.EVENT, row.event.saved_id)
    elif row.kind is RowKind.PROJECT and row.project is not None:
        ref = ItemRef(ItemKind.PROJECT, row.project.saved_id)
    return ref


def _small_font(painter: QPainter) -> None:
    """描画の字を一回り小さくする。"""
    font = painter.font()
    font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
    painter.setFont(font)


def _run_menu(menu: QMenu, actions: dict[QAction, Callable[[], object]], pos: QPoint) -> None:
    """メニューを出し、選ばれた項目の処理を行う。何も選ばなければ何もしない。"""
    chosen = menu.exec(pos)
    action = actions.get(chosen)
    if action is not None:
        action()


class _Geometry:
    """日時と横位置(ピクセル)の対応。

    Args:
        start: 時間軸の左端の日。
        days: 時間軸の日数。
        px_per_day: 1日の幅(ピクセル)。
        first_weekday: 週の最初の曜日(0=月曜 … 6=日曜)。週スケールの区切りに使う。
    """

    def __init__(
        self, start: date, days: int, px_per_day: float, *, first_weekday: int = 0
    ) -> None:
        self.start = start
        self.origin = datetime.combine(start, time.min)
        self.days = days
        self.px_per_day = px_per_day
        self.first_weekday = first_weekday

    @property
    def width(self) -> float:
        """時間軸の全体の幅。

        Returns:
            幅(ピクセル)。
        """
        return self.days * self.px_per_day

    def x(self, moment: datetime) -> float:
        """日時の横位置を返す。

        Returns:
            左端からの位置(ピクセル)。
        """
        return (moment - self.origin).total_seconds() / 86400 * self.px_per_day

    def day_x(self, index: int) -> float:
        """左端から index 日目の始まりの横位置を返す。

        Returns:
            左端からの位置(ピクセル)。
        """
        return index * self.px_per_day

    def day(self, index: int) -> date:
        """左端から index 日目の日付を返す。

        Returns:
            日付。
        """
        return self.start + timedelta(days=index)

    def date_at(self, x: float) -> datetime:
        """横位置の日時を返す。

        Returns:
            日時。
        """
        return self.origin + timedelta(days=x / self.px_per_day)


def _bar_tooltip(row: GanttRow) -> str:
    """帯のツールチップを作る。

    Returns:
        名前、期間、完了率などを並べた文字列。
    """
    tip = [row.label, f"{format_datetime(row.start)} 〜 {format_datetime(row.end)}"]
    if row.kind is RowKind.PROJECT:
        if row.open_ended:
            tip[1] = f"{format_datetime(row.start)} 〜(目標日なし)"
        else:
            tip[1] = f"{format_datetime(row.start)} 〜 目標 {format_datetime(row.end)}"
        if row.progress is not None:
            tip.append(f"完了率 {round(row.progress * 100)}%")
    elif row.open_ended:
        tip[1] = f"開始: {format_datetime(row.start)}(期限なし)"
    elif row.relation is EventRelation.BEFORE:
        tip.append("イベント開始までに終える")
    return "\n".join(tip)


class _Bar(QGraphicsItem):
    """1行ぶんの帯。イベントは太く、タスクは細く描く。

    Args:
        index: 行番号。
        row: ガントの行。
        span: 帯の左端と右端の横位置。
        color: 帯の色。
    """

    def __init__(
        self, index: int, row: GanttRow, span: tuple[float, float], *, color: QColor
    ) -> None:
        super().__init__()
        self.index = index
        self.row = row
        self._color = color
        self.selected = False
        self.links: list[_Link] = []  # この帯につながる矢印(帯を動かすと引き直す)
        left, right = span
        height = {RowKind.EVENT: _EVENT_BAR_H, RowKind.PROJECT: _PROJECT_BAR_H}.get(
            row.kind, _TASK_BAR_H
        )
        top = index * ROW_H + (ROW_H - height) / 2
        self.rect = QRectF(left, top, max(4.0, right - left), height)
        self.setToolTip(_bar_tooltip(row))

    def set_rect(self, rect: QRectF) -> None:
        """ドラッグ中の仮の位置に描き直す。つながる矢印も引き直す。

        Args:
            rect: 新しい矩形。
        """
        self.prepareGeometryChange()
        for link in self.links:
            link.prepareGeometryChange()
        self.rect = QRectF(rect)
        self.update()
        for link in self.links:
            link.update()

    @override
    def boundingRect(self) -> QRectF:
        extra = _PROJECT_TEXT_W if self.row.kind is RowKind.PROJECT else 0
        return self.rect.adjusted(-6, -6, 6 + extra, 6)

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        row = self.row
        color = QColor(_COLOR_DONE if row.done else self._color)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if row.kind is RowKind.EVENT:
            self._paint_event(painter, color)
        elif row.kind is RowKind.PROJECT:
            self._paint_project(painter, color)
        else:
            self._paint_task(painter, color)
        if self.selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.scene().palette().color(QPalette.ColorRole.Text), 2))
            painter.drawRoundedRect(self.rect.adjusted(-2, -2, 2, 2), 5, 5)

    def _paint_event(self, painter: QPainter, color: QColor) -> None:
        """イベントの帯。太い帯に名前を白で書く。"""
        rect = self.rect
        if self.row.done:
            color.setAlpha(_DONE_EVENT_ALPHA)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(_WHITE)
        _small_font(painter)
        text_rect = rect.adjusted(6, 0, -4, 0)
        label = QFontMetrics(painter.font()).elidedText(
            self.row.label, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label
        )

    def _paint_task(self, painter: QPainter, color: QColor) -> None:
        """タスクの帯。準備(イベントまで)と期限なしは薄く、進捗があれば達成ぶんを濃く塗る。"""
        row, rect = self.row, self.rect
        faint = QColor(color)
        prep = row.relation is EventRelation.BEFORE or row.open_ended
        faint.setAlpha(_PREP_ALPHA if prep else _BAR_ALPHA)
        painter.setBrush(faint if row.progress is not None or prep else color)
        painter.drawRoundedRect(rect, 3, 3)
        if row.progress is not None and row.progress > 0:
            filled = QRectF(rect)
            filled.setWidth(rect.width() * row.progress)
            painter.setBrush(color)
            painter.drawRoundedRect(filled, 3, 3)
        if row.relation is EventRelation.BEFORE:
            # 帯の終わり(=イベント開始)に区切りを入れる
            painter.setBrush(color)
            painter.drawRect(QRectF(rect.right() - 2, rect.top() - 3, 2, rect.height() + 6))

    def _paint_project(self, painter: QPainter, color: QColor) -> None:
        """プロジェクトの帯。枠つきの薄い帯に完了率ぶんを塗り、締め切りに菱形を置く。"""
        row, rect = self.row, self.rect
        faint = QColor(color)
        faint.setAlpha(60)
        painter.setPen(QPen(color, 1))
        painter.setBrush(faint)
        painter.drawRoundedRect(rect, 3, 3)
        if row.progress is not None and row.progress > 0:
            filled = QRectF(rect)
            filled.setWidth(rect.width() * row.progress)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(filled, 3, 3)
        if not row.open_ended:
            # 目標日(締め切り)の印
            center = QPointF(rect.right(), rect.center().y())
            size = rect.height() / 2 + 2
            diamond = QPolygonF(
                [
                    center + QPointF(0, -size),
                    center + QPointF(size, 0),
                    center + QPointF(0, size),
                    center + QPointF(-size, 0),
                ]
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(diamond)
        if row.progress is not None:
            painter.setPen(QColor(self.scene().palette().color(QPalette.ColorRole.Text)))
            _small_font(painter)
            text_rect = QRectF(rect.right() + 10, rect.top() - 4, _PROJECT_TEXT_W - 10, 20)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{round(row.progress * 100)}%",
            )


_LINK_GAP = 7.0  # 矢印が帯の端から出て折れ曲がるまでの距離
_ARROW = 5.0  # 矢じりの大きさ


class _Link(QGraphicsItem):
    """前後関係の矢印。先のタスクの帯の右端から、後のタスクの帯の左端へ折れ線で引く。

    帯の位置はドラッグ中に変わるので、描くたびに両端の帯の矩形から道筋を作り直す。

    Args:
        link: 前後関係の矢印の情報。
        source: 先のタスクの帯。
        target: 後のタスクの帯。
    """

    def __init__(self, link: GanttLink, source: _Bar, target: _Bar) -> None:
        super().__init__()
        self.link = link
        self.source = source
        self.target = target
        self.selected = False
        self.setZValue(1)  # 帯より手前(矢じりが帯に隠れないように)
        before, after = source.row, target.row
        tip = f"「{before.label}」を終えてから「{after.label}」"
        if link.conflict:
            tip += "\n後のタスクが、前のタスクの終わりより前に始まっています"
        self.setToolTip(tip + "\n右クリックで外せます")

    def path(self) -> QPainterPath:
        """矢印の道筋を作る。

        Returns:
            先の帯の右端から後の帯までの折れ線。
        """
        start = QPointF(self.source.rect.right(), self.source.rect.center().y())
        end = QPointF(self.target.rect.left(), self.target.rect.center().y())
        turn_x = start.x() + _LINK_GAP
        path = QPainterPath(start)
        target = self.target.rect
        if not self.target.row.has_start and target.left() <= turn_x <= target.right():
            # 期限だけのタスクの帯の始まりは仮のものなので、前のタスクの終わりの位置で
            # 帯の上か下から縦に入れる
            down = target.center().y() >= start.y()
            path.lineTo(turn_x, start.y())
            path.lineTo(turn_x, target.top() - 1 if down else target.bottom() + 1)
            return path
        if end.x() - _LINK_GAP >= turn_x:
            # 後のタスクが右にあるときは、右へ出て、縦に移り、右へ入る
            path.lineTo(turn_x, start.y())
            path.lineTo(turn_x, end.y())
        else:
            # 後のタスクが左に戻るときは、行の境目を通って回り込む
            down = end.y() >= start.y()
            between = start.y() + (ROW_H / 2 if down else -ROW_H / 2)
            path.lineTo(turn_x, start.y())
            path.lineTo(turn_x, between)
            path.lineTo(end.x() - _LINK_GAP, between)
            path.lineTo(end.x() - _LINK_GAP, end.y())
        path.lineTo(end)
        return path

    @override
    def boundingRect(self) -> QRectF:
        return self.path().boundingRect().adjusted(-_ARROW - 2, -_ARROW - 2, _ARROW + 2, _ARROW + 2)

    @override
    def shape(self) -> QPainterPath:
        # クリックしやすいよう、線の周りを太めに当たり判定にする
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(self.path())

    def color(self) -> QColor:
        """矢印の色を返す。

        Returns:
            食い違いがあれば赤、先のタスクが完了していれば薄い色。
        """
        palette = self.scene().palette()
        if self.link.conflict:
            return QColor(_COLOR_TODAY)
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        return mix_colors(base, text, 0.35 if self.link.done else 0.6)

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        color = self.color()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 2.5 if self.selected else 1.4)
        if self.link.done:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = self.path()
        painter.drawPath(path)
        # 矢じりは最後の線の向きに合わせる(右向きか、上下向き)
        end = path.currentPosition()
        last = path.elementAt(path.elementCount() - 2)
        dx, dy = end.x() - last.x, end.y() - last.y
        if abs(dy) > abs(dx):
            sign = 1 if dy > 0 else -1
            back = QPointF(0, -sign * (_ARROW + 2))
            side = QPointF(_ARROW - 1, 0)
        else:
            back = QPointF(-_ARROW - 2, 0)
            side = QPointF(0, _ARROW - 1)
        head = QPolygonF([end, end + back - side, end + back + side])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(head)


@dataclass(frozen=True, slots=True)
class TimelineState:
    """時間軸に渡す、表示内容のほかの情報。

    Attributes:
        scale: 時間軸の目盛り("day" か "week")。
        categories: 分類 id → 分類。
        now: 現在の日時。
        selected_row: 選択中の行の番号。なければ -1。
        first_weekday: 週の最初の曜日(0=月曜 … 6=日曜)。
    """

    scale: str
    categories: Mapping[int, Category]
    now: datetime
    selected_row: int
    first_weekday: int = 0


def _mode_at(x: float, rect: QRectF, modes: frozenset[DragMode], *, edge: float) -> DragMode | None:
    """帯のどこをつかんだかで、ドラッグの種類を決める。

    Returns:
        両端なら伸縮、中ほどなら移動。どれも許されなければ None。
    """
    if DragMode.END in modes and x >= rect.right() - edge:
        return DragMode.END
    if DragMode.START in modes and x <= rect.left() + edge:
        return DragMode.START
    if DragMode.MOVE in modes and rect.left() <= x <= rect.right():
        return DragMode.MOVE
    return None


class TimelineView(QGraphicsView):
    """時間軸と帯。背景の罫線と今日の線は drawBackground / drawForeground で描く。

    Args:
        parent: 親のウィジェット。
    """

    row_clicked = Signal(int)
    context_requested = Signal(int, object)  # 行番号, QPoint(グローバル座標)
    drag_finished = Signal(int, str, int)  # 行番号, DragMode の値, ずらした日数
    link_context_requested = Signal(object, object)  # Dependency, QPoint(グローバル座標)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setMouseTracking(True)
        # ドラッグ中の状態: (行番号, 種類, 押した位置の x, 元の帯の矩形)
        self._drag: tuple[int, DragMode, float, QRectF] | None = None
        self._drag_days = 0
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.geometry_: _Geometry | None = None
        self._rows: list[GanttRow] = []
        self._now: datetime | None = None
        self._selected_row = -1
        self._scale = "day"
        self.bars: dict[int, _Bar] = {}
        self.links: list[_Link] = []

    def set_data(self, data: GanttData, state: TimelineState) -> None:
        """表示する内容を受け取り、帯と矢印を作り直す。

        Args:
            data: ガントの内容。
            state: 目盛り、分類、現在時刻、選択中の行。
        """
        self._rows = data.rows
        self._now = state.now
        self._scale = state.scale
        self._drag = None  # 作り直したら、ドラッグ中の帯はもうない
        self._drag_days = 0
        self._selected_row = state.selected_row
        geometry = _Geometry(
            data.start, data.days, _PX_PER_DAY[state.scale], first_weekday=state.first_weekday
        )
        self.geometry_ = geometry
        scene = self.scene()
        scene.clear()
        scene.setPalette(self.palette())
        self.bars = {}
        for index, row in enumerate(data.rows):
            if row.start is None or row.end is None:
                continue
            category = state.categories.get(row.category_id)
            color = QColor(category.color if category is not None else _NO_CATEGORY_COLOR)
            span = (geometry.x(row.start), geometry.x(row.end))
            bar = _Bar(index, row, span, color=color)
            bar.selected = index == state.selected_row
            scene.addItem(bar)
            self.bars[index] = bar
        self.links = []
        for link in data.links:
            source, target = self.bars.get(link.from_row), self.bars.get(link.to_row)
            if source is None or target is None:
                continue
            item = _Link(link, source, target)
            item.selected = state.selected_row in {link.from_row, link.to_row}
            source.links.append(item)
            target.links.append(item)
            scene.addItem(item)
            self.links.append(item)
        scene.setSceneRect(0, 0, geometry.width, max(1, len(data.rows)) * ROW_H)
        self.viewport().update()

    def set_selected_row(self, index: int) -> None:
        """選択中の行を変え、帯と矢印を強調し直す。

        Args:
            index: 行番号。なければ -1。
        """
        self._selected_row = index
        for bar_index, bar in self.bars.items():
            bar.selected = bar_index == index
        for link in self.links:
            # 選んだタスクにつながる矢印を太くする
            link.selected = index in {link.link.from_row, link.link.to_row}
        self.scene().update()
        self.viewport().update()

    def row_at(self, viewport_pos: QPoint) -> int:
        """位置にある行の番号を返す。

        Args:
            viewport_pos: ビューポートの中の位置。

        Returns:
            行番号。行の外なら -1。
        """
        y = self.mapToScene(viewport_pos).y()
        index = int(y // ROW_H)
        return index if 0 <= index < len(self._rows) and y >= 0 else -1

    def drag_hit(self, viewport_pos: QPoint) -> tuple[int, DragMode] | None:
        """その位置で始められるドラッグを返す。帯の両端は伸縮、中ほどは移動。

        Args:
            viewport_pos: ビューポートの中の位置。

        Returns:
            (行番号, ドラッグの種類)。始められなければ None。
        """
        index = self.row_at(viewport_pos)
        bar = self.bars.get(index)
        if bar is None:
            return None
        modes = drag_modes(bar.row)
        point = self.mapToScene(viewport_pos)
        rect = bar.rect
        if not modes or not rect.adjusted(-_EDGE, -4, _EDGE, 4).contains(point):
            return None
        # 短い帯でも移動できるよう、端の判定は帯の幅の 1/3 までにする
        mode = _mode_at(point.x(), rect, modes, edge=min(_EDGE, rect.width() / 3))
        return None if mode is None else (index, mode)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.row_at(pos)
            if index >= 0:
                self.row_clicked.emit(index)
            hit = self.drag_hit(pos)
            if hit is not None and hit[0] in self.bars:
                bar = self.bars[hit[0]]
                self._drag = (hit[0], hit[1], self.mapToScene(pos).x(), QRectF(bar.rect))
                self._drag_days = 0
                event.accept()
                return
        super().mousePressEvent(event)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._drag is None:
            hit = self.drag_hit(pos)
            if hit is None:
                self.viewport().unsetCursor()
            elif hit[1] is DragMode.MOVE:
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            super().mouseMoveEvent(event)
            return
        index, mode, press_x, original = self._drag
        bar = self.bars.get(index)
        if bar is None or self.geometry_ is None:
            return
        px = self.geometry_.px_per_day
        days = round((self.mapToScene(pos).x() - press_x) / px)
        if days != self._drag_days:
            self._drag_days = days
            bar.set_rect(_preview(original, bar.row, mode, shift=days * px))
            QToolTip.showText(event.globalPosition().toPoint(), _drag_text(bar.row, mode, days))
        event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            index, mode, _, _ = self._drag
            days = self._drag_days
            self._drag = None
            self._drag_days = 0
            QToolTip.hideText()
            if days:
                self.drag_finished.emit(index, mode.value, days)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._drag is not None:
            self.cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def drag_days(self) -> int:
        """ドラッグ中の帯をずらしている日数。

        Returns:
            日数。ドラッグしていなければ 0。
        """
        return self._drag_days

    def cancel_drag(self) -> None:
        """ドラッグを取り消して帯を元の位置に戻す。"""
        if self._drag is None:
            return
        index, _, _, original = self._drag
        self._drag = None
        self._drag_days = 0
        QToolTip.hideText()
        bar = self.bars.get(index)
        if bar is not None:
            bar.set_rect(original)

    def link_at(self, viewport_pos: QPoint) -> _Link | None:
        """その位置にある前後関係の矢印を返す。

        Args:
            viewport_pos: ビューポートの中の位置。

        Returns:
            矢印。なければ None。
        """
        for item in self.items(viewport_pos):
            if isinstance(item, _Link):
                return item
        return None

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        link = self.link_at(event.pos())
        if link is not None:
            self.link_context_requested.emit(link.link.dependency, event.globalPos())
            return
        index = self.row_at(event.pos())
        if index >= 0:
            self.row_clicked.emit(index)
            self.context_requested.emit(index, event.globalPos())

    @override
    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        area = QRectF(rect)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(area, base)
        geometry = self.geometry_
        if geometry is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        first_row = max(0, int(area.top() // ROW_H))
        last_row = min(len(self._rows) - 1, int(area.bottom() // ROW_H))
        # 行の背景(分類の見出し行と、選択中の行)
        tint = QColor(palette.color(QPalette.ColorRole.Highlight))
        tint.setAlpha(60)
        for index in range(first_row, last_row + 1):
            row_rect = QRectF(area.left(), index * ROW_H, area.width(), ROW_H)
            if self._rows[index].kind is RowKind.CATEGORY:
                painter.fillRect(row_rect, mix_colors(base, text, 0.08))
            if index == self._selected_row:
                painter.fillRect(row_rect, tint)
        self._draw_day_lines(painter, area, geometry)
        # 横線
        painter.setPen(QPen(mix_colors(base, text, 0.12), 1))
        for index in range(first_row, last_row + 2):
            y = index * ROW_H
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

    def _draw_day_lines(self, painter: QPainter, area: QRectF, geometry: _Geometry) -> None:
        """週末の背景と縦線を描く。日スケールは毎日、週スケールは週の始まりごと。月初は濃く。"""
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        first_day = max(0, int(area.left() // geometry.px_per_day))
        last_day = min(geometry.days, int(area.right() // geometry.px_per_day) + 1)
        bottom = max(area.bottom(), len(self._rows) * ROW_H)
        weekend = mix_colors(base, text, 0.05)
        for index in range(first_day, min(last_day + 1, geometry.days)):
            if geometry.day(index).weekday() >= SATURDAY_COL:
                x = geometry.day_x(index)
                painter.fillRect(QRectF(x, area.top(), geometry.px_per_day, area.height()), weekend)
        line = QPen(mix_colors(base, text, 0.12), 1)
        strong = QPen(mix_colors(base, text, 0.3), 1)
        for index in range(first_day, last_day + 1):
            day = geometry.day(index)
            if day.day == 1:
                painter.setPen(strong)
            elif self._scale == "day" or day.weekday() == geometry.first_weekday:
                painter.setPen(line)
            else:
                continue
            x = geometry.day_x(index)
            painter.drawLine(QPointF(x, area.top()), QPointF(x, bottom))

    @override
    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        if self.geometry_ is None or self._now is None:
            return
        area = QRectF(rect)
        x = self.geometry_.x(self._now)
        if area.left() - 2 <= x <= area.right() + 2:
            painter.setPen(QPen(_COLOR_TODAY, 2))
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))


def _preview(original: QRectF, row: GanttRow, mode: DragMode, *, shift: float) -> QRectF:
    """ドラッグ中の帯の仮の矩形を作る。

    Returns:
        仮の矩形。開始が終了を追い越さないよう止める(プロジェクトの目標日は除く)。
    """
    preview = QRectF(original)
    if mode is DragMode.MOVE:
        preview.translate(shift, 0)
    elif mode is DragMode.START:
        preview.setLeft(min(original.left() + shift, original.right()))
    elif row.kind is RowKind.PROJECT:
        preview.setRight(original.right() + shift)  # 目標日は開始に縛られない
        preview = preview.normalized()
    else:
        preview.setRight(max(original.right() + shift, original.left()))
    return preview


def _drag_text(row: GanttRow, mode: DragMode, days: int) -> str:
    """ドラッグ中に出す、新しい日程の説明を作る。

    Returns:
        新しい開始と終了(プロジェクトは目標日)とずらした日数。
    """
    start, end = row.start, row.end
    if start is None or end is None:
        return ""
    sign = "+" if days > 0 else ""
    if row.kind is RowKind.PROJECT:
        target = end + timedelta(days=days)
        return f"目標日: {target:%Y/%m/%d}({sign}{days}日)"
    new_start, new_end = dragged_range(
        (start, end), (start, end), mode=mode, delta=timedelta(days=days)
    )
    return f"{format_datetime(new_start)} 〜 {format_datetime(new_end)}({sign}{days}日)"


class TimelineHeader(QWidget):
    """時間軸の見出し(上段: 年月、下段: 日)。横スクロール量を TimelineView に合わせる。

    Args:
        parent: 親のウィジェット。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(HEADER_H)
        self._geometry: _Geometry | None = None
        self._scale = "day"
        self._offset = 0
        self._today: date | None = None

    def set_data(self, geometry: _Geometry, scale: str, today: date) -> None:
        """時間軸の範囲と目盛りを受け取り、描き直す。

        Args:
            geometry: 日時と横位置の対応。
            scale: 目盛り("day" か "week")。
            today: 今日の日付。
        """
        self._geometry = geometry
        self._scale = scale
        self._today = today
        self.update()

    def set_offset(self, offset: int) -> None:
        """横スクロール量を合わせて描き直す。

        Args:
            offset: 横スクロール量(ピクセル)。
        """
        self._offset = offset
        self.update()

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        line = QPen(mix_colors(base, text, 0.18), 1)
        painter.fillRect(self.rect(), mix_colors(base, text, 0.06))
        geometry = self._geometry
        if geometry is None:
            return
        first = max(0, int(self._offset // geometry.px_per_day))
        last = min(geometry.days - 1, int((self._offset + self.width()) // geometry.px_per_day) + 1)
        self._draw_months(painter, geometry, (first, last))
        self._draw_days(painter, geometry, (first, last))
        half = HEADER_H // 2
        painter.setPen(line)
        painter.drawLine(0, half, self.width(), half)
        painter.drawLine(0, HEADER_H - 1, self.width(), HEADER_H - 1)
        painter.end()

    def _draw_months(self, painter: QPainter, geometry: _Geometry, shown: tuple[int, int]) -> None:
        """上段の年月を描く。左端に隠れた月も、見えている範囲の左に寄せて表示する。"""
        palette = self.palette()
        text = palette.color(QPalette.ColorRole.Text)
        line = QPen(mix_colors(palette.color(QPalette.ColorRole.Base), text, 0.18), 1)
        half = HEADER_H // 2
        painter.setFont(self.font())
        index = shown[0]
        while index <= shown[1]:
            day = geometry.day(index)
            month_start = index - (day.day - 1)
            next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
            next_index = (next_month - geometry.start).days
            left = max(geometry.day_x(month_start) - self._offset, 0)
            width = geometry.day_x(next_index) - self._offset - left
            painter.setPen(text)
            painter.drawText(
                QRect(int(left) + 6, 0, max(0, int(width) - 8), half),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{day.year}年{day.month}月",
            )
            if day.day == 1 or index == shown[0]:
                painter.setPen(line)
                x = int(geometry.day_x(month_start) - self._offset)
                painter.drawLine(x, 0, x, half)
            index = next_index

    def _draw_days(self, painter: QPainter, geometry: _Geometry, shown: tuple[int, int]) -> None:
        """下段を描く。日スケールは毎日、週スケールは週の始まりの日付。"""
        palette = self.palette()
        text = palette.color(QPalette.ColorRole.Text)
        line = QPen(mix_colors(palette.color(QPalette.ColorRole.Base), text, 0.18), 1)
        half = HEADER_H // 2
        small = self.font()
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        painter.setFont(small)
        for index in range(shown[0], shown[1] + 1):
            day = geometry.day(index)
            x = int(geometry.day_x(index) - self._offset)
            if self._scale == "day":
                rect = QRect(x, half, int(geometry.px_per_day), half)
                if day == self._today:
                    painter.fillRect(rect.adjusted(2, 2, -2, -2), _COLOR_TODAY)
                    painter.setPen(_WHITE)
                else:
                    painter.setPen(weekday_color(day.weekday(), text))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day))
            elif day.weekday() == geometry.first_weekday:
                painter.setPen(line)
                painter.drawLine(x, half, x, HEADER_H)
                painter.setPen(text)
                rect = QRect(x + 4, half, int(geometry.px_per_day * 7) - 4, half)
                painter.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{day.month}/{day.day}",
                )


class LabelPane(QWidget):
    """左のツリー。行の高さと縦スクロール量を TimelineView に合わせて描く。

    Args:
        parent: 親のウィジェット。
    """

    row_clicked = Signal(int)
    context_requested = Signal(int, object)
    wheel_scrolled = Signal(int)  # 縦スクロールの移動量(ピクセル)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[GanttRow] = []
        self._categories: Mapping[int, Category] = {}
        self._offset = 0
        self._selected_row = -1
        self.setMinimumWidth(140)

    def set_data(
        self, rows: list[GanttRow], categories: Mapping[int, Category], selected_row: int
    ) -> None:
        """行を受け取り、描き直す。

        Args:
            rows: ガントの行。
            categories: 分類 id → 分類。
            selected_row: 選択中の行の番号。なければ -1。
        """
        self._rows = rows
        self._categories = categories
        self._selected_row = selected_row
        self.update()

    def set_selected_row(self, index: int) -> None:
        """選択中の行を変えて描き直す。

        Args:
            index: 行番号。なければ -1。
        """
        self._selected_row = index
        self.update()

    def set_offset(self, offset: int) -> None:
        """縦スクロール量を合わせて描き直す。

        Args:
            offset: 縦スクロール量(ピクセル)。
        """
        self._offset = offset
        self.update()

    def row_at(self, pos: QPoint) -> int:
        """位置にある行の番号を返す。

        Args:
            pos: ウィジェットの中の位置。

        Returns:
            行番号。行の外なら -1。
        """
        index = (pos.y() + self._offset) // ROW_H
        return index if 0 <= index < len(self._rows) else -1

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.row_at(event.position().toPoint())
            if index >= 0:
                self.row_clicked.emit(index)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        index = self.row_at(event.pos())
        if index >= 0:
            self.row_clicked.emit(index)
            self.context_requested.emit(index, event.globalPos())

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        self.wheel_scrolled.emit(-event.angleDelta().y())

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(self.rect(), base)
        first = max(0, self._offset // ROW_H)
        last = min(len(self._rows) - 1, (self._offset + self.height()) // ROW_H)
        line = QPen(mix_colors(base, text, 0.12), 1)
        for index in range(first, last + 1):
            self._draw_row(painter, index)
            # 罫線は行ごとに引く。次の行の背景が上に重なる
            bottom = (index + 1) * ROW_H - self._offset
            painter.setPen(line)
            painter.drawLine(0, bottom, self.width(), bottom)
        painter.end()

    def _draw_row_background(self, painter: QPainter, index: int) -> None:
        """1行ぶんの背景を塗る。分類の見出し行と選択中の行は色を変える。"""
        palette = self.palette()
        rect = QRect(0, index * ROW_H - self._offset, self.width(), ROW_H)
        if self._rows[index].kind is RowKind.CATEGORY:
            base = palette.color(QPalette.ColorRole.Base)
            painter.fillRect(rect, mix_colors(base, palette.color(QPalette.ColorRole.Text), 0.08))
        if index == self._selected_row:
            tint = QColor(palette.color(QPalette.ColorRole.Highlight))
            tint.setAlpha(60)
            painter.fillRect(rect, tint)

    def _draw_row(self, painter: QPainter, index: int) -> None:
        """1行ぶんの背景、分類の色の点、名前を描く。"""
        self._draw_row_background(painter, index)
        text = self.palette().color(QPalette.ColorRole.Text)
        row = self._rows[index]
        top = index * ROW_H - self._offset
        left = 8 + row.depth * 16
        if row.kind is RowKind.CATEGORY:
            category = self._categories.get(row.category_id)
            color = category.color if category is not None else _NO_CATEGORY_COLOR
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(color)))
            painter.drawEllipse(left, top + ROW_H // 2 - 5, 10, 10)
            left += 16
        font = self.font()
        font.setBold(row.kind in {RowKind.CATEGORY, RowKind.EVENT, RowKind.PROJECT})
        painter.setFont(font)
        painter.setPen(_COLOR_DONE if row.done or row.kind is RowKind.GROUP else text)
        label = ("✓ " if row.done and row.kind is RowKind.TASK else "") + row.label
        text_rect = QRect(left, top, self.width() - left - 6, ROW_H)
        elided = QFontMetrics(font).elidedText(
            label, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided
        )


class GanttView(QWidget):
    """ガントのビュー。上に目盛りの切り替え、下に左のツリーと右の時間軸を並べる。

    Args:
        store: データ操作の窓口。
        parent: 親のウィジェット。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._scale = "day"
        self._data: GanttData | None = None
        self._refresh_pending = False
        self._selecting = False
        self._positioned = False  # 初回表示で今日の位置へスクロールしたか

        self.labels = LabelPane()
        self.timeline = TimelineView()
        self.header = TimelineHeader()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._build_splitter(), 1)

        self.timeline.horizontalScrollBar().valueChanged.connect(self.header.set_offset)
        self.timeline.verticalScrollBar().valueChanged.connect(self.labels.set_offset)
        self.labels.wheel_scrolled.connect(self._scroll_vertically)
        self.timeline.row_clicked.connect(self._on_row_clicked)
        self.labels.row_clicked.connect(self._on_row_clicked)
        self.timeline.context_requested.connect(self._show_menu)
        self.timeline.drag_finished.connect(self._apply_drag)
        self.timeline.link_context_requested.connect(self._show_link_menu)
        self.labels.context_requested.connect(self._show_menu)

        for signal in (
            store.tasks_changed,
            store.events_changed,
            store.categories_changed,
            store.projects_changed,
            store.filter_changed,
            store.periods_changed,
            store.minute_ticked,
            store.week_start_changed,
        ):
            signal.connect(self._schedule_refresh)
        store.selection_changed.connect(self._on_selection_changed)
        self.refresh()

    def _build_toolbar(self) -> QHBoxLayout:
        """上の操作欄(目盛りの切り替えと今日)を作る。

        Returns:
            操作欄のレイアウト。
        """
        day_button = QToolButton()
        day_button.setText("日")
        day_button.setCheckable(True)
        day_button.setChecked(True)
        week_button = QToolButton()
        week_button.setText("週")
        week_button.setCheckable(True)
        self._scale_buttons = QButtonGroup(self)
        self._scale_buttons.addButton(day_button)
        self._scale_buttons.addButton(week_button)
        today_button = QPushButton("今日")
        self._empty_label = QLabel(
            "日付のあるイベントやタスクがありません。"
            "恒常タスクと繰り返しタスクはガントには表示されません。"
        )
        self._empty_label.setObjectName("info")
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("スケール"))
        toolbar.addWidget(day_button)
        toolbar.addWidget(week_button)
        toolbar.addSpacing(8)
        toolbar.addWidget(today_button)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._empty_label)
        toolbar.addStretch(1)  # 空の案内を隠したときにボタンが横に伸びないようにする
        day_button.clicked.connect(lambda: self.set_scale("day"))
        week_button.clicked.connect(lambda: self.set_scale("week"))
        today_button.clicked.connect(self.scroll_to_today)
        return toolbar

    def _build_splitter(self) -> QSplitter:
        """左のツリーと右の時間軸を並べる。

        Returns:
            左右を分ける区切り。
        """
        corner = QLabel("  項目")
        corner.setObjectName("ganttCorner")
        corner.setFixedHeight(HEADER_H)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(corner)
        left_layout.addWidget(self.labels, 1)
        # 右側の横スクロールバーのぶんだけ下を空け、行の位置を揃える
        left_layout.addSpacing(self.timeline.horizontalScrollBar().sizeHint().height())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.header)
        right_layout.addWidget(self.timeline, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("ganttSplitter")
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 520])
        splitter.setChildrenCollapsible(False)
        return splitter

    # --- 表示 ---

    @property
    def data(self) -> GanttData | None:
        """表示中のガントの内容。

        Returns:
            ガントの内容。まだ作っていなければ None。
        """
        return self._data

    @property
    def scale(self) -> str:
        """時間軸の目盛り。

        Returns:
            "day" か "week"。
        """
        return self._scale

    def _schedule_refresh(self) -> None:
        """作り直しを1回にまとめて予約する。"""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        """内容を読み直して作り直す。スクロール位置は保つ。"""
        self._refresh_pending = False
        store = self._store
        # 作り直しても同じ日付が左端に来るよう、スクロール位置を日時で覚えておく
        left_edge = self._left_edge()
        vertical = self.timeline.verticalScrollBar().value()
        data = store.gantt()
        self._data = data
        categories = {c.saved_id: c for c in store.categories(include_archived=True)}
        selected_row = self._row_of(store.selected)
        now = store.now()
        state = TimelineState(
            self._scale, categories, now, selected_row, store.week_start().weekday
        )
        self.timeline.set_data(data, state)
        geometry = self.timeline.geometry_
        if geometry is not None:
            self.header.set_data(geometry, self._scale, now.date())
        self.labels.set_data(data.rows, categories, selected_row)
        self._empty_label.setVisible(not data.rows)
        if left_edge is not None:
            self._scroll_to(left_edge)
        self.timeline.verticalScrollBar().setValue(vertical)
        self.header.set_offset(self.timeline.horizontalScrollBar().value())
        self.labels.set_offset(self.timeline.verticalScrollBar().value())

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._positioned:
            # 表示されて幅が決まってから、今日(または選択中の項目)の位置へ移動する
            self._positioned = True
            QTimer.singleShot(0, self._initial_position)

    def _initial_position(self) -> None:
        """今日の位置へスクロールし、選択中の項目があればそれを見せる。"""
        self.scroll_to_today()
        self._reveal_selected()

    def set_scale(self, scale: str) -> None:
        """時間軸の目盛りを変える。表示の中央の日時は保つ。

        Args:
            scale: "day" か "week"。
        """
        if scale == self._scale:
            return
        center = self._center_moment()
        self._scale = scale
        for button in self._scale_buttons.buttons():
            button.setChecked(button.text() == ("日" if scale == "day" else "週"))
        self.refresh()
        if center is not None:
            self._scroll_to(center, ratio=0.5)

    def scroll_to_today(self) -> None:
        """今日が左から 1/4 の位置に来るようにスクロールする。"""
        self._scroll_to(self._store.now(), ratio=0.25)

    def _left_edge(self) -> datetime | None:
        """左端の日時を返す。

        Returns:
            左端の日時。初回の位置合わせの前なら None。
        """
        geometry = self.timeline.geometry_
        if geometry is None or not self._positioned:
            return None
        return geometry.date_at(self.timeline.horizontalScrollBar().value())

    def _center_moment(self) -> datetime | None:
        """表示の中央の日時を返す。

        Returns:
            中央の日時。まだ描いていなければ None。
        """
        geometry = self.timeline.geometry_
        if geometry is None:
            return None
        bar = self.timeline.horizontalScrollBar()
        return geometry.date_at(bar.value() + self.timeline.viewport().width() / 2)

    def _scroll_to(self, moment: datetime, *, ratio: float = 0.0) -> None:
        """日時が、表示の幅の ratio の位置に来るようにスクロールする。"""
        geometry = self.timeline.geometry_
        if geometry is None:
            return
        x = geometry.x(moment) - self.timeline.viewport().width() * ratio
        self.timeline.horizontalScrollBar().setValue(int(x))

    def _scroll_vertically(self, delta: int) -> None:
        """左のツリーのホイールで、時間軸を縦にスクロールする。"""
        bar = self.timeline.verticalScrollBar()
        bar.setValue(bar.value() + delta)

    # --- 選択の同期 ---

    def _row_of(self, ref: ItemRef | None) -> int:
        """項目の行番号を返す。

        Returns:
            行番号。ガントに出ていなければ -1。
        """
        if ref is None or self._data is None:
            return -1
        match ref.kind:
            case ItemKind.TASK:
                index = row_index(self._data, task_id=ref.id)
            case ItemKind.EVENT:
                index = row_index(self._data, event_id=ref.id)
            case ItemKind.PROJECT:
                index = row_index(self._data, project_id=ref.id)
        return index

    def _on_row_clicked(self, index: int) -> None:
        """行をクリックしたら、その項目を選択する。"""
        ref = _ref_of(self._data.rows[index]) if self._data is not None else None
        if ref is None:
            return
        self._selecting = True
        try:
            self._store.select(ref)
        finally:
            self._selecting = False
        self._highlight(index)

    def _highlight(self, index: int) -> None:
        """行を強調する。"""
        self.timeline.set_selected_row(index)
        self.labels.set_selected_row(index)

    def _on_selection_changed(self, _ref: ItemRef | None) -> None:
        """他のビューで選択が変わったら、その項目を見せる。"""
        if self._selecting or self._refresh_pending:
            return  # 自分のクリックか、直後の作り直しで反映される
        self._reveal_selected()

    def _reveal_selected(self) -> None:
        """選択中の項目の行をハイライトし、帯が見える位置までスクロールする。"""
        index = self._row_of(self._store.selected)
        self._highlight(index)
        bar = self.timeline.bars.get(index)
        if bar is not None:
            visible = QRectF(bar.rect)
            # 長い帯は左端(開始)が見えればよい
            visible.setWidth(min(visible.width(), self.timeline.viewport().width() * 0.6))
            self.timeline.ensureVisible(visible, 60, ROW_H)
        elif index >= 0:
            self.timeline.ensureVisible(QRectF(0, index * ROW_H, 1, ROW_H), 0, ROW_H)

    # --- 操作 ---

    def _apply_drag(self, index: int, mode: str, days: int) -> None:
        """帯のドラッグを保存する。保存後は変更シグナル経由で作り直される。"""
        if self._data is None or not 0 <= index < len(self._data.rows):
            return
        if not self._store.apply_gantt_drag(self._data.rows[index], DragMode(mode), days):
            self.refresh()  # 保存しなかったときは仮の位置を元に戻す

    def _show_menu(self, index: int, global_pos: QPoint) -> None:
        """行の右クリックメニューを出す。"""
        row = self._data.rows[index] if self._data is not None else None
        ref = _ref_of(row) if row is not None else None
        if row is None or ref is None:
            return
        store = self._store
        menu = QMenu(self)
        actions: dict[QAction, Callable[[], object]] = {}
        if ref.kind is ItemKind.TASK:
            actions[menu.addAction("ToDoで表示")] = lambda: store.reveal(ref, VIEW_TODO)
        if ref.kind is ItemKind.PROJECT:
            actions[menu.addAction("編集…")] = lambda: edit_project(self, store, ref.id)
        else:
            actions[menu.addAction("カレンダーで表示")] = lambda: store.reveal(ref, VIEW_CALENDAR)
        if row.task is not None and store.recurrence(ref.id) is None:
            menu.addSeparator()
            actions |= self._task_actions(menu, ref.id)
        _run_menu(menu, actions, global_pos)

    def _task_actions(self, menu: QMenu, task_id: int) -> dict[QAction, Callable[[], object]]:
        """繰り返しでないタスクの、サブタスクと前後関係の項目をメニューに足す。

        Returns:
            メニューの項目 → 処理。
        """
        store = self._store
        return {
            menu.addAction("サブタスクを追加…"): lambda: self._add_subtask(task_id),
            menu.addAction("前のタスクを追加…"): lambda: add_dependency_interactive(
                self, store, task_id, as_predecessor=True
            ),
            menu.addAction("後のタスクを追加…"): lambda: add_dependency_interactive(
                self, store, task_id, as_predecessor=False
            ),
        }

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

    def _show_link_menu(self, dependency: Dependency, global_pos: QPoint) -> None:
        """矢印の右クリック: 前後関係を外すか、両端のタスクを選ぶ。"""
        store = self._store
        before = store.task(dependency.predecessor_id)
        after = store.task(dependency.successor_id)
        if before is None or after is None:
            return
        before_ref = ItemRef(ItemKind.TASK, before.saved_id)
        after_ref = ItemRef(ItemKind.TASK, after.saved_id)
        menu = QMenu(self)
        actions: dict[QAction, Callable[[], object]] = {
            menu.addAction("この前後関係を外す"): lambda: store.remove_dependency(
                dependency.predecessor_id, dependency.successor_id
            )
        }
        menu.addSeparator()
        actions[menu.addAction(f"前のタスク「{before.title}」を選択")] = lambda: store.select(
            before_ref
        )
        actions[menu.addAction(f"後のタスク「{after.title}」を選択")] = lambda: store.select(
            after_ref
        )
        _run_menu(menu, actions, global_pos)

"""カレンダー(月表示と週表示)。週表示は ui/week_view.py にある。

イベントは期間の帯、期限つきタスクは期限日のラベル、日課は達成数で出す。
QCalendarWidget は複数日にまたがる帯を描けないので、グリッドを QPainter で自作している。
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from neko_gantcha.domain.calendar import due_day, event_days, shift_month, week_start
from neko_gantcha.services.store import VIEW_GANTT, VIEW_TODO, ItemKind, ItemRef
from neko_gantcha.ui.calendar_canvas import (
    COLOR_DONE,
    COLOR_MUTED,
    WEEKDAYS,
    CalendarCanvas,
    CanvasState,
    keep_selected_visible,
    paint_style,
    weekday_color,
)
from neko_gantcha.ui.week_view import WeekPanel
from neko_gantcha.ui.widgets import color_icon, format_datetime

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QAction, QPaintEvent

    from neko_gantcha.domain.calendar import BandSegment, DayCell, MonthView
    from neko_gantcha.domain.models import Category
    from neko_gantcha.domain.todo import TodoRow
    from neko_gantcha.services.store import AppStore
    from neko_gantcha.ui.calendar_canvas import PaintStyle

MODE_MONTH = "month"
MODE_WEEK = "week"

_HEADER_H = 24
_NUMBER_H = 20  # 日付の数字を置く行の高さ
_LANE_H = 18  # イベントの帯1段の高さ
_LABEL_H = 17  # 期限ラベル1行の高さ
_WIDE_CELL = 90  # これより広いマスでは、日課の達成数に「日課」と書き添える
_ROLE_REF = Qt.ItemDataRole.UserRole


def _run_menu(menu: QMenu, actions: dict[QAction, Callable[[], object]], pos: QPoint) -> None:
    """メニューを出し、選ばれた項目の処理を行う。何も選ばなければ何もしない。"""
    chosen = menu.exec(pos)
    action = actions.get(chosen)
    if action is not None:
        action()


@dataclass(frozen=True, slots=True)
class _WeekRow:
    """月グリッドの1週ぶんの行。

    Attributes:
        cells: 7日ぶんのマス。
        segments: その週にかかるイベントの帯。
        xs: 列の境目の x 座標(8個)。
        top: 行の上端。
        bottom: 行の下端。
    """

    cells: list[DayCell]
    segments: list[BandSegment]
    xs: list[int]
    top: int
    bottom: int


@dataclass(frozen=True, slots=True)
class _LabelSlot:
    """1日ぶんの期限ラベルを置く場所。

    Attributes:
        area: ラベルを並べる矩形。
        number_top: 日付の数字の行の上端(「+N」を置く)。
        capacity: 入るラベルの行数。
        hidden_bands: 入りきらなかった帯の数。
    """

    area: QRect
    number_top: int
    capacity: int
    hidden_bands: int


def _hidden_band_counts(segments: list[BandSegment], shown_lanes: int) -> list[int]:
    """曜日ごとの、入りきらずに描かなかった帯の数を数える。

    Returns:
        曜日ごとの件数(7個)。
    """
    counts = [0] * 7
    for segment in segments:
        if segment.lane >= shown_lanes:
            for col in range(segment.first_col, segment.last_col + 1):
                counts[col] += 1
    return counts


class MonthGrid(CalendarCanvas):
    """月グリッドの描画とクリック判定。表示内容は set_data() で受け取る。

    Args:
        parent: 親のウィジェット。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: MonthView | None = None
        self.setMinimumSize(420, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, view: MonthView, state: CanvasState) -> None:
        """表示する内容を受け取り、描き直す。

        Args:
            view: 月表示の内容。
            state: 分類、現在時刻、選択などの情報。
        """
        self._view = view
        self._state = state
        self.update()

    # --- 描画 ---

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
        width, height = self.width(), self.height()
        xs = [round(i * width / 7) for i in range(8)]
        ys = [_HEADER_H + round(i * (height - _HEADER_H) / 6) for i in range(7)]
        painter.fillRect(self.rect(), style.base)

        # 曜日の見出し
        for col in range(7):
            painter.setPen(weekday_color(col, style.text))
            rect = QRect(xs[col], 0, xs[col + 1] - xs[col], _HEADER_H)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, WEEKDAYS[col])

        for index, cells in enumerate(view.weeks):
            week = _WeekRow(cells, view.bands[index], xs, ys[index], ys[index + 1])
            self._draw_week(painter, style, week)

        # 罫線
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(style.mix(0.18), 1))
        for x in xs[1:-1]:
            painter.drawLine(x, _HEADER_H, x, height)
        for y in ys[:-1]:
            painter.drawLine(0, y, width, y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, width - 1, height - 1)

        # 選択中の日
        for rect, day in self._day_hits:
            if day == state.selected_day:
                painter.setPen(QPen(style.highlight, 2))
                painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.end()

    def _draw_week(self, painter: QPainter, style: PaintStyle, week: _WeekRow) -> None:
        """1週ぶんのマス、イベントの帯、期限ラベルを描く。"""
        xs, top, bottom = week.xs, week.top, week.bottom
        cell_h = bottom - top
        # 期限ラベルを最低1行は出せるよう、帯の段数を抑える
        shown_lanes = min(
            max((s.lane for s in week.segments), default=-1) + 1,
            max(1, (cell_h - _NUMBER_H - _LABEL_H) // _LANE_H),
        )
        for col, cell in enumerate(week.cells):
            rect = QRect(xs[col], top, xs[col + 1] - xs[col], cell_h)
            self._draw_cell(painter, style, cell, rect=rect, col=col)

        painter.setFont(style.small)
        self._draw_bands(painter, style, week, shown_lanes=shown_lanes)

        labels_top = top + _NUMBER_H + shown_lanes * _LANE_H + 1
        capacity = max(0, (bottom - labels_top - 2) // _LABEL_H)
        hidden = _hidden_band_counts(week.segments, shown_lanes)
        for col, cell in enumerate(week.cells):
            area = QRect(xs[col], labels_top, xs[col + 1] - xs[col], bottom - labels_top)
            slot = _LabelSlot(area, top, capacity, hidden[col])
            self._draw_labels(painter, style, cell.due, slot=slot)

    def _draw_bands(
        self, painter: QPainter, style: PaintStyle, week: _WeekRow, *, shown_lanes: int
    ) -> None:
        """1週ぶんのイベントの帯を、入る段数まで描く。"""
        xs = week.xs
        for segment in week.segments:
            if segment.lane >= shown_lanes:
                continue
            left = xs[segment.first_col] + (3 if segment.starts_here else 0)
            right = xs[segment.last_col + 1] - (3 if segment.ends_here else 0)
            y = week.top + _NUMBER_H + segment.lane * _LANE_H
            self._draw_event_band(
                painter, style, segment, rect=QRect(left, y, right - left, _LANE_H - 3)
            )

    def _draw_cell(
        self, painter: QPainter, style: PaintStyle, cell: DayCell, *, rect: QRect, col: int
    ) -> None:
        """マスの背景、日付、日課の達成数を描き、クリック判定に記録する。"""
        self._day_hits.append((rect, cell.day))
        dim_text = style.mix(0.4)
        if not cell.in_month:
            painter.fillRect(rect, style.mix(0.05))
        number_rect = QRect(rect.left() + 4, rect.top() + 2, 24, _NUMBER_H - 4)
        today = self._state.now.date() if self._state is not None else None
        if cell.day == today:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(style.highlight)
            painter.drawRoundedRect(number_rect, 9, 9)
            painter.setPen(style.highlighted_text)
            bold = self.font()
            bold.setBold(True)
            painter.setFont(bold)
        else:
            painter.setPen(dim_text if not cell.in_month else weekday_color(col, style.text))
            painter.setFont(self.font())
        painter.drawText(number_rect, Qt.AlignmentFlag.AlignCenter, str(cell.day.day))
        if cell.daily_total:
            complete = cell.daily_done == cell.daily_total
            painter.setFont(style.small)
            painter.setPen(COLOR_DONE if complete else dim_text)
            summary = QRect(rect.left(), rect.top() + 2, rect.width() - 5, _NUMBER_H - 4)
            # 狭いセルでは日付や「+N」と重ならないよう、数字だけにする
            prefix = "日課 " if rect.width() >= _WIDE_CELL else ""
            painter.drawText(
                summary,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{prefix}{cell.daily_done}/{cell.daily_total}",
            )

    def _draw_labels(
        self, painter: QPainter, style: PaintStyle, rows: list[TodoRow], *, slot: _LabelSlot
    ) -> None:
        """1日ぶんの期限ラベルを描く。入りきらない帯とラベルは「+N」で出す。"""
        shown = rows[: slot.capacity]
        if shown:
            selected = self._state.selected if self._state is not None else None
            shown = keep_selected_visible(rows, shown, selected)
        # 入りきらない帯とラベルの件数は、日付の数字の右に「+N」で出す
        overflow = slot.hidden_bands + len(rows) - len(shown)
        area = slot.area
        if overflow:
            painter.setPen(style.mix(0.4))
            painter.drawText(
                QRect(area.left() + 30, slot.number_top + 2, 40, _NUMBER_H - 4),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"+{overflow}",
            )
        y = area.top()
        for row in shown:
            rect = QRect(area.left() + 2, y, area.width() - 4, _LABEL_H - 1)
            self._draw_task_label(painter, style, row, rect=rect)
            y += _LABEL_H


class CalendarView(QWidget):
    """カレンダーのビュー。月表示と週表示を切り替え、下に選んだ日の一覧を出す。

    Args:
        store: データ操作の窓口。
        parent: 親のウィジェット。
    """

    def __init__(self, store: AppStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        today = store.now().date()
        self._year, self._month = today.year, today.month
        self._selected_day: date = today
        self._refresh_pending = False
        self._selecting = False  # 自分のクリックで選択を変えている最中

        self._mode = MODE_MONTH
        self._week_first = week_start(today)  # 週表示で出している週の月曜日
        self._scrolled_week: date | None = None  # 時間軸の初期位置を合わせた週

        self.grid = MonthGrid()
        self.week = WeekPanel()
        self._pages = QStackedWidget()
        self._pages.addWidget(self.grid)
        self._pages.addWidget(self.week)
        self._day_label = QLabel()
        self._day_label.setObjectName("paneTitle")
        self._day_list = QListWidget()
        self._day_list.setObjectName("dayList")
        self._day_list.setFixedHeight(128)
        self._day_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(self._build_nav())
        layout.addWidget(self._pages, 1)
        layout.addWidget(self._day_label)
        layout.addWidget(self._day_list)

        for canvas in (self.grid, *self.week.canvases):
            canvas.day_clicked.connect(self._on_day_clicked)
            canvas.item_clicked.connect(self._select_from_view)
            canvas.day_double_clicked.connect(self._add_task_on)
            canvas.context_requested.connect(self._show_menu)
        self._day_list.itemClicked.connect(self._on_list_clicked)
        self._day_list.customContextMenuRequested.connect(self._show_list_menu)

        for signal in (
            store.tasks_changed,
            store.events_changed,
            store.categories_changed,
            store.filter_changed,
            store.periods_changed,
            store.minute_ticked,
        ):
            signal.connect(self._schedule_refresh)
        store.selection_changed.connect(self._on_selection_changed)
        self.refresh()

    def _build_nav(self) -> QHBoxLayout:
        """上の操作欄(月・週の切り替え、前後の移動、今日、見出し)を作る。

        Returns:
            操作欄のレイアウト。
        """
        month_button = QToolButton()
        month_button.setText("月")
        month_button.setCheckable(True)
        month_button.setChecked(True)
        week_button = QToolButton()
        week_button.setText("週")
        week_button.setCheckable(True)
        self._mode_buttons = QButtonGroup(self)
        self._mode_buttons.addButton(month_button)
        self._mode_buttons.addButton(week_button)
        self._month_button, self._week_button = month_button, week_button
        self._previous = QToolButton()
        self._previous.setText("◀")
        self._following = QToolButton()
        self._following.setText("▶")
        today_button = QPushButton("今日")
        self._month_label = QLabel()
        self._month_label.setObjectName("itemTitle")
        self._hint = QLabel()
        self._hint.setObjectName("info")
        nav = QHBoxLayout()
        for widget in (month_button, week_button):
            nav.addWidget(widget)
        nav.addSpacing(8)
        for widget in (self._previous, today_button, self._following):
            nav.addWidget(widget)
        nav.addSpacing(8)
        nav.addWidget(self._month_label)
        nav.addStretch(1)
        nav.addWidget(self._hint)

        self._previous.clicked.connect(lambda: self._move(-1))
        self._following.clicked.connect(lambda: self._move(+1))
        today_button.clicked.connect(self.go_today)
        month_button.clicked.connect(lambda: self.set_mode(MODE_MONTH))
        week_button.clicked.connect(lambda: self.set_mode(MODE_WEEK))
        return nav

    # --- 表示する月と日 ---

    @property
    def shown_month(self) -> tuple[int, int]:
        """月表示で出している年月。

        Returns:
            (年, 月)。
        """
        return self._year, self._month

    @property
    def selected_day(self) -> date:
        """選択中の日。

        Returns:
            選択中の日。
        """
        return self._selected_day

    @property
    def mode(self) -> str:
        """表示の種類。

        Returns:
            MODE_MONTH か MODE_WEEK。
        """
        return self._mode

    @property
    def shown_week(self) -> date:
        """週表示で出している週。

        Returns:
            その週の月曜日。
        """
        return self._week_first

    def set_mode(self, mode: str) -> None:
        """月表示と週表示を切り替える。選択中の日を含む月・週を出す。

        Args:
            mode: MODE_MONTH か MODE_WEEK。
        """
        self._mode = mode
        day = self._selected_day
        self._year, self._month = day.year, day.month
        self._week_first = week_start(day)
        (self._month_button if mode == MODE_MONTH else self._week_button).setChecked(True)
        self._pages.setCurrentWidget(self.grid if mode == MODE_MONTH else self.week)
        self.refresh()

    def show_day(self, day: date) -> None:
        """その日を選び、見えていなければその月(週表示ならその週)に移動する。

        Args:
            day: 選ぶ日。
        """
        self._selected_day = day
        if self._mode == MODE_WEEK:
            self._week_first = week_start(day)
        else:
            view = self._store.calendar_month(self._year, self._month)
            if not view.first_day <= day <= view.last_day:
                self._year, self._month = day.year, day.month
        self.refresh()

    def go_today(self) -> None:
        """今日を含む月・週に戻り、今日を選ぶ。"""
        today = self._store.now().date()
        self._year, self._month = today.year, today.month
        self._week_first = week_start(today)
        self._selected_day = today
        self._scrolled_week = None  # 時間軸も今の時刻へ戻す
        self.refresh()

    def _move(self, delta: int) -> None:
        """前後の月(週表示なら週)へ移る。"""
        if self._mode == MODE_WEEK:
            self._week_first += timedelta(weeks=delta)
            # 選択中の日は同じ曜日のまま新しい週へ移す。日別一覧を見えている週と合わせるため
            self._selected_day = self._week_first + timedelta(days=self._selected_day.weekday())
        else:
            self._year, self._month = shift_month(self._year, self._month, delta)
        self.refresh()

    # --- 再描画 ---

    def _schedule_refresh(self) -> None:
        """描き直しを1回にまとめて予約する。"""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        """今の表示の内容を読み直して描き直す。"""
        self._refresh_pending = False
        store = self._store
        categories = {c.saved_id: c for c in store.categories(include_archived=True)}
        now = store.now()
        state = CanvasState(categories, now, self._selected_day, store.selected)
        if self._mode == MODE_WEEK:
            self._refresh_week(state)
        else:
            self._month_label.setText(f"{self._year}年{self._month}月")
            self._previous.setToolTip("前の月")
            self._following.setToolTip("次の月")
            self._hint.setText("ダブルクリックで、その日が期限のタスクを追加")
            self.grid.set_data(store.calendar_month(self._year, self._month), state)
        self._rebuild_day_list(categories)

    def _refresh_week(self, state: CanvasState) -> None:
        """週表示の内容を読み直して描き直す。週を開いたときは時間軸の位置も合わせる。"""
        first = self._week_first
        last = first + timedelta(days=6)
        now = state.now
        self._month_label.setText(_week_title(first, last))
        self._previous.setToolTip("前の週")
        self._following.setToolTip("次の週")
        self._hint.setText("ダブルクリックで、その時刻が期限のタスクを追加")
        self.week.set_data(self._store.calendar_week(first), state)
        if self._scrolled_week != first:
            # 週を開いたときは、今週なら今の時刻の少し前、それ以外は朝 8 時から見せる
            self._scrolled_week = first
            hour = 8.0
            if first <= now.date() <= last:
                hour = max(0.0, now.hour + now.minute / 60 - 2)
            QTimer.singleShot(0, lambda: self.week.scroll_to_hour(hour))

    def _rebuild_day_list(self, categories: Mapping[int, Category]) -> None:
        """下の、選んだ日のイベント・期限のタスク・日課の一覧を作り直す。"""
        day = self._selected_day
        detail = self._store.calendar_day(day)
        self._day_label.setText(f"{day.year}年{day.month}月{day.day}日({WEEKDAYS[day.weekday()]})")
        self._day_list.clear()

        def add(text: str, category_id: int, ref: ItemRef, *, muted: bool = False) -> None:
            item = QListWidgetItem(text)
            category = categories.get(category_id)
            if category is not None:
                item.setIcon(color_icon(category.color))
            item.setData(_ROLE_REF, ref)
            if muted:
                item.setForeground(COLOR_MUTED)
            self._day_list.addItem(item)
            if ref == self._store.selected:
                self._day_list.setCurrentItem(item)

        for event in detail.events:
            period = f"{format_datetime(event.start_at)} 〜 {format_datetime(event.end_at)}"
            add(
                f"[イベント] {event.name}  {period}",
                event.category_id,
                ItemRef(ItemKind.EVENT, event.saved_id),
            )
        for row in detail.due:
            mark = "✓" if row.done else "・"
            due = f"{row.due:%H:%M}" if row.due is not None else ""
            add(
                f"[期限 {due}] {mark} {row.task.title}",
                row.task.category_id,
                ItemRef(ItemKind.TASK, row.task.saved_id),
                muted=row.done,
            )
        for entry in detail.dailies:
            mark = "✓" if entry.done else "・"
            count = f"  {entry.count}/{entry.target}" if entry.target > 1 else ""
            add(
                f"[日課] {mark} {entry.task.title}{count}",
                entry.task.category_id,
                ItemRef(ItemKind.TASK, entry.task.saved_id),
                muted=entry.done,
            )
        if self._day_list.count() == 0:
            placeholder = QListWidgetItem("この日のイベントやタスクはありません。")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._day_list.addItem(placeholder)

    # --- 選択の同期 ---

    def _on_day_clicked(self, day: date) -> None:
        """日付をクリックしたら、その日を選ぶ。"""
        self._selected_day = day
        self.refresh()

    def _select_from_view(self, ref: ItemRef) -> None:
        """カレンダーの中でクリックした項目を選択する。"""
        self._selecting = True
        try:
            self._store.select(ref)
        finally:
            self._selecting = False
        self.refresh()

    def _on_list_clicked(self, item: QListWidgetItem) -> None:
        """日別一覧の項目をクリックしたら、その項目を選択する。"""
        ref = item.data(_ROLE_REF)
        if ref is not None:
            self._select_from_view(ref)

    def _on_selection_changed(self, ref: ItemRef | None) -> None:
        """他のビューで選ばれた項目を、見える位置に出してハイライトする。"""
        if self._selecting:
            return
        day = self._day_of(ref)
        if day is not None:
            self.show_day(day)
            if self._mode == MODE_WEEK:
                # 時間軸に置かれた項目なら、配置が済んでからその時刻までスクロールする
                QTimer.singleShot(0, lambda: self.week.ensure_visible(ref))
        else:
            self._schedule_refresh()

    def _day_of(self, ref: ItemRef | None) -> date | None:
        """項目を表示するために選ぶ日を決める。

        Returns:
            選ぶ日。カレンダーに出ない項目なら None。
        """
        day: date | None = None
        if ref is not None and ref.kind is ItemKind.TASK:
            row = self._store.todo_row(ref.id)
            day = due_day(row) if row is not None else None
        elif ref is not None and ref.kind is ItemKind.EVENT:
            event = self._store.get_event(ref.id)
            if event is not None:
                first, last = event_days(event)
                # 選択中の日がすでに期間内なら動かさない
                day = self._selected_day if first <= self._selected_day <= last else first
        return day

    # --- 操作 ---

    def _add_task_on(self, target: date | datetime) -> None:
        """日付ならその日の 23:59、日時ならその時刻を期限にしてタスクを追加する。"""
        store = self._store
        categories = store.categories()
        if not categories:
            return
        filtered = store.filter.category_id
        category_id = filtered if filtered is not None else categories[0].saved_id
        if isinstance(target, datetime):
            due, day = target, target.date()
            when = f"{day.month}月{day.day}日 {target:%H:%M}"
        else:
            due, day = datetime.combine(target, time(23, 59)), target
            when = f"{day.month}月{day.day}日"
        title, accepted = QInputDialog.getText(
            self, "タスクの追加", f"{when}が期限のタスクのタイトル"
        )
        if not accepted or not title.strip():
            return
        task = store.add_task(title, category_id, due_at=due)
        self._selected_day = day
        self._select_from_view(ItemRef(ItemKind.TASK, task.saved_id))

    def _reveal_actions(
        self, menu: QMenu, ref: ItemRef | None
    ) -> dict[QAction, Callable[[], object]]:
        """「ToDoで表示」「ガントで表示」のうち、項目に使えるものをメニューに足す。

        Returns:
            メニューの項目 → 処理。
        """
        actions: dict[QAction, Callable[[], object]] = {}
        if ref is None:
            return actions
        store = self._store
        if self._can_show_in_todo(ref):
            actions[menu.addAction("ToDoで表示")] = lambda: store.reveal(ref, VIEW_TODO)
        if store.shown_in_gantt(ref):
            actions[menu.addAction("ガントで表示")] = lambda: store.reveal(ref, VIEW_GANTT)
        return actions

    def _show_menu(self, ref: ItemRef | None, global_pos: QPoint) -> None:
        """カレンダーの中の右クリックメニューを出す。"""
        menu = QMenu(self)
        actions = self._reveal_actions(menu, ref)
        add_task = menu.addAction("この日が期限のタスクを追加…")
        actions[add_task] = lambda: self._add_task_on(self._selected_day)
        _run_menu(menu, actions, global_pos)

    def _show_list_menu(self, pos: QPoint) -> None:
        """日別一覧の右クリックメニューを出す。出せる項目がなければ出さない。"""
        item = self._day_list.itemAt(pos)
        ref = item.data(_ROLE_REF) if item is not None else None
        menu = QMenu(self)
        actions = self._reveal_actions(menu, ref)
        if actions:
            _run_menu(menu, actions, self._day_list.viewport().mapToGlobal(pos))

    @staticmethod
    def _can_show_in_todo(ref: ItemRef | None) -> bool:
        """項目を ToDo で表示できるか。

        Returns:
            タスクなら True。
        """
        return ref is not None and ref.kind is ItemKind.TASK


def _week_title(first: date, last: date) -> str:
    """週の見出しを作る。

    Returns:
        `2026年11月16日〜22日`、`2026年11月30日〜12月6日`、
        `2026年12月28日〜2027年1月3日` のような文字列。
    """
    if first.year != last.year:
        tail = f"{last.year}年{last.month}月{last.day}日"
    elif first.month != last.month:
        tail = f"{last.month}月{last.day}日"
    else:
        tail = f"{last.day}日"
    return f"{first.year}年{first.month}月{first.day}日〜{tail}"

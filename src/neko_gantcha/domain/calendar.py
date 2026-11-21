"""カレンダー(月表示・週表示)に載せる内容の計算。Qt には依存しない。

週は月曜始まり。月表示は常に6週ぶん(42日)を返す。
週表示は1週間ぶんを、終日欄(帯と期限ラベル)と時間軸(時刻つきの項目)に分けて返す。
"""

import calendar
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import EventRelation, Freq

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from neko_gantcha.domain.models import Category, Event, Recurrence, Task
    from neko_gantcha.domain.todo import Counts, TodoRow


WEEKS_IN_MONTH_VIEW = 6
LAST_COL = 6  # 日曜の列


@dataclass(frozen=True, slots=True)
class CalendarInputs:
    """カレンダーの組み立てに使う情報。

    Attributes:
        rows: 絞り込み後のタスクの行。
        shown_events: 帯や時間軸に描くイベント。
        events: 全イベント(期間限定の日課の判定に使う)。
        categories: 分類 id → 分類。
        counts: (タスク id, 日付の文字列) → その日の達成回数。
        now: 現在の日時。
    """

    rows: list[TodoRow]
    shown_events: list[Event]
    events: Mapping[int, Event]
    categories: Mapping[int, Category]
    counts: Counts
    now: datetime


@dataclass(frozen=True, slots=True)
class BandSegment:
    """イベントの帯のうち、1週間に収まる部分。"""

    event: Event
    first_col: int  # 0=月曜 … 6=日曜
    last_col: int
    lane: int  # 同じ週の中での段(0 が最上段)
    starts_here: bool  # この週の中でイベントが始まる
    ends_here: bool  # この週の中でイベントが終わる


@dataclass(frozen=True, slots=True)
class DailyEntry:
    """ある日の日課1件の達成状況。"""

    task: Task
    count: int
    target: int

    @property
    def done(self) -> bool:
        """その日の目標に届いたか。

        Returns:
            達成回数が目標以上なら True。
        """
        return self.count >= self.target


@dataclass(frozen=True, slots=True)
class DayCell:
    """月表示・週表示の1日分のマス。

    Attributes:
        day: 日付。
        in_month: 表示中の月の日か(月表示の前後の月の日は偽)。
        due: その日が期限のタスク。
        daily_done: その日に達成した日課の数。
        daily_total: その日に対象だった日課の数。0 なら日課の表示なし(未来の日、または
            日課がない)。
    """

    day: date
    in_month: bool
    due: list[TodoRow]  # その日が期限のタスク
    daily_done: int
    daily_total: int  # 0 なら日課の表示なし(未来の日、または日課がない)


@dataclass(frozen=True, slots=True)
class MonthView:
    """月表示の内容。

    Attributes:
        year: 年。
        month: 月。
        weeks: 6週ぶんのマス。
        bands: 週ごとのイベントの帯。
    """

    year: int
    month: int
    weeks: list[list[DayCell]]  # 6週ぶん。1週は7日
    bands: list[list[BandSegment]]  # 週ごとのイベントの帯

    @property
    def first_day(self) -> date:
        """表示の最初の日。

        Returns:
            最初の週の月曜日。
        """
        return self.weeks[0][0].day

    @property
    def last_day(self) -> date:
        """表示の最後の日。

        Returns:
            最後の週の日曜日。
        """
        return self.weeks[-1][-1].day


@dataclass(frozen=True, slots=True)
class DayDetail:
    """日別一覧の内容。

    Attributes:
        day: 日付。
        events: その日にかかるイベント。
        due: その日が期限のタスク。
        dailies: その日の日課と達成状況。
    """

    day: date
    events: list[Event]
    due: list[TodoRow]
    dailies: list[DailyEntry]


def month_weeks(year: int, month: int) -> list[list[date]]:
    """月曜始まりで、その月を含む6週ぶんの日付を返す。

    Args:
        year: 年。
        month: 月。

    Returns:
        週ごとの日付(1週は7日)。
    """
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    while len(weeks) < WEEKS_IN_MONTH_VIEW:
        last = weeks[-1][-1]
        weeks.append([last + timedelta(days=offset) for offset in range(1, 8)])
    return weeks


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """年月を月単位でずらす。

    Args:
        year: 年。
        month: 月。
        delta: ずらす月数。負なら前へ。

    Returns:
        (年, 月)。
    """
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def event_days(event: Event) -> tuple[date, date]:
    """イベントがかかる最初と最後の日付を返す。終了が 0:00 ちょうどなら前日までとする。

    Args:
        event: 対象のイベント。

    Returns:
        (最初の日, 最後の日)。
    """
    first = event.start_at.date()
    last = event.end_at.date()
    if event.end_at.time() == time(0, 0) and last > first:
        last -= timedelta(days=1)
    return first, last


def events_on(events: Iterable[Event], day: date) -> list[Event]:
    """その日にかかるイベントを開始順に返す。

    Args:
        events: 候補のイベント。
        day: 日付。

    Returns:
        その日にかかるイベント。
    """
    result = [e for e in events if event_days(e)[0] <= day <= event_days(e)[1]]
    result.sort(key=lambda e: (e.start_at, e.id or 0))
    return result


def layout_week(events: Iterable[Event], week: list[date]) -> list[BandSegment]:
    """1週間ぶんの帯を、重ならないように段へ割り当てる。

    Args:
        events: 帯にするイベント。
        week: 週の日付(月曜〜日曜)。

    Returns:
        その週にかかる帯。
    """
    week_start, week_end = week[0], week[-1]
    segments: list[BandSegment] = []
    lane_ends: list[int] = []  # 段ごとの、埋まっている最後の列
    for event in sorted(events, key=lambda e: (e.start_at, e.id or 0)):
        first, last = event_days(event)
        if last < week_start or first > week_end:
            continue
        first_col = max(0, (first - week_start).days)
        last_col = min(LAST_COL, (last - week_start).days)
        lane = next((i for i, end in enumerate(lane_ends) if end < first_col), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(last_col)
        else:
            lane_ends[lane] = last_col
        segments.append(
            BandSegment(event, first_col, last_col, lane, first >= week_start, last <= week_end)
        )
    return segments


def due_day(row: TodoRow) -> date | None:
    """カレンダーで期限ラベルを出す日を返す。

    繰り返しタスクと、期限を個別に持たない「イベント中」タスクは出さない
    (前者は達成数で、後者はイベントの帯で表す)。

    Args:
        row: 対象の行。

    Returns:
        期限の日付。出さないタスクは None。
    """
    if row.is_recurring or row.due is None:
        return None
    task = row.task
    if (
        row.event is not None
        and task.event_relation is EventRelation.DURING
        and task.due_at is None
    ):
        return None
    return row.due.date()


def daily_entries(day: date, inputs: CalendarInputs) -> list[DailyEntry]:
    """その日(リセット時刻で区切った1日)に対象だった日課と達成状況を返す。

    Args:
        day: 日付。
        inputs: カレンダーの組み立てに使う情報。

    Returns:
        日課と達成状況。未達成が先に来る。未来の日は空。
    """
    entries: list[DailyEntry] = []
    for row in inputs.rows:
        recurrence = row.recurrence
        if recurrence is None or recurrence.freq is not Freq.DAILY:
            continue
        task = row.task
        start = datetime.combine(day, inputs.categories[task.category_id].reset_rule.daily_time)
        if start > inputs.now or not _daily_applies(
            task, recurrence, start=start, events=inputs.events
        ):
            continue
        count = inputs.counts.get((task.saved_id, day.isoformat()), 0)
        entries.append(DailyEntry(task, count, task.progress_target or 1))
    entries.sort(key=lambda e: (e.done, -e.task.priority, e.task.sort_order, e.task.id or 0))
    return entries


def _daily_applies(
    task: Task, recurrence: Recurrence, *, start: datetime, events: Mapping[int, Event]
) -> bool:
    """日課が、start から始まる1日に対象だったか。

    Returns:
        その日にタスクがあり、有効期間とイベント期間にかかっていれば True。
    """
    end = start + timedelta(days=1)
    if task.created_at >= end:
        return False
    after_start = recurrence.active_from is None or recurrence.active_from < end
    before_end = recurrence.active_until is None or recurrence.active_until > start
    if not (after_start and before_end):
        return False
    if recurrence.event_id is None:
        return True
    event = events.get(recurrence.event_id)
    return event is not None and event.end_at > start and event.start_at < end


def _due_by_day(rows: Iterable[TodoRow]) -> dict[date, list[TodoRow]]:
    result: dict[date, list[TodoRow]] = {}
    for row in rows:
        day = due_day(row)
        if day is not None:
            result.setdefault(day, []).append(row)
    for day_rows in result.values():
        day_rows.sort(key=lambda r: (r.due or datetime.max, -r.task.priority, r.task.id or 0))
    return result


def build_month(year: int, month: int, inputs: CalendarInputs) -> MonthView:
    """月表示の内容を組み立てる。

    Args:
        year: 年。
        month: 月。
        inputs: カレンダーの組み立てに使う情報。

    Returns:
        月表示の内容。
    """
    due = _due_by_day(inputs.rows)
    weeks: list[list[DayCell]] = []
    bands: list[list[BandSegment]] = []
    for week in month_weeks(year, month):
        cells: list[DayCell] = []
        for day in week:
            dailies = daily_entries(day, inputs)
            cells.append(
                DayCell(
                    day,
                    in_month=day.month == month,
                    due=due.get(day, []),
                    daily_done=sum(1 for entry in dailies if entry.done),
                    daily_total=len(dailies),
                )
            )
        weeks.append(cells)
        bands.append(layout_week(inputs.shown_events, week))
    return MonthView(year, month, weeks, bands)


# --- 週表示 ---

ALL_DAY_DUE = time(23, 59)  # この時刻が期限のタスクは「その日のうち」とみなし、終日欄に出す
MIN_BLOCK = timedelta(minutes=30)  # 時間軸に置く項目の最小の長さ(短すぎて押せなくならないように)
_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class TimedBlock:
    """週表示の時間軸に置く項目1件(1日の中に収まる)。"""

    col: int  # 0=月曜 … 6=日曜
    start: datetime  # 表示上の開始
    end: datetime  # 表示上の終了(MIN_BLOCK 以上の長さにしてある)
    event: Event | None = None
    row: TodoRow | None = None  # タスクなら表示用の行
    lane: int = 0  # 重なりの中での列(0 が左)
    lanes: int = 1  # 重なりのまとまりの列数

    @property
    def is_event(self) -> bool:
        """イベントの項目か。

        Returns:
            イベントなら True、タスクなら False。
        """
        return self.event is not None


@dataclass(frozen=True, slots=True)
class WeekView:
    """週表示の内容。

    Attributes:
        days: 月曜〜日曜のマス。
        bands: 終日欄のイベントの帯。
        all_day: 曜日ごとの、終日欄に出す期限つきタスク。
        blocks: 時間軸に置く項目。
    """

    days: list[DayCell]  # 月曜〜日曜
    bands: list[BandSegment]  # 終日欄のイベントの帯
    all_day: list[list[TodoRow]]  # 曜日ごとの、終日欄に出す期限つきタスク
    blocks: list[TimedBlock]  # 時間軸に置く項目

    @property
    def first_day(self) -> date:
        """週の最初の日。

        Returns:
            月曜日。
        """
        return self.days[0].day

    @property
    def last_day(self) -> date:
        """週の最後の日。

        Returns:
            日曜日。
        """
        return self.days[-1].day


def week_start(day: date) -> date:
    """その日を含む週の月曜日を返す。

    Args:
        day: 日付。

    Returns:
        月曜日の日付。
    """
    return day - timedelta(days=day.weekday())


def is_timed_event(event: Event) -> bool:
    """週表示で時間軸に置くイベントか。1日の中で終わる短いものだけが対象。

    日をまたぐものと、ほぼ丸1日(23時間以上)のものは終日欄の帯にする。

    Args:
        event: 対象のイベント。

    Returns:
        時間軸に置くなら True。
    """
    first, last = event_days(event)
    return first == last and event.end_at - event.start_at < timedelta(hours=23)


def task_block(row: TodoRow) -> tuple[datetime, datetime] | None:
    """期限つきタスクを時間軸に置くときの(開始, 終了)を返す。

    期限ラベルを出すタスク(due_day が日付を返すもの)が対象。期限が 23:59 なら終日欄。
    同じ日の開始を持つなら開始から期限まで、そうでなければ期限の手前 MIN_BLOCK ぶん。

    Args:
        row: 対象の行。

    Returns:
        (開始, 終了)。終日欄に出すものは None。
    """
    day = due_day(row)
    due = row.due
    if day is None or due is None or due.time() == ALL_DAY_DUE:
        return None
    day_start = datetime.combine(day, time(0, 0))
    start = row.start if row.start is not None and day_start <= row.start < due else None
    if start is None:
        start = max(day_start, due - MIN_BLOCK)
    return _min_length(start, due, day_start)


def _min_length(start: datetime, end: datetime, day_start: datetime) -> tuple[datetime, datetime]:
    """MIN_BLOCK より短ければ伸ばす。日の終わりを越えるなら前へずらす。

    Returns:
        (開始, 終了)。
    """
    end = max(end, start + MIN_BLOCK)
    if end > day_start + _DAY:
        end = day_start + _DAY
        start = min(start, end - MIN_BLOCK)
    return start, end


def assign_lanes(blocks: list[TimedBlock]) -> list[TimedBlock]:
    """同じ日の中で重なる項目を左右の列に割り振る。

    重なりがつながっているまとまりごとに、空いている最も左の列へ入れ、
    まとまりの列数を lanes に入れる(描画では幅を lanes で割る)。

    Args:
        blocks: 割り振る項目。

    Returns:
        lane と lanes を入れた項目。
    """
    groups: list[list[TimedBlock]] = []  # 重なりがつながっているまとまり
    ordered = sorted(blocks, key=lambda b: (b.col, b.start, b.start - b.end))
    group_end = datetime.min
    for block in ordered:
        if groups and groups[-1][0].col == block.col and block.start < group_end:
            groups[-1].append(block)
            group_end = max(group_end, block.end)
        else:
            groups.append([block])
            group_end = block.end

    result: list[TimedBlock] = []
    for group in groups:
        lane_ends: list[datetime] = []
        placed: list[tuple[TimedBlock, int]] = []
        for block in group:
            lane = next((i for i, end in enumerate(lane_ends) if end <= block.start), None)
            if lane is None:
                lane = len(lane_ends)
                lane_ends.append(block.end)
            else:
                lane_ends[lane] = block.end
            placed.append((block, lane))
        lanes = len(lane_ends)
        result.extend(
            TimedBlock(b.col, b.start, b.end, b.event, b.row, lane, lanes) for b, lane in placed
        )
    return result


def build_week(first: date, inputs: CalendarInputs) -> WeekView:
    """週表示の内容を組み立てる。

    日をまたぐイベントは終日欄の帯、1日の中で終わるイベントは時間軸に置く。
    期限つきタスクは期限の時刻に置き、期限が 23:59 のものは終日欄に出す。

    Args:
        first: その週の月曜日。
        inputs: カレンダーの組み立てに使う情報。

    Returns:
        週表示の内容。
    """
    week = [first + timedelta(days=offset) for offset in range(7)]
    cells: list[DayCell] = []
    for day in week:
        dailies = daily_entries(day, inputs)
        done = sum(1 for entry in dailies if entry.done)
        cells.append(DayCell(day, in_month=True, due=[], daily_done=done, daily_total=len(dailies)))

    blocks: list[TimedBlock] = []
    band_events: list[Event] = []
    for event in inputs.shown_events:
        if not is_timed_event(event):
            band_events.append(event)
            continue
        day = event.start_at.date()
        if first <= day <= week[-1]:
            day_start = datetime.combine(day, time(0, 0))
            start, end = _min_length(event.start_at, event.end_at, day_start)
            blocks.append(TimedBlock((day - first).days, start, end, event=event))

    all_day: list[list[TodoRow]] = [[] for _ in week]
    for day, day_rows in _due_by_day(inputs.rows).items():
        if not first <= day <= week[-1]:
            continue
        col = (day - first).days
        for row in day_rows:
            span = task_block(row)
            if span is None:
                all_day[col].append(row)
            else:
                blocks.append(TimedBlock(col, span[0], span[1], row=row))

    return WeekView(cells, layout_week(band_events, week), all_day, assign_lanes(blocks))


def build_day(day: date, inputs: CalendarInputs) -> DayDetail:
    """日別一覧の内容を組み立てる。

    Args:
        day: 日付。
        inputs: カレンダーの組み立てに使う情報。

    Returns:
        その日のイベント、期限のタスク、日課。
    """
    return DayDetail(
        day,
        events_on(inputs.shown_events, day),
        _due_by_day(inputs.rows).get(day, []),
        daily_entries(day, inputs),
    )

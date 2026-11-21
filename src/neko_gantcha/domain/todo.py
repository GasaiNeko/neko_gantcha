"""ToDo リストのセクション分けと並び順。Qt には依存しない。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import EventRelation, Freq, TaskStatus
from neko_gantcha.domain.periods import current_period
from neko_gantcha.domain.schedule import effective_range, is_recurrence_active

if TYPE_CHECKING:
    from collections.abc import Iterable

    from neko_gantcha.domain.models import Category, Event, Recurrence, Task
    from neko_gantcha.domain.periods import Period


class Section(StrEnum):
    """ToDo のセクション。この順に並べる。"""

    DAILY = "daily"  # 今日の日課
    WEEKLY = "weekly"  # 今週の週課
    MONTHLY = "monthly"  # 今月の月課
    ACTIVE_EVENT = "active_event"  # 開催中イベントのタスク
    DATED = "dated"  # 期限つき
    STANDING = "standing"  # 恒常
    DONE = "done"  # 完了済み(繰り返し以外)


_RECURRING_SECTIONS = {
    Freq.DAILY: Section.DAILY,
    Freq.WEEKLY: Section.WEEKLY,
    Freq.MONTHLY: Section.MONTHLY,
}

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24

# (task_id, period_key) → その期間の達成回数
type Counts = Mapping[tuple[int, str], int]


@dataclass(frozen=True, slots=True)
class TodoRow:
    """ToDo などに出すタスク1件の表示用の情報。

    Attributes:
        task: タスク。
        start: 実効開始。
        due: 実効期限。繰り返しタスクでは次のリセット日時。
        event: 紐付くイベント。
        recurrence: 繰り返しの設定。
        period: 繰り返しタスクの現在の期間。
        count: 繰り返しタスクの、現在の期間の達成回数。
    """

    task: Task
    start: datetime | None  # 実効開始
    due: datetime | None  # 実効期限。繰り返しタスクでは次のリセット日時
    event: Event | None
    recurrence: Recurrence | None = None
    period: Period | None = None  # 繰り返しタスクの現在の期間
    count: int = 0  # 繰り返しタスクの、現在の期間の達成回数

    @property
    def is_recurring(self) -> bool:
        """繰り返しタスクか。

        Returns:
            繰り返しの設定があれば True。
        """
        return self.recurrence is not None

    @property
    def target(self) -> int | None:
        """回数管理の目標。

        Returns:
            目標の回数。None なら単純なチェックボックス。
        """
        return self.task.progress_target

    @property
    def current(self) -> int | None:
        """回数管理の現在の回数。

        Returns:
            繰り返しタスクなら今の期間の達成回数、それ以外は進捗。回数管理でなければ None。
        """
        if self.target is None:
            return None
        return self.count if self.is_recurring else (self.task.progress_current or 0)

    @property
    def done(self) -> bool:
        """達成済みか。

        Returns:
            繰り返しタスクなら今の期間の目標に届いたか、それ以外は完了しているか。
        """
        if self.is_recurring:
            return self.count >= (self.target or 1)
        return self.task.status is TaskStatus.DONE


@dataclass(frozen=True, slots=True)
class SectionData:
    """ToDo のセクション1つ分。

    Attributes:
        section: セクション。
        rows: 表示する行。
        total: 非表示の達成済みも含めた件数。
        done: そのうち達成済みの件数。
        children: 親タスク id → その直下に並べるサブタスク。このセクションの行の子孫だけが
            入る(孫は子の id で引く)。
    """

    section: Section
    rows: list[TodoRow]  # 表示する行
    total: int  # 非表示の達成済みも含めた件数
    done: int  # そのうち達成済みの件数
    # 親タスク id → その直下に並べるサブタスク。このセクションの行の子孫だけが入る
    # (孫は子の id で引く)
    children: dict[int, list[TodoRow]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TodoContext:
    """行を組み立てるのに使う、タスク以外の情報。

    Attributes:
        events: イベント id → イベント。
        recurrences: タスク id → 繰り返しの設定。
        categories: 分類 id → 分類。
        counts: (タスク id, period_key) → その期間の達成回数。
        now: 現在の日時。
    """

    events: Mapping[int, Event]
    recurrences: Mapping[int, Recurrence]
    categories: Mapping[int, Category]
    counts: Counts
    now: datetime


def build_row(task: Task, ctx: TodoContext) -> TodoRow:
    """タスク1件の表示用の情報をまとめる。

    Args:
        task: 対象のタスク。
        ctx: イベントや繰り返しなど、行の組み立てに使う情報。

    Returns:
        表示用の行。
    """
    event = ctx.events.get(task.event_id) if task.event_id is not None else None
    recurrence = ctx.recurrences.get(task.id) if task.id is not None else None
    if recurrence is None:
        start, due = effective_range(task, event)
        return TodoRow(task, start, due, event)

    rule = ctx.categories[task.category_id].reset_rule
    period = current_period(recurrence.freq, ctx.now, rule)
    if recurrence.event_id is not None:
        event = ctx.events.get(recurrence.event_id)
    due = period.end
    if event is not None and recurrence.event_id is not None:
        due = min(due, event.end_at)  # イベントが先に終わるなら、そこが締め切り
    return TodoRow(
        task,
        period.start,
        due,
        event,
        recurrence=recurrence,
        period=period,
        count=ctx.counts.get((task.saved_id, period.key), 0),
    )


def build_sections(
    tasks: Iterable[Task], ctx: TodoContext, *, show_done: bool = False
) -> list[SectionData]:
    """タスクをセクションに振り分け、表示順に並べて返す。空のセクションは返さない。

    繰り返しタスクは、達成済みでも自分のセクションに残す(show_done が偽なら隠す)。
    有効期間外やイベント期間外の繰り返しタスクは出さない。
    サブタスク(孫以下も同じ)は、親が一覧に出ていれば親の下(SectionData.children)に並べ、
    親が出ていなければ(完了済みで隠れているなど)自分の行として振り分ける。

    Args:
        tasks: 振り分けるタスク。
        ctx: イベントや繰り返しなど、行の組み立てに使う情報。
        show_done: 達成済みの繰り返しタスクも出すか。

    Returns:
        表示順のセクション。
    """
    buckets, nested = _distribute(list(tasks), ctx)
    result: list[SectionData] = []
    for section, rows in buckets.items():
        if not rows:
            continue
        shown = _sorted_rows(section, rows, show_done=show_done)
        done = sum(1 for row in rows if row.done)
        result.append(SectionData(section, shown, len(rows), done, _nest(shown, nested)))
    return result


def _distribute(
    tasks: list[Task], ctx: TodoContext
) -> tuple[dict[Section, list[TodoRow]], dict[int, list[TodoRow]]]:
    """タスクを、セクションごとの行と、親の下に入れるサブタスクの行に分ける。

    Returns:
        (セクション → 行, 親タスク id → サブタスクの行)。
    """
    shown_ids = {task.id for task in tasks}
    nested: dict[int, list[TodoRow]] = {}
    buckets: dict[Section, list[TodoRow]] = {section: [] for section in Section}
    for task in tasks:
        row = build_row(task, ctx)
        recurrence = row.recurrence
        if task.parent_task_id is not None and task.parent_task_id in shown_ids:
            nested.setdefault(task.parent_task_id, []).append(row)
        elif recurrence is None:
            buckets[_section_of(row, ctx.now)].append(row)
        elif _is_active(recurrence, ctx):
            buckets[_RECURRING_SECTIONS[recurrence.freq]].append(row)
    return buckets, nested


def _is_active(recurrence: Recurrence, ctx: TodoContext) -> bool:
    """繰り返しタスクが今の時点で ToDo に出る対象か。

    Returns:
        有効期間の中にあれば True。
    """
    event_id = recurrence.event_id
    bound_event = None if event_id is None else ctx.events.get(event_id)
    return is_recurrence_active(recurrence, ctx.now, bound_event)


def _sorted_rows(section: Section, rows: list[TodoRow], *, show_done: bool) -> list[TodoRow]:
    """セクションの中の行を表示順に並べる。

    Returns:
        並べた行。繰り返しのセクションで show_done が偽なら、達成済みを除く。
    """
    if section in _RECURRING_SECTIONS.values():
        kept = rows if show_done else [row for row in rows if not row.done]
        return sorted(kept, key=_by_done_then_priority)
    if section is Section.STANDING:
        return sorted(rows, key=_by_priority)
    if section is Section.DONE:
        return sorted(rows, key=_by_completed, reverse=True)
    return sorted(rows, key=_by_due)


def _nest(rows: list[TodoRow], nested: dict[int, list[TodoRow]]) -> dict[int, list[TodoRow]]:
    """セクションの行の下に入れる、子・孫…の行を集める。

    Returns:
        親タスク id → 直下に並べるサブタスクの行。
    """
    children: dict[int, list[TodoRow]] = {}
    pending = [row.task.saved_id for row in rows]
    while pending:
        parent_id = pending.pop()
        if parent_id in nested and parent_id not in children:
            children[parent_id] = sorted(nested[parent_id], key=_by_position)
            pending.extend(child.task.saved_id for child in children[parent_id])
    return children


def _section_of(row: TodoRow, now: datetime) -> Section:
    task = row.task
    if task.status is TaskStatus.DONE:
        return Section.DONE
    if (
        row.event is not None
        and task.event_relation is EventRelation.DURING
        and row.event.start_at <= now < row.event.end_at
    ):
        return Section.ACTIVE_EVENT
    if row.start is not None or row.due is not None:
        return Section.DATED
    return Section.STANDING


def _by_due(row: TodoRow) -> tuple[datetime, datetime, int, int, int]:
    # 期限が近い順。期限なし(開始だけ持つ)は最後
    return (
        row.due or datetime.max,
        row.start or datetime.max,
        -row.task.priority,
        row.task.sort_order,
        row.task.id or 0,
    )


def _by_position(row: TodoRow) -> tuple[int, int]:
    return (row.task.sort_order, row.task.id or 0)


def _by_priority(row: TodoRow) -> tuple[int, int, int]:
    return (-row.task.priority, row.task.sort_order, row.task.id or 0)


def _by_done_then_priority(row: TodoRow) -> tuple[bool, int, int, int]:
    return (row.done, *_by_priority(row))


def _by_completed(row: TodoRow) -> tuple[datetime, int]:
    return (row.task.completed_at or datetime.min, row.task.id or 0)


def format_span(seconds: float) -> str:
    """時間の長さを表示用の文字列にする。

    Args:
        seconds: 長さ(秒)。

    Returns:
        `59分`、`3時間`、`2日` のような文字列。
    """
    minutes = max(0, int(seconds // MINUTES_PER_HOUR))
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes}分"
    hours = minutes // MINUTES_PER_HOUR
    if hours < HOURS_PER_DAY:
        return f"{hours}時間"
    return f"{hours // HOURS_PER_DAY}日"


def format_remaining(due: datetime | None, now: datetime) -> str:
    """期限までの残り時間を表示用の文字列にする。

    Args:
        due: 期限。
        now: 現在の日時。

    Returns:
        `あと3時間` のような文字列。期限を過ぎていれば `期限切れ`、期限がなければ空文字。
    """
    if due is None:
        return ""
    seconds = (due - now).total_seconds()
    if seconds < 0:
        return "期限切れ"
    return f"あと{format_span(seconds)}"


def format_row_remaining(row: TodoRow, now: datetime) -> str:
    """行の残り時間を表示用の文字列にする。

    Args:
        row: 対象の行。
        now: 現在の日時。

    Returns:
        繰り返しタスクは `リセットまで3時間` の形、それ以外は format_remaining と同じ。
    """
    if row.is_recurring and row.due is not None:
        if row.period is not None and row.due < row.period.end:
            return f"終了まで{format_span((row.due - now).total_seconds())}"
        return f"リセットまで{format_span((row.due - now).total_seconds())}"
    return format_remaining(row.due, now)


def format_event_status(event: Event, now: datetime) -> str:
    """イベントの開催状況を表示用の文字列にする。

    Args:
        event: 対象のイベント。
        now: 現在の日時。

    Returns:
        `開催前(開始まで2日)`、`開催中(終了まで3時間)`、`終了` のいずれか。
    """
    if now < event.start_at:
        return f"開催前(開始まで{format_span((event.start_at - now).total_seconds())})"
    if now < event.end_at:
        return f"開催中(終了まで{format_span((event.end_at - now).total_seconds())})"
    return "終了"

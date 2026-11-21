"""週表示カレンダー(フェーズ9)のテスト。"""

from dataclasses import replace
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from conftest import must

from neko_gantcha.domain.calendar import (
    MIN_BLOCK,
    CalendarInputs,
    TimedBlock,
    assign_lanes,
    build_week,
    is_timed_event,
    task_block,
    week_start,
)
from neko_gantcha.domain.models import Category, Event, EventRelation, Freq, Recurrence, Task
from neko_gantcha.domain.todo import TodoContext, build_row

if TYPE_CHECKING:
    from collections.abc import Mapping

    from neko_gantcha.domain.todo import TodoRow


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-18T12:00")  # 水曜
MONDAY = date(2026, 11, 16)
CATEGORIES = {1: Category(id=1, name="ソシャゲA", sort_order=0, daily_reset_time=time(5, 0))}


def make_task(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-01T09:00")), **overrides
    )


def row_of(
    task: Task,
    events: Mapping[int, Event] | None = None,
    recurrences: Mapping[int, Recurrence] | None = None,
) -> TodoRow:
    return build_row(task, TodoContext(events or {}, recurrences or {}, CATEGORIES, {}, NOW))


def event(event_id: int, start: str, end: str, *, name: str = "イベント") -> Event:
    return Event(id=event_id, category_id=1, name=name, start_at=dt(start), end_at=dt(end))


# --- 週の区切り ---


def test_week_start_is_monday_across_month_and_year() -> None:
    assert week_start(date(2026, 11, 16)) == MONDAY
    assert week_start(date(2026, 11, 22)) == MONDAY  # 日曜は前の月曜の週
    assert week_start(date(2026, 12, 2)) == date(2026, 11, 30)  # 月またぎ
    assert week_start(date(2027, 1, 1)) == date(2026, 12, 28)  # 年またぎ


# --- 時間軸に置くか ---


def test_is_timed_event() -> None:
    assert is_timed_event(event(1, "2026-11-18T19:00", "2026-11-18T21:00"))
    assert not is_timed_event(event(2, "2026-11-18T19:00", "2026-11-19T01:00"))  # 日をまたぐ
    assert not is_timed_event(event(3, "2026-11-18T00:00", "2026-11-18T23:59"))  # ほぼ丸1日
    # 0:00 ちょうどに終わるものは前日まで(1日の中)とみなす
    assert is_timed_event(event(4, "2026-11-18T20:00", "2026-11-19T00:00"))


def test_task_block_rules() -> None:
    # 期限 23:59 は終日欄
    assert task_block(row_of(make_task(1, due_at=dt("2026-11-18T23:59")))) is None
    # 期限だけなら手前 30 分
    assert task_block(row_of(make_task(2, due_at=dt("2026-11-18T18:00")))) == (
        dt("2026-11-18T17:30"),
        dt("2026-11-18T18:00"),
    )
    # 同じ日の開始があれば開始から
    span = task_block(
        row_of(make_task(3, start_at=dt("2026-11-18T09:00"), due_at=dt("2026-11-18T12:00")))
    )
    assert span == (dt("2026-11-18T09:00"), dt("2026-11-18T12:00"))
    # 開始が前の日なら期限の手前だけ
    span = task_block(
        row_of(make_task(4, start_at=dt("2026-11-17T09:00"), due_at=dt("2026-11-18T12:00")))
    )
    assert span == (dt("2026-11-18T11:30"), dt("2026-11-18T12:00"))
    # 0:10 期限は日の始まりより前へ出ない(最小の長さは保つ)
    assert task_block(row_of(make_task(5, due_at=dt("2026-11-18T00:10")))) == (
        dt("2026-11-18T00:00"),
        dt("2026-11-18T00:30"),
    )
    # 期限のない・繰り返しのタスクは出さない
    assert task_block(row_of(make_task(6))) is None
    daily = make_task(7)
    assert task_block(row_of(daily, recurrences={7: Recurrence(7, Freq.DAILY)})) is None


def test_task_block_for_before_event_task_uses_event_start() -> None:
    raid = event(9, "2026-11-20T19:00", "2026-11-27T23:59")
    task = make_task(1, event_id=9, event_relation=EventRelation.BEFORE)
    assert task_block(row_of(task, events={9: raid})) == (
        dt("2026-11-20T18:30"),
        dt("2026-11-20T19:00"),
    )


# --- 重なりの列分け ---


def block(col: int, start: str, end: str) -> TimedBlock:
    return TimedBlock(col, dt(start), dt(end))


def test_assign_lanes_splits_only_overlapping_groups() -> None:
    blocks = [
        block(2, "2026-11-18T09:00", "2026-11-18T11:00"),
        block(2, "2026-11-18T10:00", "2026-11-18T10:30"),
        block(2, "2026-11-18T10:30", "2026-11-18T12:00"),  # 2件目の後なので同じ列に入る
        block(2, "2026-11-18T13:00", "2026-11-18T14:00"),  # 離れているので単独
        block(3, "2026-11-19T09:00", "2026-11-19T10:00"),  # 別の日
    ]
    laid = {(b.col, b.start.time()): (b.lane, b.lanes) for b in assign_lanes(blocks)}
    assert laid[2, time(9, 0)] == (0, 2)
    assert laid[2, time(10, 0)] == (1, 2)
    assert laid[2, time(10, 30)] == (1, 2)
    assert laid[2, time(13, 0)] == (0, 1)
    assert laid[3, time(9, 0)] == (0, 1)


def test_assign_lanes_touching_blocks_do_not_overlap() -> None:
    blocks = [
        block(0, "2026-11-16T09:00", "2026-11-16T10:00"),
        block(0, "2026-11-16T10:00", "2026-11-16T11:00"),
    ]
    assert [(b.lane, b.lanes) for b in assign_lanes(blocks)] == [(0, 1), (0, 1)]


# --- 週の組み立て ---


def test_build_week_splits_all_day_and_timed() -> None:
    battle = event(1, "2026-11-20T19:00", "2026-11-27T23:59", name="協力バトル")
    raid = event(2, "2026-11-18T21:00", "2026-11-18T22:00", name="共闘")
    last_week = event(3, "2026-11-10T21:00", "2026-11-10T22:00", name="先週")
    tasks = [
        make_task(10, "買い物", due_at=dt("2026-11-18T18:00")),
        make_task(11, "レポート", due_at=dt("2026-11-19T23:59")),
        make_task(12, "編成", event_id=1, event_relation=EventRelation.BEFORE),
        make_task(13, "報酬交換", event_id=1, event_relation=EventRelation.DURING),  # 帯で表す
        make_task(14, "来週", due_at=dt("2026-11-24T10:00")),
        make_task(15, "日課"),
    ]
    events = {e.saved_id: e for e in (battle, raid, last_week)}
    recurrences = {15: Recurrence(15, Freq.DAILY)}
    rows = [build_row(t, TodoContext(events, recurrences, CATEGORIES, {}, NOW)) for t in tasks]
    counts = {(15, "2026-11-17"): 1}
    view = build_week(
        MONDAY, CalendarInputs(rows, list(events.values()), events, CATEGORIES, counts, NOW)
    )

    assert [cell.day for cell in view.days] == [date(2026, 11, d) for d in range(16, 23)]
    assert view.first_day == MONDAY
    assert view.last_day == date(2026, 11, 22)
    # 帯は日をまたぐイベントだけ(金曜〜日曜)
    assert [(s.event.name, s.first_col, s.last_col) for s in view.bands] == [("協力バトル", 4, 6)]
    # 終日欄: 23:59 期限のタスク
    assert [[r.task.title for r in day] for day in view.all_day] == [
        [],
        [],
        [],
        ["レポート"],
        [],
        [],
        [],
    ]
    timed = sorted(
        (b.col, must(b.event).name if b.is_event else must(b.row).task.title) for b in view.blocks
    )
    assert timed == [(2, "共闘"), (2, "買い物"), (4, "編成")]
    # 日課の達成数は過去と当日だけ
    assert [(c.daily_done, c.daily_total) for c in view.days] == [
        (0, 1),
        (1, 1),
        (0, 1),
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
    ]


def test_build_week_short_event_gets_minimum_length() -> None:
    flash = event(1, "2026-11-17T12:00", "2026-11-17T12:05")
    view = build_week(MONDAY, CalendarInputs([], [flash], {1: flash}, CATEGORIES, {}, NOW))
    (only,) = view.blocks
    assert only.col == 1
    assert only.end - only.start == MIN_BLOCK

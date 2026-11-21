"""ガントチャートの行と期間の計算のテスト。"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest
from conftest import must

from neko_gantcha.domain.gantt import GanttInputs, RowKind, bar_range, build_gantt, row_index
from neko_gantcha.domain.models import (
    Category,
    Event,
    EventRelation,
    Freq,
    Recurrence,
    Task,
    TaskStatus,
)
from neko_gantcha.domain.todo import TodoContext, build_row
from neko_gantcha.services.store import VIEW_GANTT, ItemKind, ItemRef

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from neko_gantcha.domain.gantt import GanttData
    from neko_gantcha.domain.todo import TodoRow
    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {
    1: Category(id=1, name="ソシャゲA", sort_order=0, daily_reset_time=time(5, 0)),
    2: Category(id=2, name="ソシャゲB", sort_order=1),
    3: Category(id=3, name="日常", sort_order=2),
}


def make_event(
    event_id: int, start: str, end: str, *, name: str = "イベント", category_id: int = 1
) -> Event:
    return Event(
        id=event_id, category_id=category_id, name=name, start_at=dt(start), end_at=dt(end)
    )


def make_task(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-10T09:00")), **overrides
    )


def row_of(
    task: Task,
    events: Mapping[int, Event] | None = None,
    recurrences: Mapping[int, Recurrence] | None = None,
) -> TodoRow:
    return build_row(task, TodoContext(events or {}, recurrences or {}, CATEGORIES, {}, NOW))


def gantt_of(
    tasks: Iterable[Task],
    events: Sequence[Event] = (),
    *,
    show_done: bool = False,
    recurrences: Mapping[int, Recurrence] | None = None,
) -> GanttData:
    by_id = {event.saved_id: event for event in events}
    rows = [row_of(task, by_id, recurrences) for task in tasks]
    return build_gantt(GanttInputs(rows, list(events), CATEGORIES, NOW, show_done=show_done))


def labels(data: GanttData) -> list[tuple[RowKind, str]]:
    return [(row.kind, row.label) for row in data.rows]


# --- 帯の期間 ---


def test_bar_range_uses_start_and_due() -> None:
    task = make_task(1, start_at=dt("2026-11-12T10:00"), due_at=dt("2026-11-20T23:59"))
    assert bar_range(row_of(task)) == (dt("2026-11-12T10:00"), dt("2026-11-20T23:59"), False)


def test_bar_range_due_only_starts_at_creation() -> None:
    task = make_task(1, due_at=dt("2026-11-20T23:59"))
    assert bar_range(row_of(task)) == (dt("2026-11-10T09:00"), dt("2026-11-20T23:59"), False)


def test_bar_range_due_before_creation_is_one_day() -> None:
    # 過去の期限で後から登録したタスクも、1日ぶんの帯にする
    task = make_task(1, due_at=dt("2026-11-05T12:00"))
    assert bar_range(row_of(task)) == (dt("2026-11-04T12:00"), dt("2026-11-05T12:00"), False)


def test_bar_range_start_after_due_is_clamped() -> None:
    task = make_task(1, start_at=dt("2026-11-22T00:00"), due_at=dt("2026-11-20T00:00"))
    start, end, _ = must(bar_range(row_of(task)))
    assert end == dt("2026-11-20T00:00")
    assert start == end - timedelta(days=1)


def test_bar_range_start_only_is_open_ended() -> None:
    task = make_task(1, start_at=dt("2026-11-18T00:00"))
    assert bar_range(row_of(task)) == (dt("2026-11-18T00:00"), dt("2026-11-19T00:00"), True)


def test_bar_range_skips_standing_and_recurring_tasks() -> None:
    assert bar_range(row_of(make_task(1))) is None
    recurring = make_task(2, due_at=dt("2026-11-20T00:00"))
    recurrences = {2: Recurrence(task_id=2, freq=Freq.DAILY)}
    assert bar_range(row_of(recurring, recurrences=recurrences)) is None


def test_bar_range_follows_event_period() -> None:
    event = make_event(10, "2026-11-20T19:00", "2026-11-27T23:59")
    during = make_task(1, event_id=10, event_relation=EventRelation.DURING)
    before = make_task(2, event_id=10, event_relation=EventRelation.BEFORE)
    events = {10: event}
    assert must(bar_range(row_of(during, events)))[:2] == (event.start_at, event.end_at)
    assert must(bar_range(row_of(before, events)))[1] == event.start_at


# --- 行の並び ---


def test_rows_are_grouped_by_category_then_event() -> None:
    raid = make_event(10, "2026-11-20T19:00", "2026-11-27T23:59", name="協力バトル")
    scenario = make_event(11, "2026-11-09T17:00", "2026-11-17T20:59", name="シナリオ")
    pickup = make_event(
        12, "2026-11-13T15:00", "2026-11-24T14:59", name="ピックアップ", category_id=2
    )
    tasks = [
        make_task(1, "報酬交換", event_id=10, event_relation=EventRelation.DURING),
        make_task(2, "編成", event_id=10, event_relation=EventRelation.BEFORE),
        make_task(3, "ストーリー", event_id=11, event_relation=EventRelation.DURING),
        make_task(4, "買い物", category_id=3, due_at=dt("2026-11-18T18:00")),
        make_task(5, "武器", category_id=1),  # 恒常タスクは出さない
    ]
    data = gantt_of(tasks, [raid, scenario, pickup])
    assert labels(data) == [
        (RowKind.CATEGORY, "ソシャゲA"),
        (RowKind.EVENT, "シナリオ"),
        (RowKind.TASK, "ストーリー"),
        (RowKind.EVENT, "協力バトル"),
        (RowKind.TASK, "編成"),  # 準備(イベントまで)が先
        (RowKind.TASK, "報酬交換"),
        (RowKind.CATEGORY, "ソシャゲB"),
        (RowKind.EVENT, "ピックアップ"),
        (RowKind.CATEGORY, "日常"),
        (RowKind.GROUP, "イベントなし"),
        (RowKind.TASK, "買い物"),
    ]
    assert [row.depth for row in data.rows[:3]] == [0, 1, 2]
    before = data.rows[4]
    assert before.relation is EventRelation.BEFORE
    assert before.end == raid.start_at


def test_loose_tasks_follow_event_rows_in_same_category() -> None:
    event = make_event(10, "2026-11-20T19:00", "2026-11-27T23:59", name="協力バトル")
    tasks = [
        make_task(1, "後", due_at=dt("2026-11-25T00:00")),
        make_task(2, "先", start_at=dt("2026-11-11T00:00"), due_at=dt("2026-11-25T00:00")),
    ]
    data = gantt_of(tasks, [event])
    assert labels(data)[1:] == [
        (RowKind.EVENT, "協力バトル"),
        (RowKind.GROUP, "イベントなし"),
        (RowKind.TASK, "後"),  # 作成日時 11/10 から
        (RowKind.TASK, "先"),
    ]


def test_finished_event_is_hidden_unless_tasks_remain_or_show_done() -> None:
    old = make_event(10, "2026-10-28T15:00", "2026-11-05T14:59", name="終了")
    assert gantt_of([], [old]).rows == []
    remaining = make_task(1, "取り残し", event_id=10, event_relation=EventRelation.DURING)
    data = gantt_of([remaining], [old])
    assert labels(data)[1:] == [(RowKind.EVENT, "終了"), (RowKind.TASK, "取り残し")]
    assert data.rows[1].done
    assert labels(gantt_of([], [old], show_done=True))[1:] == [(RowKind.EVENT, "終了")]


def test_task_of_hidden_event_goes_to_loose_group() -> None:
    # 絞り込みでイベントが外れたときは、タスクを「イベントなし」に出す
    task = make_task(1, "報酬交換", event_id=10, event_relation=EventRelation.DURING)
    event = make_event(10, "2026-11-20T19:00", "2026-11-27T23:59")
    rows = [row_of(task, {10: event})]
    data = build_gantt(GanttInputs(rows, [], CATEGORIES, NOW))
    assert labels(data)[1:] == [(RowKind.GROUP, "イベントなし"), (RowKind.TASK, "報酬交換")]


def test_task_row_progress_and_done() -> None:
    done = make_task(1, "済", due_at=dt("2026-11-20T00:00"), status=TaskStatus.DONE)
    counted = make_task(
        2, "回数", due_at=dt("2026-11-20T00:00"), progress_target=4, progress_current=1
    )
    rows = {row.label: row for row in gantt_of([done, counted]).rows}
    assert rows["済"].done
    assert rows["回数"].progress == pytest.approx(0.25)
    assert not rows["回数"].done


# --- 時間軸 ---


def test_time_range_covers_bars_and_today_with_padding() -> None:
    event = make_event(10, "2026-11-01T00:00", "2026-12-31T23:59")
    data = gantt_of([], [event])
    assert data.start == date(2026, 10, 29)
    assert data.end == date(2027, 1, 7)
    assert data.days == (data.end - data.start).days + 1


def test_time_range_has_minimum_span() -> None:
    data = gantt_of([])
    assert data.rows == []
    assert data.start == date(2026, 11, 13)
    assert data.days == 42


def test_row_index_finds_tasks_and_events() -> None:
    event = make_event(10, "2026-11-20T19:00", "2026-11-27T23:59")
    task = make_task(1, event_id=10, event_relation=EventRelation.DURING)
    data = gantt_of([task], [event])
    assert row_index(data, event_id=10) == 1
    assert row_index(data, task_id=1) == 2
    assert row_index(data, task_id=99) == -1


# --- AppStore ---


def test_store_gantt_applies_category_filter(store: AppStore) -> None:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    store.add_task("報酬交換", 1, event_id=raid.id, event_relation=EventRelation.DURING)
    store.add_task("買い物", 3, due_at=dt("2026-11-18T18:00"))
    store.add_task("日課", 1, freq=Freq.DAILY)
    names = [row.label for row in store.gantt().rows]
    assert "協力バトル" in names
    assert "報酬交換" in names
    assert "買い物" in names
    assert "日課" not in names
    store.set_filter(category_id=3)
    names = [row.label for row in store.gantt().rows]
    assert "協力バトル" not in names
    assert "買い物" in names


def test_store_shown_in_gantt(store: AppStore) -> None:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    dated = store.add_task("買い物", 3, due_at=dt("2026-11-18T18:00"))
    standing = store.add_task("武器", 1)
    daily = store.add_task("日課", 1, freq=Freq.DAILY)
    assert store.shown_in_gantt(ItemRef(ItemKind.EVENT, raid.saved_id))
    assert store.shown_in_gantt(ItemRef(ItemKind.TASK, dated.saved_id))
    assert not store.shown_in_gantt(ItemRef(ItemKind.TASK, standing.saved_id))
    assert not store.shown_in_gantt(ItemRef(ItemKind.TASK, daily.saved_id))
    assert not store.shown_in_gantt(None)
    assert VIEW_GANTT == "gantt"

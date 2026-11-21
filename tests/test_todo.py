"""ToDo のセクション分けと残り時間表示のテスト。"""

from dataclasses import replace
from datetime import datetime, time
from typing import TYPE_CHECKING

from conftest import must

from neko_gantcha.domain.models import (
    Category,
    Event,
    EventRelation,
    Freq,
    Recurrence,
    Task,
    TaskStatus,
)
from neko_gantcha.domain.todo import (
    Section,
    TodoContext,
    build_row,
    build_sections,
    format_event_status,
    format_remaining,
    format_row_remaining,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from neko_gantcha.domain.todo import Counts, SectionData


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {
    1: Category(id=1, name="ソシャゲA", daily_reset_time=time(5, 0)),
    3: Category(id=3, name="日常"),
}
EVENT = Event(
    id=1,
    category_id=1,
    name="協力バトル",
    start_at=dt("2026-11-15T19:00"),
    end_at=dt("2026-11-22T23:59"),
)
FUTURE_EVENT = Event(
    id=2,
    category_id=1,
    name="シナリオ",
    start_at=dt("2026-11-30T17:00"),
    end_at=dt("2026-12-07T20:59"),
)
EVENTS = {1: EVENT, 2: FUTURE_EVENT}

_next_id = iter(range(1, 1000))


def make_task(title: str, **overrides: object) -> Task:
    return replace(Task(id=next(_next_id), category_id=1, title=title, created_at=NOW), **overrides)


def sections_of(
    tasks: Iterable[Task],
    recurrences: Mapping[int, Recurrence] | None = None,
    counts: Counts | None = None,
    *,
    now: datetime = NOW,
    show_done: bool = False,
) -> list[SectionData]:
    return build_sections(
        tasks,
        TodoContext(EVENTS, recurrences or {}, CATEGORIES, counts or {}, now),
        show_done=show_done,
    )


def titles(sections: list[SectionData], section: Section) -> list[str]:
    for data in sections:
        if data.section is section:
            return [row.task.title for row in data.rows]
    return []


def find(sections: list[SectionData], section: Section) -> SectionData:
    return next(data for data in sections if data.section is section)


# --- 単発タスク ---


def test_tasks_are_split_into_sections() -> None:
    tasks = [
        make_task("恒常"),
        make_task("期限つき", due_at=dt("2026-11-20T00:00")),
        make_task("開始だけ", start_at=dt("2026-11-18T00:00")),
        make_task("開催中", event_id=1, event_relation=EventRelation.DURING),
        make_task("準備", event_id=2, event_relation=EventRelation.BEFORE),
        make_task("開催前のイベント中タスク", event_id=2, event_relation=EventRelation.DURING),
        make_task("完了", status=TaskStatus.DONE, completed_at=NOW),
    ]
    sections = sections_of(tasks)

    assert [data.section for data in sections] == [
        Section.ACTIVE_EVENT,
        Section.DATED,
        Section.STANDING,
        Section.DONE,
    ]
    assert titles(sections, Section.ACTIVE_EVENT) == ["開催中"]
    # 期限が近い順。期限のないものは最後
    assert titles(sections, Section.DATED) == [
        "期限つき",
        "準備",
        "開催前のイベント中タスク",
        "開始だけ",
    ]
    assert titles(sections, Section.STANDING) == ["恒常"]
    assert titles(sections, Section.DONE) == ["完了"]


def test_event_rows_carry_effective_dates() -> None:
    tasks = [
        make_task("開催中", event_id=1, event_relation=EventRelation.DURING),
        make_task("準備", event_id=2, event_relation=EventRelation.BEFORE),
    ]
    sections = sections_of(tasks)
    active = find(sections, Section.ACTIVE_EVENT).rows[0]
    assert (active.start, active.due, active.event) == (EVENT.start_at, EVENT.end_at, EVENT)
    assert find(sections, Section.DATED).rows[0].due == FUTURE_EVENT.start_at


def test_empty_sections_are_omitted() -> None:
    assert sections_of([]) == []
    assert [data.section for data in sections_of([make_task("恒常")])] == [Section.STANDING]


def test_standing_sorted_by_priority_then_order() -> None:
    tasks = [
        make_task("通常2", sort_order=2),
        make_task("通常1", sort_order=1),
        make_task("高", priority=1, sort_order=9),
    ]
    assert titles(sections_of(tasks), Section.STANDING) == ["高", "通常1", "通常2"]


def test_done_sorted_by_latest_completion_first() -> None:
    tasks = [
        make_task("先に完了", status=TaskStatus.DONE, completed_at=dt("2026-11-15T10:00")),
        make_task("後に完了", status=TaskStatus.DONE, completed_at=dt("2026-11-16T10:00")),
    ]
    assert titles(sections_of(tasks), Section.DONE) == ["後に完了", "先に完了"]


def test_overdue_task_stays_in_dated_section() -> None:
    tasks = [make_task("期限切れ", due_at=dt("2026-11-10T00:00"))]
    assert titles(sections_of(tasks), Section.DATED) == ["期限切れ"]


# --- 繰り返しタスク ---


def recurring(title: str, freq: Freq, **overrides: object) -> tuple[Task, Recurrence]:
    task = make_task(title, **overrides)
    return task, Recurrence(task.saved_id, freq, event_id=task.event_id)


def test_recurring_tasks_go_to_their_sections() -> None:
    daily, daily_rule = recurring("デイリーミッション", Freq.DAILY)
    weekly, weekly_rule = recurring("週課", Freq.WEEKLY)
    monthly, monthly_rule = recurring("月初の交換", Freq.MONTHLY)
    recurrences = {r.task_id: r for r in (daily_rule, weekly_rule, monthly_rule)}

    sections = sections_of([monthly, weekly, daily], recurrences)

    assert [data.section for data in sections] == [Section.DAILY, Section.WEEKLY, Section.MONTHLY]
    row = find(sections, Section.DAILY).rows[0]
    assert must(row.period).key == "2026-11-16"
    assert row.due == dt("2026-11-17T05:00")  # 次のリセット
    assert must(find(sections, Section.WEEKLY).rows[0].period).key == "2026-W47"
    assert must(find(sections, Section.MONTHLY).rows[0].period).key == "2026-11"


def test_daily_done_state_follows_current_period() -> None:
    task, rule = recurring("デイリーミッション", Freq.DAILY)
    recurrences = {task.saved_id: rule}
    counts = {(task.saved_id, "2026-11-16"): 1}

    # リセット前(11/17 4:59)はまだ 11/16 の日課なので達成済み
    before = find(
        sections_of([task], recurrences, counts, now=dt("2026-11-17T04:59"), show_done=True),
        Section.DAILY,
    )
    assert before.rows[0].done
    assert (before.done, before.total) == (1, 1)

    # リセット後は未達成に戻る
    after = find(
        sections_of([task], recurrences, counts, now=dt("2026-11-17T05:00")), Section.DAILY
    )
    assert not after.rows[0].done
    assert after.rows[0].count == 0
    assert (after.done, after.total) == (0, 1)


def test_done_recurring_rows_are_hidden_but_counted() -> None:
    done_task, done_rule = recurring("済んだ日課", Freq.DAILY)
    open_task, open_rule = recurring("残りの日課", Freq.DAILY)
    recurrences = {done_task.saved_id: done_rule, open_task.saved_id: open_rule}
    counts = {(done_task.saved_id, "2026-11-16"): 1}

    hidden = find(sections_of([done_task, open_task], recurrences, counts), Section.DAILY)
    assert [row.task.title for row in hidden.rows] == ["残りの日課"]
    assert (hidden.done, hidden.total) == (1, 2)

    shown = find(
        sections_of([done_task, open_task], recurrences, counts, show_done=True), Section.DAILY
    )
    assert [row.task.title for row in shown.rows] == ["残りの日課", "済んだ日課"]  # 未達成が先


def test_recurring_with_count_target() -> None:
    task, rule = recurring("1日3回", Freq.DAILY, progress_target=3)
    recurrences = {task.saved_id: rule}

    partial = build_row(
        task, TodoContext(EVENTS, recurrences, CATEGORIES, {(task.saved_id, "2026-11-16"): 2}, NOW)
    )
    assert (partial.current, partial.target, partial.done) == (2, 3, False)

    full = build_row(
        task, TodoContext(EVENTS, recurrences, CATEGORIES, {(task.saved_id, "2026-11-16"): 3}, NOW)
    )
    assert full.done


def test_event_bound_daily_appears_only_during_event() -> None:
    task, rule = recurring("イベント日課", Freq.DAILY, event_id=1)
    recurrences = {task.saved_id: rule}

    assert sections_of([task], recurrences, now=dt("2026-11-15T18:59")) == []
    during = sections_of([task], recurrences, now=dt("2026-11-16T12:00"))
    assert titles(during, Section.DAILY) == ["イベント日課"]
    assert find(during, Section.DAILY).rows[0].event == EVENT
    assert sections_of([task], recurrences, now=dt("2026-11-22T23:59")) == []


def test_event_bound_daily_is_due_at_event_end_on_last_day() -> None:
    task, rule = recurring("イベント日課", Freq.DAILY, event_id=1)
    now = dt("2026-11-22T20:00")
    row = build_row(task, TodoContext(EVENTS, {task.saved_id: rule}, CATEGORIES, {}, now))
    assert row.due == EVENT.end_at  # リセット(翌 5:00)より先にイベントが終わる
    assert format_row_remaining(row, now) == "終了まで3時間"


def test_recurrence_outside_active_window_is_hidden() -> None:
    task = make_task("期間限定の週課")
    rule = Recurrence(task.saved_id, Freq.WEEKLY, active_until=dt("2026-11-16T00:00"))
    assert sections_of([task], {task.saved_id: rule}) == []


def test_reset_time_differs_by_category() -> None:
    game, game_rule = recurring("ゲームの日課", Freq.DAILY)
    life, life_rule = recurring("生活の日課", Freq.DAILY, category_id=3)
    recurrences = {game.saved_id: game_rule, life.saved_id: life_rule}
    now = dt("2026-11-17T03:00")  # 日常(0:00)は 11/17、ソシャゲA(5:00)はまだ 11/16

    rows = {
        row.task.title: row
        for row in find(sections_of([game, life], recurrences, now=now), Section.DAILY).rows
    }
    assert must(rows["ゲームの日課"].period).key == "2026-11-16"
    assert must(rows["生活の日課"].period).key == "2026-11-17"


# --- 表示文字列 ---


def test_format_remaining() -> None:
    assert not format_remaining(None, NOW)
    assert format_remaining(dt("2026-11-16T11:59"), NOW) == "期限切れ"
    assert format_remaining(dt("2026-11-16T12:00"), NOW) == "あと0分"
    assert format_remaining(dt("2026-11-16T12:59"), NOW) == "あと59分"
    assert format_remaining(dt("2026-11-16T13:00"), NOW) == "あと1時間"
    assert format_remaining(dt("2026-11-17T11:59"), NOW) == "あと23時間"
    assert format_remaining(dt("2026-11-17T12:00"), NOW) == "あと1日"
    assert format_remaining(dt("2026-11-18T23:00"), NOW) == "あと2日"


def test_format_row_remaining_for_recurring() -> None:
    task, rule = recurring("デイリーミッション", Freq.DAILY)
    now = dt("2026-11-17T02:00")
    row = build_row(task, TodoContext(EVENTS, {task.saved_id: rule}, CATEGORIES, {}, now))
    assert format_row_remaining(row, now) == "リセットまで3時間"

    plain = build_row(
        make_task("期限つき", due_at=dt("2026-11-18T12:00")),
        TodoContext({}, {}, CATEGORIES, {}, NOW),
    )
    assert format_row_remaining(plain, NOW) == "あと2日"


def test_format_event_status() -> None:
    assert format_event_status(FUTURE_EVENT, NOW) == "開催前(開始まで14日)"
    assert format_event_status(EVENT, NOW) == "開催中(終了まで6日)"
    assert format_event_status(EVENT, dt("2026-11-22T23:59")) == "終了"

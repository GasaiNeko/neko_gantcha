"""カレンダーの配置計算のテスト。"""

from dataclasses import replace
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from conftest import Recorder

from neko_gantcha.domain.calendar import (
    CalendarInputs,
    daily_entries,
    due_day,
    event_days,
    events_on,
    layout_week,
    month_weeks,
    shift_month,
)
from neko_gantcha.domain.models import Category, Event, EventRelation, Freq, Recurrence, Task
from neko_gantcha.domain.todo import TodoContext, build_row
from neko_gantcha.services.store import VIEW_CALENDAR, ItemKind, ItemRef

if TYPE_CHECKING:
    from collections.abc import Mapping

    from conftest import Clock

    from neko_gantcha.domain.calendar import DayCell, MonthView
    from neko_gantcha.domain.todo import TodoRow
    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {
    1: Category(id=1, name="ソシャゲA", daily_reset_time=time(5, 0)),
    3: Category(id=3, name="日常"),
}
GAME_A = 1


def make_event(event_id: int, start: str, end: str, *, name: str = "イベント") -> Event:
    return Event(id=event_id, category_id=1, name=name, start_at=dt(start), end_at=dt(end))


def make_task(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-01T00:00")), **overrides
    )


def row_of(
    task: Task,
    events: Mapping[int, Event] | None = None,
    recurrences: Mapping[int, Recurrence] | None = None,
    *,
    now: datetime = NOW,
) -> TodoRow:
    return build_row(task, TodoContext(events or {}, recurrences or {}, CATEGORIES, {}, now))


# --- 月のグリッド ---


def test_month_weeks_start_on_monday_and_have_six_weeks() -> None:
    weeks = month_weeks(2026, 11)
    assert len(weeks) == 6
    assert all(len(week) == 7 for week in weeks)
    assert weeks[0][0] == date(2026, 10, 26)  # 11/1 は日曜なので前月の月曜から
    assert weeks[0][6] == date(2026, 11, 1)
    assert weeks[5][6] == date(2026, 12, 6)
    assert all(week[0].weekday() == 0 for week in weeks)


def test_month_weeks_can_start_on_sunday() -> None:
    weeks = month_weeks(2026, 11, first_weekday=6)
    assert len(weeks) == 6
    assert weeks[0][0] == date(2026, 11, 1)  # 11/1 は日曜なので、その日から
    assert weeks[0][6] == date(2026, 11, 7)
    assert weeks[5][6] == date(2026, 12, 12)
    assert all(week[0].weekday() == 6 for week in weeks)


def test_month_weeks_pads_short_months_to_six_weeks() -> None:
    # 2027年2月は月曜始まりの28日で、ちょうど4週に収まる
    weeks = month_weeks(2027, 2)
    assert len(weeks) == 6
    assert weeks[0][0] == date(2027, 2, 1)
    assert weeks[5][6] == date(2027, 3, 14)


def test_month_weeks_across_year_end() -> None:
    weeks = month_weeks(2026, 12)
    assert weeks[0][0] == date(2026, 11, 30)
    assert date(2027, 1, 3) in weeks[4]


def test_shift_month() -> None:
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2027, 1, -1) == (2026, 12)
    assert shift_month(2026, 11, -11) == (2025, 12)


# --- イベントの帯 ---


def test_event_days_excludes_midnight_end() -> None:
    assert event_days(make_event(1, "2026-11-20T19:00", "2026-11-27T23:59")) == (
        date(2026, 11, 20),
        date(2026, 11, 27),
    )
    # 0:00 ちょうどに終わるイベントは、前日までとして描く
    assert event_days(make_event(1, "2026-11-20T19:00", "2026-11-28T00:00"))[1] == date(
        2026, 11, 27
    )
    assert event_days(make_event(1, "2026-11-20T00:00", "2026-11-20T00:00")) == (
        date(2026, 11, 20),
        date(2026, 11, 20),
    )


def test_band_is_split_across_weeks() -> None:
    event = make_event(1, "2026-11-20T19:00", "2026-11-27T23:59")  # 金曜〜翌週金曜
    weeks = month_weeks(2026, 11)

    first = layout_week([event], weeks[3])  # 11/16〜11/22
    assert [(s.first_col, s.last_col, s.starts_here, s.ends_here) for s in first] == [
        (4, 6, True, False)
    ]
    second = layout_week([event], weeks[4])  # 11/23〜11/29
    assert [(s.first_col, s.last_col, s.starts_here, s.ends_here) for s in second] == [
        (0, 4, False, True)
    ]
    assert layout_week([event], weeks[0]) == []


def test_overlapping_events_get_separate_lanes() -> None:
    events = [
        make_event(1, "2026-11-16T00:00", "2026-11-18T23:59", name="A"),
        make_event(2, "2026-11-17T00:00", "2026-11-20T23:59", name="B"),
        make_event(3, "2026-11-19T00:00", "2026-11-22T23:59", name="C"),  # A の後ろに入る
        make_event(4, "2026-11-21T00:00", "2026-11-22T23:59", name="D"),  # B の後ろに入る
    ]
    week = month_weeks(2026, 11)[3]
    lanes = {s.event.name: s.lane for s in layout_week(events, week)}
    assert lanes == {"A": 0, "B": 1, "C": 0, "D": 1}


def test_events_on_day() -> None:
    events = [
        make_event(2, "2026-11-17T00:00", "2026-11-20T23:59", name="後"),
        make_event(1, "2026-11-16T00:00", "2026-11-18T23:59", name="先"),
    ]
    assert [e.name for e in events_on(events, date(2026, 11, 17))] == ["先", "後"]
    assert [e.name for e in events_on(events, date(2026, 11, 19))] == ["後"]
    assert events_on(events, date(2026, 11, 21)) == []


# --- 期限ラベル ---


def test_due_day_for_plain_and_event_tasks() -> None:
    event = make_event(1, "2026-11-20T19:00", "2026-11-27T23:59")
    events = {1: event}

    assert due_day(row_of(make_task(1, due_at=dt("2026-11-18T23:59")))) == date(2026, 11, 18)
    assert due_day(row_of(make_task(2))) is None  # 恒常タスク

    before = make_task(3, event_id=1, event_relation=EventRelation.BEFORE)
    assert due_day(row_of(before, events)) == date(2026, 11, 20)  # イベント開始日

    during = make_task(4, event_id=1, event_relation=EventRelation.DURING)
    assert due_day(row_of(during, events)) is None  # 帯で表すのでラベルは出さない

    during_with_due = make_task(
        5, event_id=1, event_relation=EventRelation.DURING, due_at=dt("2026-11-25T12:00")
    )
    assert due_day(row_of(during_with_due, events)) == date(2026, 11, 25)


def test_due_day_is_none_for_recurring() -> None:
    task = make_task(1)
    assert due_day(row_of(task, recurrences={1: Recurrence(1, Freq.DAILY)})) is None


# --- 日課の達成数 ---


def daily_rows(
    *tasks: Task,
    events: Mapping[int, Event] | None = None,
    rules: Mapping[int, Recurrence] | None = None,
) -> list[TodoRow]:
    recurrences = rules or {task.saved_id: Recurrence(task.saved_id, Freq.DAILY) for task in tasks}
    return [row_of(task, events, recurrences) for task in tasks]


def test_daily_entries_count_done_per_day() -> None:
    rows = daily_rows(make_task(1, "A"), make_task(2, "B"), make_task(3, "3回", progress_target=3))
    counts = {(1, "2026-11-15"): 1, (3, "2026-11-15"): 2, (2, "2026-11-16"): 1}

    yesterday = daily_entries(
        date(2026, 11, 15), CalendarInputs(rows, [], {}, CATEGORIES, counts, NOW)
    )
    assert {e.task.title: e.done for e in yesterday} == {"A": True, "B": False, "3回": False}
    assert [e.task.title for e in yesterday][-1] == "A"  # 未達成が先

    today = daily_entries(date(2026, 11, 16), CalendarInputs(rows, [], {}, CATEGORIES, counts, NOW))
    assert sum(e.done for e in today) == 1


def test_daily_entries_skip_future_days() -> None:
    rows = daily_rows(make_task(1))
    assert (
        daily_entries(date(2026, 11, 17), CalendarInputs(rows, [], {}, CATEGORIES, {}, NOW)) == []
    )
    # リセット前(11/17 3:00)は、ソシャゲAの 11/17 はまだ始まっていない
    assert (
        daily_entries(
            date(2026, 11, 17), CalendarInputs(rows, [], {}, CATEGORIES, {}, dt("2026-11-17T03:00"))
        )
        == []
    )
    assert len(
        daily_entries(
            date(2026, 11, 17), CalendarInputs(rows, [], {}, CATEGORIES, {}, dt("2026-11-17T05:00"))
        )
    )


def test_daily_entries_skip_days_before_task_existed() -> None:
    rows = daily_rows(make_task(1, created_at=dt("2026-11-10T12:00")))
    assert daily_entries(date(2026, 11, 9), CalendarInputs(rows, [], {}, CATEGORIES, {}, NOW)) == []
    assert (
        len(daily_entries(date(2026, 11, 10), CalendarInputs(rows, [], {}, CATEGORIES, {}, NOW)))
        == 1
    )


def test_daily_entries_respect_event_and_active_window() -> None:
    event = make_event(1, "2026-11-10T19:00", "2026-11-14T23:59")
    bound = make_task(1, event_id=1)
    limited = make_task(2)
    rules = {
        1: Recurrence(1, Freq.DAILY, event_id=1),
        2: Recurrence(2, Freq.DAILY, active_until=dt("2026-11-12T05:00")),
    }
    rows = daily_rows(bound, limited, events={1: event}, rules=rules)

    def ids(day: date) -> list[int]:
        return sorted(
            e.task.saved_id
            for e in daily_entries(day, CalendarInputs(rows, [], {1: event}, CATEGORIES, {}, NOW))
        )

    assert ids(date(2026, 11, 9)) == [2]  # イベント開始前(11/10 5:00 まで)
    assert ids(date(2026, 11, 10)) == [1, 2]
    assert ids(date(2026, 11, 12)) == [1]  # 有効期間が終わった
    assert ids(date(2026, 11, 14)) == [1]
    assert ids(date(2026, 11, 15)) == []  # イベント終了後


def test_weekly_tasks_are_not_daily_entries() -> None:
    rows = daily_rows(make_task(1), rules={1: Recurrence(1, Freq.WEEKLY)})
    assert (
        daily_entries(date(2026, 11, 16), CalendarInputs(rows, [], {}, CATEGORIES, {}, NOW)) == []
    )


# --- AppStore 経由 ---


def cell_of(view: MonthView, day: date) -> DayCell:
    return next(cell for week in view.weeks for cell in week if cell.day == day)


def test_store_calendar_month(store: AppStore, clock: Clock) -> None:
    event = store.save_event(
        Event(
            category_id=GAME_A,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00:00"),
            end_at=dt("2026-11-27T23:59:00"),
        )
    )
    dated = store.add_task("手続き", 3, due_at=dt("2026-11-18T17:00:00"))
    daily = store.add_task("デイリーミッション", GAME_A, freq=Freq.DAILY)
    store.add_task("編成の更新", GAME_A, event_id=event.id, event_relation=EventRelation.BEFORE)
    store.set_task_done(daily.saved_id, done=True)
    clock.now = dt("2026-11-17T12:00:00")  # 翌日。11/16 は達成済み、11/17 は未達成

    view = store.calendar_month(2026, 11)

    assert (view.first_day, view.last_day) == (date(2026, 10, 26), date(2026, 12, 6))
    assert [row.task.id for row in cell_of(view, date(2026, 11, 18)).due] == [dated.id]
    assert [row.task.title for row in cell_of(view, date(2026, 11, 20)).due] == ["編成の更新"]
    assert not cell_of(view, date(2026, 10, 26)).in_month

    done_day = cell_of(view, date(2026, 11, 16))
    assert (done_day.daily_done, done_day.daily_total) == (1, 1)
    open_day = cell_of(view, date(2026, 11, 17))
    assert (open_day.daily_done, open_day.daily_total) == (0, 1)
    assert cell_of(view, date(2026, 11, 18)).daily_total == 0  # 未来

    assert [s.event.id for s in view.bands[3]] == [event.id]

    # 分類で絞り込むと、他の分類のイベントとタスクは出ない
    store.set_filter(category_id=3)
    filtered = store.calendar_month(2026, 11)
    assert filtered.bands[3] == []
    assert cell_of(filtered, date(2026, 11, 16)).daily_total == 0
    assert len(cell_of(filtered, date(2026, 11, 18)).due) == 1


def test_store_calendar_day_shows_history(store: AppStore, clock: Clock) -> None:
    daily = store.add_task("デイリーミッション", GAME_A, freq=Freq.DAILY)
    store.set_task_done(daily.saved_id, done=True)
    clock.now = dt("2026-11-20T12:00:00")

    past = store.calendar_day(date(2026, 11, 16))
    assert [(e.task.id, e.done) for e in past.dailies] == [(daily.id, True)]
    missed = store.calendar_day(date(2026, 11, 17))
    assert [(e.task.id, e.done) for e in missed.dailies] == [(daily.id, False)]


def test_reveal_selects_and_requests_view(store: AppStore) -> None:
    task = store.add_task("手続き", 3)
    requests = Recorder(store.reveal_requested)
    store.reveal(ItemRef(ItemKind.TASK, task.saved_id), VIEW_CALENDAR)
    assert store.selected == ItemRef(ItemKind.TASK, task.saved_id)
    assert requests.calls == [(VIEW_CALENDAR,)]

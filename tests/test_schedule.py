"""タスクの実効日程と分類のテスト。"""

from dataclasses import replace
from datetime import datetime

from neko_gantcha.domain.models import Event, EventRelation, Freq, Recurrence, Task
from neko_gantcha.domain.schedule import TaskKind, effective_range, is_recurrence_active, task_kind


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")


def make_event(**overrides: object) -> Event:
    return replace(
        Event(
            id=1,
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        ),
        **overrides,
    )


def make_task(**overrides: object) -> Task:
    return replace(Task(category_id=1, title="タスク", created_at=NOW), **overrides)


def test_plain_task_uses_own_dates() -> None:
    task = make_task(start_at=dt("2026-11-17T00:00"), due_at=dt("2026-11-18T00:00"))
    assert effective_range(task, None) == (dt("2026-11-17T00:00"), dt("2026-11-18T00:00"))


def test_during_task_without_dates_uses_event_period() -> None:
    event = make_event()
    task = make_task(event_id=1, event_relation=EventRelation.DURING)
    assert effective_range(task, event) == (event.start_at, event.end_at)


def test_during_task_keeps_its_own_dates_when_set() -> None:
    event = make_event()
    task = make_task(event_id=1, event_relation=EventRelation.DURING, due_at=dt("2026-11-25T00:00"))
    assert effective_range(task, event) == (event.start_at, dt("2026-11-25T00:00"))


def test_before_task_is_due_at_event_start() -> None:
    event = make_event()
    task = make_task(
        event_id=1,
        event_relation=EventRelation.BEFORE,
        start_at=dt("2026-11-10T00:00"),
        due_at=dt("2026-11-12T00:00"),  # イベント開始が優先される
    )
    assert effective_range(task, event) == (dt("2026-11-10T00:00"), event.start_at)


def test_tasks_follow_event_reschedule() -> None:
    event = make_event()
    during = make_task(event_id=1, event_relation=EventRelation.DURING)
    before = make_task(event_id=1, event_relation=EventRelation.BEFORE)

    event.start_at = dt("2026-12-01T19:00")
    event.end_at = dt("2026-12-08T23:59")

    assert effective_range(during, event) == (dt("2026-12-01T19:00"), dt("2026-12-08T23:59"))
    assert effective_range(before, event) == (None, dt("2026-12-01T19:00"))


def test_relation_is_ignored_without_matching_event() -> None:
    task = make_task(event_id=1, event_relation=EventRelation.DURING)
    assert effective_range(task, None) == (None, None)
    assert effective_range(task, make_event(id=2)) == (None, None)
    # イベントが削除されて event_id だけ NULL になった場合
    orphan = make_task(event_id=None, event_relation=EventRelation.BEFORE)
    assert effective_range(orphan, make_event()) == (None, None)
    assert task_kind(orphan, None, None) is TaskKind.STANDING


def test_task_kind() -> None:
    event = make_event()
    assert task_kind(make_task(), None, None) is TaskKind.STANDING
    assert task_kind(make_task(due_at=NOW), None, None) is TaskKind.DATED
    during = make_task(event_id=1, event_relation=EventRelation.DURING)
    before = make_task(event_id=1, event_relation=EventRelation.BEFORE)
    assert task_kind(during, None, event) is TaskKind.EVENT_DURING
    assert task_kind(before, None, event) is TaskKind.EVENT_BEFORE
    for freq, kind in (
        (Freq.DAILY, TaskKind.DAILY),
        (Freq.WEEKLY, TaskKind.WEEKLY),
        (Freq.MONTHLY, TaskKind.MONTHLY),
    ):
        assert task_kind(make_task(id=5), Recurrence(5, freq), None) is kind


def test_recurrence_active_window() -> None:
    recurrence = Recurrence(
        1, Freq.DAILY, active_from=dt("2026-11-10T00:00"), active_until=dt("2026-11-20T00:00")
    )
    assert not is_recurrence_active(recurrence, dt("2026-11-09T23:59"), None)
    assert is_recurrence_active(recurrence, dt("2026-11-10T00:00"), None)
    assert is_recurrence_active(recurrence, dt("2026-11-19T23:59"), None)
    assert not is_recurrence_active(recurrence, dt("2026-11-20T00:00"), None)
    assert is_recurrence_active(Recurrence(1, Freq.WEEKLY), NOW, None)


def test_event_bound_recurrence_is_active_only_during_event() -> None:
    event = make_event()
    recurrence = Recurrence(1, Freq.DAILY, event_id=1)
    assert not is_recurrence_active(recurrence, dt("2026-11-20T18:59"), event)
    assert is_recurrence_active(recurrence, dt("2026-11-20T19:00"), event)
    assert is_recurrence_active(recurrence, dt("2026-11-27T23:58"), event)
    assert not is_recurrence_active(recurrence, dt("2026-11-27T23:59"), event)
    assert not is_recurrence_active(recurrence, dt("2026-11-22T00:00"), None)

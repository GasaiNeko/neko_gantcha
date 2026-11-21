"""リポジトリのテスト(メモリ上の SQLite)。"""

import sqlite3
from dataclasses import replace
from datetime import date, datetime, time

import pytest
from conftest import must

from neko_gantcha.data.repositories import (
    CategoryRepository,
    CompletionRepository,
    EventRepository,
    ProjectRepository,
    RecurrenceRepository,
    TaskRepository,
)
from neko_gantcha.domain.models import (
    Category,
    Event,
    EventRelation,
    Freq,
    Project,
    ProjectStatus,
    Recurrence,
    Task,
    TaskStatus,
)
from neko_gantcha.domain.periods import period_key
from neko_gantcha.domain.schedule import effective_range


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00:00")
GAME_A = 1  # 初期データの「ソシャゲA」


def add_event(conn: sqlite3.Connection, **overrides: object) -> Event:
    return EventRepository(conn).add(
        replace(
            Event(
                category_id=GAME_A,
                name="協力バトル",
                start_at=dt("2026-11-20T19:00"),
                end_at=dt("2026-11-27T23:59"),
            ),
            **overrides,
        )
    )


def add_task(conn: sqlite3.Connection, **overrides: object) -> Task:
    return TaskRepository(conn).add(
        replace(Task(category_id=GAME_A, title="タスク", created_at=NOW), **overrides)
    )


# --- 分類 ---


def test_category_crud_and_archive(conn: sqlite3.Connection) -> None:
    repo = CategoryRepository(conn)
    added = repo.add(
        Category(
            name="別ゲーム",
            color="#123456",
            daily_reset_time=time(4, 0),
            weekly_reset_weekday=3,
            monthly_reset_day=15,
            sort_order=9,
        )
    )
    assert added.id is not None
    assert repo.get(added.id) == added

    added.archived = True
    repo.update(added)
    assert added.id not in [c.id for c in repo.list_all()]
    assert added.id in [c.id for c in repo.list_all(include_archived=True)]

    repo.delete(added.id)
    assert repo.get(added.id) is None


def test_category_in_use_cannot_be_deleted(conn: sqlite3.Connection) -> None:
    add_task(conn)
    with pytest.raises(sqlite3.IntegrityError):
        CategoryRepository(conn).delete(GAME_A)


# --- イベント ---


def test_event_roundtrip_and_queries(conn: sqlite3.Connection) -> None:
    repo = EventRepository(conn)
    event = add_event(conn, url="https://example.com", note="メモ")
    earlier = add_event(
        conn, name="シナリオ", start_at=dt("2026-11-01T17:00"), end_at=dt("2026-11-08T20:59")
    )
    assert repo.get(event.saved_id) == event
    assert [e.id for e in repo.list_all()] == [earlier.id, event.id]  # 開始日時順
    assert repo.list_all(category_id=2) == []

    assert [e.id for e in repo.list_active(dt("2026-11-21T00:00"))] == [event.id]
    assert repo.list_active(dt("2026-11-27T23:59")) == []  # 終了時刻は含まない
    november = repo.list_overlapping(dt("2026-11-01T00:00"), dt("2026-12-01T00:00"))
    assert len(november) == 2
    assert repo.list_overlapping(dt("2026-11-09T00:00"), dt("2026-11-20T19:00")) == []


def test_event_end_before_start_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, start_at=dt("2026-11-20T19:00"), end_at=dt("2026-11-19T19:00"))


def test_deleting_event_keeps_tasks_and_clears_links(conn: sqlite3.Connection) -> None:
    event = add_event(conn)
    task = add_task(conn, event_id=event.id, event_relation=EventRelation.DURING)
    project = ProjectRepository(conn).add(
        Project(category_id=GAME_A, name="協力バトル準備", event_id=event.id)
    )
    daily = add_task(conn, title="イベント日課")
    RecurrenceRepository(conn).set(Recurrence(daily.saved_id, Freq.DAILY, event_id=event.id))

    EventRepository(conn).delete(event.saved_id)

    remaining = TaskRepository(conn).get(task.saved_id)
    assert remaining is not None
    assert remaining.event_id is None
    assert effective_range(remaining, None) == (None, None)
    assert must(ProjectRepository(conn).get(project.saved_id)).event_id is None
    assert must(RecurrenceRepository(conn).get(daily.saved_id)).event_id is None


def test_task_dates_follow_event_reschedule(conn: sqlite3.Connection) -> None:
    events = EventRepository(conn)
    tasks = TaskRepository(conn)
    event = add_event(conn)
    during = add_task(conn, event_id=event.id, event_relation=EventRelation.DURING)
    before = add_task(conn, event_id=event.id, event_relation=EventRelation.BEFORE)

    event.start_at = dt("2026-12-01T19:00")
    event.end_at = dt("2026-12-08T23:59")
    events.update(event)

    stored = events.get(event.saved_id)
    assert effective_range(must(tasks.get(during.saved_id)), stored) == (
        dt("2026-12-01T19:00"),
        dt("2026-12-08T23:59"),
    )
    assert effective_range(must(tasks.get(before.saved_id)), stored) == (
        None,
        dt("2026-12-01T19:00"),
    )


# --- プロジェクト ---


def test_project_roundtrip(conn: sqlite3.Connection) -> None:
    repo = ProjectRepository(conn)
    project = repo.add(
        Project(
            category_id=GAME_A, name="編成の強化", goal="完成させる", target_date=date(2026, 12, 20)
        )
    )
    assert repo.get(project.saved_id) == project

    project.status = ProjectStatus.DONE
    repo.update(project)
    assert must(repo.get(project.saved_id)).status is ProjectStatus.DONE
    assert [p.id for p in repo.list_all(category_id=GAME_A)] == [project.id]

    task = add_task(conn, project_id=project.id)
    repo.delete(project.saved_id)
    assert must(TaskRepository(conn).get(task.saved_id)).project_id is None


# --- タスク ---


def test_task_roundtrip_preserves_all_fields(conn: sqlite3.Connection) -> None:
    event = add_event(conn)
    task = add_task(
        conn,
        title="報酬交換",
        note="40箱",
        status=TaskStatus.DOING,
        priority=2,
        start_at=dt("2026-11-21T00:00"),
        due_at=dt("2026-11-27T23:59"),
        event_id=event.id,
        event_relation=EventRelation.DURING,
        progress_current=12,
        progress_target=40,
        sort_order=3,
    )
    assert TaskRepository(conn).get(task.saved_id) == task


def test_task_update_and_completion_timestamp(conn: sqlite3.Connection) -> None:
    repo = TaskRepository(conn)
    task = add_task(conn)
    task.status = TaskStatus.DONE
    task.completed_at = dt("2026-11-16T13:30:00")
    repo.update(task)
    assert repo.get(task.saved_id) == task
    assert repo.list_all(include_done=False) == []
    assert len(repo.list_all()) == 1


def test_task_list_filters(conn: sqlite3.Connection) -> None:
    repo = TaskRepository(conn)
    event = add_event(conn)
    in_event = add_task(conn, event_id=event.id, event_relation=EventRelation.BEFORE)
    life = add_task(conn, category_id=3, title="買い物")
    assert [t.id for t in repo.list_all(event_id=event.id)] == [in_event.id]
    assert [t.id for t in repo.list_all(category_id=3)] == [life.id]


def test_update_without_id_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match=r"id\ のないオブジェクトは更新できない"):
        TaskRepository(conn).update(Task(category_id=GAME_A, title="未保存", created_at=NOW))


def test_subtasks_can_nest_without_cycles(conn: sqlite3.Connection) -> None:
    repo = TaskRepository(conn)
    parent = add_task(conn, title="親")
    child = add_task(conn, title="子", parent_task_id=parent.id)
    grandchild = add_task(conn, title="孫", parent_task_id=child.id)
    great = add_task(conn, title="ひ孫", parent_task_id=grandchild.id)
    assert [t.id for t in repo.children(parent.saved_id)] == [child.id]
    assert {t.id for t in repo.descendants(parent.saved_id)} == {child.id, grandchild.id, great.id}
    assert repo.descendants(great.saved_id) == []

    # 子を持つタスクを別のタスクの下に移すのはよい
    other = add_task(conn, title="別の親")
    parent.parent_task_id = other.id
    repo.update(parent)
    assert must(repo.get(parent.saved_id)).parent_task_id == other.id

    # 自分の子孫を親にはできない。循環するため
    other.parent_task_id = great.id
    with pytest.raises(ValueError, match="自分のサブタスクを親タスクにはできない"):
        repo.update(other)

    other.parent_task_id = other.id
    with pytest.raises(ValueError, match="自分自身を親タスクにはできない"):
        repo.update(other)

    with pytest.raises(ValueError, match="親タスクが存在しない"):
        add_task(conn, title="親なし", parent_task_id=9999)


def test_deleting_parent_deletes_subtasks(conn: sqlite3.Connection) -> None:
    repo = TaskRepository(conn)
    parent = add_task(conn, title="親")
    child = add_task(conn, title="子", parent_task_id=parent.id)
    repo.delete(parent.saved_id)
    assert repo.get(child.saved_id) is None


# --- 繰り返しと達成記録 ---


def test_recurrence_set_replaces_existing(conn: sqlite3.Connection) -> None:
    repo = RecurrenceRepository(conn)
    task = add_task(conn, title="デイリーミッション")
    repo.set(Recurrence(task.saved_id, Freq.DAILY))
    repo.set(Recurrence(task.saved_id, Freq.WEEKLY, active_from=dt("2026-11-01T00:00")))

    stored = repo.get(task.saved_id)
    assert stored == Recurrence(task.saved_id, Freq.WEEKLY, active_from=dt("2026-11-01T00:00"))
    assert repo.by_task_id() == {task.id: stored}

    repo.delete(task.saved_id)
    assert repo.get(task.saved_id) is None


def test_completion_check_and_uncheck(conn: sqlite3.Connection) -> None:
    repo = CompletionRepository(conn)
    task = add_task(conn)
    repo.set_count(task.saved_id, "2026-11-16", 1, now=NOW)
    done = must(repo.get(task.saved_id, "2026-11-16"))
    assert done.count == 1
    assert done.completed_at == NOW

    repo.clear(task.saved_id, "2026-11-16")
    assert repo.get(task.saved_id, "2026-11-16") is None


def test_completion_count_and_zero_removes_record(conn: sqlite3.Connection) -> None:
    repo = CompletionRepository(conn)
    task = add_task(conn, progress_target=3)
    repo.set_count(task.saved_id, "2026-11-16", 1, now=NOW)
    repo.set_count(task.saved_id, "2026-11-16", 2, now=dt("2026-11-16T15:00:00"))
    stored = must(repo.get(task.saved_id, "2026-11-16"))
    assert stored.count == 2
    assert stored.completed_at == dt("2026-11-16T15:00:00")

    repo.set_count(task.saved_id, "2026-11-16", 0, now=NOW)
    assert repo.get(task.saved_id, "2026-11-16") is None


def test_daily_completion_resets_after_reset_time(conn: sqlite3.Connection) -> None:
    """リセット前に達成した日課は、リセット後の period_key では未達成になる。"""
    completions = CompletionRepository(conn)
    rule = must(CategoryRepository(conn).get(GAME_A)).reset_rule
    task = add_task(conn, title="デイリーミッション")
    RecurrenceRepository(conn).set(Recurrence(task.saved_id, Freq.DAILY))

    late_night = dt("2026-11-17T03:00:00")  # まだ 11/16 の日課
    completions.set_count(
        task.saved_id, period_key(Freq.DAILY, late_night, rule), 1, now=late_night
    )

    before_reset = period_key(Freq.DAILY, dt("2026-11-17T04:59:59"), rule)
    after_reset = period_key(Freq.DAILY, dt("2026-11-17T05:00:00"), rule)
    assert completions.get(task.saved_id, before_reset) is not None
    assert completions.get(task.saved_id, after_reset) is None


def test_completion_history_queries(conn: sqlite3.Connection) -> None:
    repo = CompletionRepository(conn)
    first = add_task(conn, title="A")
    second = add_task(conn, title="B")
    repo.set_count(first.saved_id, "2026-11-15", 1, now=NOW)
    repo.set_count(first.saved_id, "2026-11-16", 1, now=NOW)
    repo.set_count(second.saved_id, "2026-11-16", 1, now=NOW)
    repo.set_count(second.saved_id, "2026-W47", 1, now=NOW)

    assert [c.period_key for c in repo.list_for_task(first.saved_id)] == [
        "2026-11-15",
        "2026-11-16",
    ]
    assert len(repo.list_for_keys(["2026-11-16"])) == 2
    assert len(repo.list_for_keys(["2026-11-15", "2026-W47"])) == 2
    assert repo.list_for_keys([]) == []


def test_deleting_task_removes_recurrence_and_completions(conn: sqlite3.Connection) -> None:
    task = add_task(conn)
    RecurrenceRepository(conn).set(Recurrence(task.saved_id, Freq.DAILY))
    CompletionRepository(conn).set_count(task.saved_id, "2026-11-16", 1, now=NOW)

    TaskRepository(conn).delete(task.saved_id)

    assert RecurrenceRepository(conn).get(task.saved_id) is None
    assert CompletionRepository(conn).list_for_task(task.saved_id) == []

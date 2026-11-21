"""AppStore の繰り返しタスク・リセット検知・イベント操作のテスト。"""

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from conftest import Recorder, must
from PySide6.QtCore import QCoreApplication, QObject, QTimer

from neko_gantcha.domain.models import Event, EventRelation, Freq, TaskStatus
from neko_gantcha.services.store import AppStore, ItemKind, ItemRef

if TYPE_CHECKING:
    from conftest import Clock

    from neko_gantcha.domain.todo import TodoRow

GAME_A = 1


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def visible_row(store: AppStore, task_id: int) -> TodoRow | None:
    """ToDo に表示されている行。出ていなければ None。"""
    for data in store.todo_sections():
        for row in data.rows:
            if row.task.id == task_id:
                return row
    return None


# --- 繰り返しタスクとリセット検知 ---


def test_daily_becomes_undone_after_reset(store: AppStore, clock: Clock) -> None:
    """リセット時刻をまたぐと日課が未達成に戻る(フェーズ3の完了条件)。"""
    task = store.add_task("デイリーミッション", GAME_A, freq=Freq.DAILY)
    store.set_filter(show_done=True)
    clock.now = dt("2026-11-17T03:00:00")  # ソシャゲAは 5:00 リセット。まだ 11/16 の日課
    store.tick()

    store.set_task_done(task.saved_id, done=True)
    assert must(visible_row(store, task.saved_id)).done
    assert store.completion_count(task.saved_id) == 1

    periods = Recorder(store.periods_changed)
    ticks = Recorder(store.minute_ticked)
    clock.now = dt("2026-11-17T04:59:00")
    store.tick()
    assert must(visible_row(store, task.saved_id)).done
    assert periods.count == 0

    clock.now = dt("2026-11-17T05:00:00")
    store.tick()
    assert periods.count == 1  # 起動したままでもリセットを検知する
    assert ticks.count == 2
    assert not visible_row(store, task.id).done
    assert store.completion_count(task.id) == 0

    # 前の期間の達成記録は履歴として残る
    clock.now = dt("2026-11-17T04:00:00")
    assert store.completion_count(task.id) == 1


def test_weekly_and_monthly_reset(store: AppStore, clock: Clock) -> None:
    weekly = store.add_task("週課", GAME_A, freq=Freq.WEEKLY)
    monthly = store.add_task("月課", GAME_A, freq=Freq.MONTHLY)
    store.set_task_done(weekly.saved_id, done=True)
    store.set_task_done(monthly.saved_id, done=True)

    clock.now = dt("2026-11-23T04:59:00")  # 翌週月曜のリセット直前
    assert store.completion_count(weekly.saved_id) == 1
    clock.now = dt("2026-11-23T05:00:00")
    assert store.completion_count(weekly.saved_id) == 0
    assert store.completion_count(monthly.saved_id) == 1

    clock.now = dt("2026-12-01T05:00:00")
    assert store.completion_count(monthly.saved_id) == 0


def test_recurring_uncheck_removes_completion(store: AppStore) -> None:
    task = store.add_task("デイリーミッション", GAME_A, freq=Freq.DAILY)
    changed = Recorder(store.tasks_changed)
    store.set_task_done(task.saved_id, done=True)
    store.set_task_done(task.saved_id, done=True)  # 変化なしなら通知しない
    store.set_task_done(task.saved_id, done=False)
    assert store.completion_count(task.saved_id) == 0
    assert changed.count == 2
    assert must(store.task(task.saved_id)).status is TaskStatus.TODO  # タスク自体の状態は変えない


def test_recurring_count_per_period(store: AppStore, clock: Clock) -> None:
    task = store.add_task("1日3回", GAME_A, freq=Freq.DAILY, progress_target=3)
    store.adjust_progress(task.saved_id, +1)
    store.adjust_progress(task.saved_id, +1)
    row = must(store.todo_row(task.saved_id))
    assert (row.current, row.target, row.done) == (2, 3, False)

    store.adjust_progress(task.saved_id, +5)  # 目標を超えない
    assert must(store.todo_row(task.saved_id)).done
    assert store.completion_count(task.saved_id) == 3

    store.adjust_progress(task.saved_id, -1)
    assert store.completion_count(task.saved_id) == 2

    store.set_task_done(task.saved_id, done=True)  # チェックで目標まで埋まる
    assert store.completion_count(task.saved_id) == 3

    clock.now = dt("2026-11-17T05:00:00")
    assert must(store.todo_row(task.saved_id)).current == 0


def test_switching_recurrence_on_and_off(store: AppStore) -> None:
    task = store.add_task("編成の更新", GAME_A, due_at=dt("2026-11-20T00:00:00"))
    store.set_task_done(task.saved_id, done=True)

    store.update_task(must(store.task(task.saved_id)), freq=Freq.WEEKLY)
    recurring = must(store.task(task.saved_id))
    assert must(store.recurrence(task.saved_id)).freq is Freq.WEEKLY
    # 繰り返しタスクは日付も完了状態も持たない
    assert recurring.due_at is None
    assert recurring.status is TaskStatus.TODO
    assert recurring.completed_at is None

    store.update_task(must(store.task(task.saved_id)))  # freq を渡さなければ繰り返しは維持
    assert must(store.recurrence(task.saved_id)).freq is Freq.WEEKLY

    store.update_task(must(store.task(task.saved_id)), freq=None)
    assert store.recurrence(task.saved_id) is None


# --- イベント ---


def make_event(**overrides: object) -> Event:
    return replace(
        Event(
            category_id=GAME_A,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00:00"),
            end_at=dt("2026-11-27T23:59:00"),
        ),
        **overrides,
    )


def test_save_event_adds_updates_and_notifies(store: AppStore) -> None:
    events = Recorder(store.events_changed)
    tasks = Recorder(store.tasks_changed)
    event = store.save_event(make_event(name="  協力バトル  "))
    assert event.id is not None
    assert event.name == "協力バトル"

    event.end_at = dt("2026-11-28T23:59:00")
    store.save_event(event)
    assert must(store.get_event(event.id)).end_at == dt("2026-11-28T23:59:00")
    assert (events.count, tasks.count) == (2, 2)


def test_save_event_validates(store: AppStore) -> None:
    with pytest.raises(ValueError, match="イベント名は空にできない"):
        store.save_event(make_event(name=" "))
    with pytest.raises(ValueError, match="終了日時は開始日時より後にする"):
        store.save_event(make_event(end_at=dt("2026-11-19T00:00:00")))


def test_event_tasks_follow_reschedule(store: AppStore) -> None:
    event = store.save_event(make_event())
    during = store.add_task("報酬交換", GAME_A, event_id=event.id)  # 既定は「イベント中」
    before = store.add_task(
        "編成の更新", GAME_A, event_id=event.id, event_relation=EventRelation.BEFORE
    )
    assert must(store.task(during.saved_id)).event_relation is EventRelation.DURING
    assert must(store.todo_row(before.saved_id)).due == dt("2026-11-20T19:00:00")

    event.start_at = dt("2026-12-01T19:00:00")
    event.end_at = dt("2026-12-08T23:59:00")
    store.save_event(event)

    during_row = must(store.todo_row(during.saved_id))
    assert (during_row.start, during_row.due) == (event.start_at, event.end_at)
    assert must(store.todo_row(before.saved_id)).due == dt("2026-12-01T19:00:00")


def test_event_daily_shows_only_during_event(store: AppStore, clock: Clock) -> None:
    event = store.save_event(make_event())
    task = store.add_task("イベント日課", GAME_A, event_id=event.id, freq=Freq.DAILY)
    assert must(store.recurrence(task.saved_id)).event_id == event.id
    assert must(store.task(task.saved_id)).event_relation is None
    assert visible_row(store, task.saved_id) is None  # 開催前

    clock.now = dt("2026-11-21T12:00:00")
    assert visible_row(store, task.saved_id) is not None

    clock.now = dt("2026-11-28T12:00:00")
    assert visible_row(store, task.saved_id) is None


def test_events_list_hides_ended_unless_show_done(store: AppStore) -> None:
    ended = store.save_event(
        make_event(
            name="終了済み", start_at=dt("2026-11-01T00:00:00"), end_at=dt("2026-11-08T00:00:00")
        )
    )
    upcoming = store.save_event(make_event())
    other = store.save_event(make_event(name="別分類", category_id=2))

    assert [e.id for e in store.events()] == [upcoming.id, other.id]
    store.set_filter(show_done=True)
    assert [e.id for e in store.events()] == [ended.id, upcoming.id, other.id]
    store.set_filter(category_id=2)
    assert [e.id for e in store.events()] == [other.id]


def test_delete_event_keeping_tasks(store: AppStore) -> None:
    event = store.save_event(make_event())
    task = store.add_task("報酬交換", GAME_A, event_id=event.id)
    daily = store.add_task("イベント日課", GAME_A, event_id=event.id, freq=Freq.DAILY)
    store.select(ItemRef(ItemKind.EVENT, event.saved_id))

    store.delete_event(event.saved_id)

    assert store.get_event(event.saved_id) is None
    assert store.selected is None
    assert must(store.task(task.saved_id)).event_id is None
    assert must(store.recurrence(daily.saved_id)).event_id is None  # 常設の日課になる
    assert visible_row(store, daily.saved_id) is not None


def test_delete_event_with_tasks(store: AppStore) -> None:
    event = store.save_event(make_event())
    task = store.add_task("報酬交換", GAME_A, event_id=event.id)
    unrelated = store.add_task("恒常", GAME_A)
    store.select(ItemRef(ItemKind.TASK, task.saved_id))

    store.delete_event(event.saved_id, delete_tasks=True)

    assert store.task(task.saved_id) is None
    assert store.task(unrelated.saved_id) is not None
    assert store.selected is None


# --- QObject との衝突 ---


def test_store_does_not_override_qobject_members() -> None:
    """AppStore のメソッド名が QObject のものと衝突していないこと。

    衝突すると Qt 内部からの呼び出し(event() など)が壊れ、タイマー作成時に落ちる。
    """
    own = {name for name in vars(AppStore) if not name.startswith("_")}
    assert own & set(dir(QObject)) == {"staticMetaObject"}  # Qt が自動で付ける属性


def test_start_clock_creates_timer(store: AppStore) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    store.start_clock()
    timer = must(store._timer)
    assert store.findChild(QTimer) is timer
    timer.stop()
    del app

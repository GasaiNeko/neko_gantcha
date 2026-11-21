"""AppStore のテスト。QApplication なしで、シグナルとデータ操作だけを確かめる。"""

from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from conftest import Recorder, must

from neko_gantcha.data import db
from neko_gantcha.domain.models import TaskStatus
from neko_gantcha.services.store import AppStore, Filter, ItemKind, ItemRef

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import Clock

    from neko_gantcha.domain.models import Task


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


GAME_A, LIFE = 1, 3


def test_add_task_persists_and_notifies(store: AppStore, clock: Clock) -> None:
    changed = Recorder(store.tasks_changed)
    task = store.add_task("  キャラの育成  ", GAME_A)

    assert task.id is not None
    assert task.title == "キャラの育成"
    assert task.created_at == clock.now
    assert store.task(task.id) == task
    assert changed.count == 1


def test_add_task_rejects_empty_title(store: AppStore) -> None:
    with pytest.raises(ValueError, match="タイトルが空のタスクは追加できない"):
        store.add_task("   ", GAME_A)


def test_done_sets_and_clears_completed_at(store: AppStore, clock: Clock) -> None:
    task = store.add_task("買い物", LIFE)
    clock.now = dt("2026-11-16T18:30:00")
    store.set_task_done(task.saved_id, done=True)
    done = must(store.task(task.saved_id))
    assert done.status is TaskStatus.DONE
    assert done.completed_at == dt("2026-11-16T18:30:00")

    store.set_task_done(task.saved_id, done=False)
    undone = must(store.task(task.saved_id))
    assert undone.status is TaskStatus.TODO
    assert undone.completed_at is None


def test_set_task_done_is_noop_when_unchanged(store: AppStore) -> None:
    task = store.add_task("買い物", LIFE)
    changed = Recorder(store.tasks_changed)
    store.set_task_done(task.saved_id, done=False)
    store.set_task_done(9999, done=True)
    assert changed.count == 0


def test_update_task_via_status_field(store: AppStore) -> None:
    task = store.add_task("編成の更新", GAME_A)
    task.status = TaskStatus.DOING
    task.due_at = dt("2026-11-20T19:00:00")
    store.update_task(task)
    stored = must(store.task(task.saved_id))
    assert stored.status is TaskStatus.DOING
    assert stored.due_at == dt("2026-11-20T19:00:00")
    assert stored.completed_at is None

    stored.status = TaskStatus.DONE
    store.update_task(stored)
    assert must(store.task(task.saved_id)).completed_at is not None


def test_update_task_rejects_empty_title(store: AppStore) -> None:
    task = store.add_task("編成の更新", GAME_A)
    task.title = " "
    with pytest.raises(ValueError, match="タイトルは空にできない"):
        store.update_task(task)


def test_progress_reaching_target_completes_task(store: AppStore) -> None:
    task = store.add_task("報酬交換", GAME_A, progress_target=3)
    assert must(store.task(task.saved_id)).progress_current == 0

    store.adjust_progress(task.saved_id, +1)
    store.adjust_progress(task.saved_id, +1)
    assert must(store.task(task.saved_id)).status is TaskStatus.TODO
    store.adjust_progress(task.saved_id, +1)
    done = must(store.task(task.saved_id))
    assert (done.progress_current, done.status) == (3, TaskStatus.DONE)

    store.adjust_progress(task.saved_id, +1)  # 目標を超えない
    assert must(store.task(task.saved_id)).progress_current == 3

    store.adjust_progress(task.saved_id, -1)  # 減らすと未完了に戻る
    back = must(store.task(task.saved_id))
    assert (back.progress_current, back.status, back.completed_at) == (2, TaskStatus.TODO, None)

    store.adjust_progress(task.saved_id, -5)  # 0 を下回らない
    assert must(store.task(task.saved_id)).progress_current == 0


def test_checking_progress_task_fills_progress(store: AppStore) -> None:
    task = store.add_task("報酬交換", GAME_A, progress_target=40, progress_current=12)
    store.set_task_done(task.saved_id, done=True)
    assert must(store.task(task.saved_id)).progress_current == 40

    store.set_task_done(task.saved_id, done=False)
    undone = must(store.task(task.saved_id))
    assert undone.status is TaskStatus.TODO


def test_removing_progress_target_clears_current(store: AppStore) -> None:
    task = store.add_task("報酬交換", GAME_A, progress_target=40, progress_current=12)
    task.progress_target = None
    store.update_task(task)
    assert must(store.task(task.saved_id)).progress_current is None


def test_delete_task_clears_selection(store: AppStore) -> None:
    task = store.add_task("買い物", LIFE)
    store.select(ItemRef(ItemKind.TASK, task.saved_id))
    selection = Recorder(store.selection_changed)

    store.delete_task(task.saved_id)

    assert store.task(task.saved_id) is None
    assert store.selected is None
    assert selection.calls == [(None,)]


def test_selection_notifies_only_on_change(store: AppStore) -> None:
    selection = Recorder(store.selection_changed)
    ref = ItemRef(ItemKind.TASK, 1)
    store.select(ref)
    store.select(ref)
    store.select(None)
    assert selection.calls == [(ref,), (None,)]


def test_filter_by_category_and_done(store: AppStore) -> None:
    game = store.add_task("編成の更新", GAME_A)
    life = store.add_task("買い物", LIFE)
    store.set_task_done(life.saved_id, done=True)
    changed = Recorder(store.filter_changed)

    assert [t.id for t in store.tasks()] == [game.id]  # 既定では完了済みを隠す

    store.set_filter(show_done=True)
    assert [t.id for t in store.tasks()] == [game.id, life.id]

    store.set_filter(category_id=LIFE)
    assert [t.id for t in store.tasks()] == [life.id]
    assert store.filter == Filter(category_id=LIFE, show_done=True)

    store.set_filter(category_id=LIFE)  # 変化なしなら通知しない
    assert changed.count == 2


def test_tasks_survive_restart(tmp_path: Path, clock: Clock) -> None:
    """単発タスクと恒常タスクが、DB を開き直しても残る(フェーズ2の完了条件)。"""
    path = tmp_path / "neko_gantcha.db"
    first = db.open_database(path)
    store = AppStore(first, clock)
    standing = store.add_task("キャラの育成", GAME_A)
    dated = store.add_task("手続き", LIFE, due_at=dt("2026-11-30T17:00:00"))
    finished = store.add_task("買い物", LIFE)
    store.set_task_done(finished.saved_id, done=True)
    first.close()

    second = db.open_database(path)
    try:
        reopened = AppStore(second, clock)
        reopened.set_filter(show_done=True)
        assert reopened.tasks() == [standing, dated, store_task(finished, clock)]
    finally:
        second.close()


def store_task(task: Task, clock: Clock) -> Task:
    """完了操作後に期待されるタスクの状態。"""
    task.status = TaskStatus.DONE
    task.completed_at = clock.now
    return task

"""フェーズ7のテスト: ガントのドラッグ、分類の設定、バックアップ、JSON の入出力。"""

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest
from conftest import Recorder, must

from neko_gantcha.data import backup, db, transfer
from neko_gantcha.domain.gantt import DragMode, GanttRow, RowKind, drag_modes, dragged_range
from neko_gantcha.domain.models import Category, Event, EventRelation, Freq, Project, Task
from neko_gantcha.services.store import ItemKind, ItemRef

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import Clock

    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


DAY = timedelta(days=1)


# --- ドラッグの計算 ---


def task_row(**overrides: object) -> GanttRow:
    return replace(
        GanttRow(
            kind=RowKind.TASK,
            label="タスク",
            depth=2,
            category_id=1,
            start=dt("2026-11-10T09:00"),
            end=dt("2026-11-20T23:59"),
        ),
        **overrides,
    )


def test_drag_modes_by_row_kind() -> None:
    assert drag_modes(task_row()) == frozenset(DragMode)
    assert drag_modes(task_row(kind=RowKind.EVENT)) == frozenset(DragMode)
    assert drag_modes(task_row(kind=RowKind.PROJECT)) == {DragMode.END}
    # イベントに紐付くタスクはイベントに従うので動かせない
    assert drag_modes(task_row(relation=EventRelation.DURING)) == frozenset()
    assert drag_modes(task_row(kind=RowKind.CATEGORY, start=None, end=None)) == frozenset()


def test_dragged_range_move_shifts_only_own_values() -> None:
    start, end = dt("2026-11-10T09:00"), dt("2026-11-20T23:59")
    assert dragged_range((start, end), (start, end), mode=DragMode.MOVE, delta=2 * DAY) == (
        dt("2026-11-12T09:00"),
        dt("2026-11-22T23:59"),
    )
    # 期限だけのタスクは期限だけが動く。帯の開始は作成日時なので持っていない
    assert dragged_range((None, end), (start, end), mode=DragMode.MOVE, delta=-DAY) == (
        None,
        dt("2026-11-19T23:59"),
    )


def test_dragged_range_resize_sets_value_and_stops_at_other_end() -> None:
    start, end = dt("2026-11-10T09:00"), dt("2026-11-20T23:59")
    # 期限だけのタスクの左端を動かすと、開始を持つようになる
    assert dragged_range((None, end), (start, end), mode=DragMode.START, delta=3 * DAY) == (
        dt("2026-11-13T09:00"),
        end,
    )
    assert dragged_range((start, end), (start, end), mode=DragMode.START, delta=30 * DAY) == (
        end,
        end,
    )
    # 開始だけのタスクの右端を動かすと、期限を持つようになる
    assert dragged_range((start, None), (start, start + DAY), mode=DragMode.END, delta=2 * DAY) == (
        start,
        start + 3 * DAY,
    )
    assert dragged_range((start, end), (start, end), mode=DragMode.END, delta=-30 * DAY) == (
        start,
        start,
    )


def gantt_row(store: AppStore, label: str) -> GanttRow:
    return next(row for row in store.gantt().rows if row.label == label)


def test_store_apply_gantt_drag_for_task_event_and_project(store: AppStore) -> None:
    task = store.add_task("買い物", 3, due_at=dt("2026-11-18T18:00"))
    assert store.apply_gantt_drag(gantt_row(store, "買い物"), DragMode.MOVE, 2)
    assert must(store.task(task.saved_id)).due_at == dt("2026-11-20T18:00")
    assert must(store.task(task.saved_id)).start_at is None
    assert store.apply_gantt_drag(gantt_row(store, "買い物"), DragMode.START, 1)
    assert must(store.task(task.saved_id)).start_at == dt(
        "2026-11-17T12:00"
    )  # 作成日時 11/16 12:00 + 1日

    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    linked = store.add_task("報酬交換", 1, event_id=raid.id)
    assert store.apply_gantt_drag(gantt_row(store, "協力バトル"), DragMode.END, 1)
    assert must(store.get_event(raid.saved_id)).end_at == dt("2026-11-28T23:59")
    assert must(store.todo_row(linked.saved_id)).due == dt("2026-11-28T23:59")  # タスクも追従
    # 紐付くタスクは動かせない
    assert not store.apply_gantt_drag(gantt_row(store, "報酬交換"), DragMode.MOVE, 1)
    assert not store.apply_gantt_drag(gantt_row(store, "協力バトル"), DragMode.MOVE, 0)

    project = store.save_project(
        Project(category_id=1, name="編成の強化", target_date=date(2026, 11, 30))
    )
    assert store.apply_gantt_drag(gantt_row(store, "編成の強化"), DragMode.END, -3)
    assert must(store.project(project.saved_id)).target_date == date(2026, 11, 27)
    assert not store.apply_gantt_drag(gantt_row(store, "編成の強化"), DragMode.MOVE, 1)


# --- 分類の設定 ---


def test_store_category_add_update_and_validation(store: AppStore) -> None:
    changed = Recorder(store.categories_changed)
    category = store.save_category(Category(name="  原神 ", color="#22AAFF"))
    assert category.id is not None
    assert category.name == "原神"
    assert category.sort_order == max(c.sort_order for c in store.categories())
    assert changed.count == 1
    with pytest.raises(ValueError, match="「ソシャゲA」という分類はすでにある"):
        store.save_category(Category(name="ソシャゲA"))
    with pytest.raises(ValueError, match="分類名は空にできない"):
        store.save_category(Category(name=" "))
    category.daily_reset_time = time(4, 0)
    store.save_category(category)
    assert must(store.category(category.id)).daily_reset_time == time(4, 0)


def test_store_category_reset_change_moves_period(store: AppStore, clock: Clock) -> None:
    # 4:30 は、5:00 リセットなら前日の期間、4:00 リセットなら当日の期間
    clock.now = dt("2026-11-16T04:30")
    daily = store.add_task("日課", 1, freq=Freq.DAILY)
    assert must(store.task_period(daily.saved_id)).key == "2026-11-15"
    periods = Recorder(store.periods_changed)
    game_a = must(store.category(1))
    game_a.daily_reset_time = time(4, 0)
    store.save_category(game_a)
    assert must(store.task_period(daily.saved_id)).key == "2026-11-16"
    assert periods.count == 1


def test_store_category_delete_and_archive(store: AppStore) -> None:
    unused = store.save_category(Category(name="未使用"))
    store.delete_category(unused.saved_id)
    assert store.category(unused.saved_id) is None
    store.add_task("買い物", 3)
    assert store.category_usage(3) == 1
    with pytest.raises(ValueError, match="使用中の分類は削除できない"):
        store.delete_category(3)
    store.set_filter(category_id=3)
    daily_life = must(store.category(3))
    daily_life.archived = True
    store.save_category(daily_life)
    assert store.filter.category_id is None
    assert 3 not in [c.id for c in store.categories()]
    assert 3 in [c.id for c in store.categories(include_archived=True)]


def test_store_move_category(store: AppStore) -> None:
    store.move_category(3, -1)
    assert [c.id for c in store.categories()] == [1, 3, 2]
    store.move_category(1, -1)  # 先頭より上には動かない
    assert [c.id for c in store.categories()] == [1, 3, 2]
    store.move_category(1, +2)
    assert [c.id for c in store.categories()] == [3, 2, 1]


# --- バックアップ ---


def test_create_backup_copies_database(conn: sqlite3.Connection, tmp_path: Path) -> None:
    conn.execute("INSERT INTO categories (name) VALUES ('原神')")
    conn.commit()
    path = backup.create_backup(conn, tmp_path, dt("2026-11-16T12:00"))
    assert path.name == "neko_gantcha-20261116-120000.db"
    copy = sqlite3.connect(path)
    names = [row[0] for row in copy.execute("SELECT name FROM categories")]
    copy.close()
    assert "原神" in names
    # 同じ秒にとっても上書きしない
    second = backup.create_backup(conn, tmp_path, dt("2026-11-16T12:00"), label="manual")
    third = backup.create_backup(conn, tmp_path, dt("2026-11-16T12:00"), label="manual")
    assert len({path, second, third}) == 3
    assert len(backup.list_backups(tmp_path)) == 3


def test_auto_backup_once_a_day_and_prunes_old_ones(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for day in range(1, 6):
        made = backup.auto_backup(conn, tmp_path, datetime(2026, 11, day, 9, 0), keep=3)
        assert made is not None
    assert backup.auto_backup(conn, tmp_path, datetime(2026, 11, 5, 20, 0), keep=3) is None
    manual = backup.create_backup(conn, tmp_path, datetime(2026, 11, 1, 8, 0), label="manual")
    backup.prune_backups(tmp_path, keep=3)
    names = [path.name for path in backup.list_backups(tmp_path)]
    assert names == [
        manual.name,  # 手動のものは消さない
        "neko_gantcha-20261103-090000.db",
        "neko_gantcha-20261104-090000.db",
        "neko_gantcha-20261105-090000.db",
    ]


# --- エクスポートとインポート ---


def fill(store: AppStore) -> tuple[Event, Project, Task, Task]:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    project = store.save_project(Project(category_id=1, name="編成の強化", event_id=raid.id))
    parent = store.add_task(
        "編成", 1, project_id=project.id, event_id=raid.id, event_relation=EventRelation.BEFORE
    )
    store.add_subtask(parent.saved_id, "武器")
    daily = store.add_task("ポイント集め", 1, event_id=raid.id, freq=Freq.DAILY, progress_target=3)
    store.set_completion_count(daily.saved_id, 2)
    return raid, project, parent, daily


def test_export_import_roundtrip(store: AppStore, tmp_path: Path) -> None:
    fill(store)
    path = tmp_path / "export.json"
    store.export_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == "neko_gantcha"
    assert data["schema_version"] == 4
    assert [row["name"] for row in data["events"]] == ["協力バトル"]
    assert len(data["tasks"]) == 3
    assert len(data["completions"]) == 1

    # 別の DB に読み込むと同じ内容になる
    other = db.open_database(":memory:")
    try:
        transfer.import_data(other, transfer.read_export(path))
        assert transfer.export_data(other, dt("2026-11-16T12:00")) == data
    finally:
        other.close()


def test_import_replaces_data_and_backs_up_first(store: AppStore, tmp_path: Path) -> None:
    path = tmp_path / "export.json"
    fill(store)
    store.export_json(path)
    extra = store.add_task("あとで追加", 3)
    store.select(ItemRef(ItemKind.TASK, extra.saved_id))
    store.set_filter(category_id=3)
    tasks = Recorder(store.tasks_changed)

    counts = store.import_json(path, tmp_path / "backup")
    assert counts["tasks"] == 3
    assert store.task(extra.saved_id) is None
    assert store.selected is None
    assert store.filter.category_id is None
    assert tasks.count >= 1
    backups = backup.list_backups(tmp_path / "backup")
    assert len(backups) == 1
    assert "before-import" in backups[0].name
    daily = next(t for t in store.tasks() if t.title == "ポイント集め")
    assert store.completion_count(daily.saved_id) == 2


def test_import_rejects_bad_files_without_changing_data(store: AppStore, tmp_path: Path) -> None:
    store.add_task("残る", 3)
    bad = tmp_path / "bad.json"
    for text in (
        "{壊れた",
        json.dumps({"format": "other"}),
        json.dumps({"format": "neko_gantcha", "format_version": 1, "tasks": "x"}),
    ):
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(transfer.TransferError):
            store.import_json(bad, tmp_path / "backup")
    assert [t.title for t in store.tasks()] == ["残る"]


def test_import_rolls_back_on_broken_references(conn: sqlite3.Connection) -> None:
    data = transfer.export_data(conn, dt("2026-11-16T12:00"))
    data["tasks"] = [
        {"id": 1, "category_id": 99, "title": "分類がない", "created_at": "2026-11-16T12:00:00"}
    ]
    before = transfer.export_data(conn, dt("2026-11-16T12:00"))
    with pytest.raises(transfer.TransferError):
        transfer.import_data(conn, data)
    assert transfer.export_data(conn, dt("2026-11-16T12:00")) == before


def test_import_rejects_newer_schema(conn: sqlite3.Connection) -> None:
    data = transfer.export_data(conn, dt("2026-11-16T12:00"))
    data["schema_version"] = 99
    with pytest.raises(transfer.TransferError):
        transfer.import_data(conn, data)


def test_task_model_unchanged_by_import(store: AppStore, tmp_path: Path) -> None:
    # インポート後もモデルとして読める。日時の文字列が壊れていない
    _, _, parent, _ = fill(store)
    path = tmp_path / "export.json"
    store.export_json(path)
    store.import_json(path, tmp_path / "backup")
    loaded = must(store.task(parent.saved_id))
    assert isinstance(loaded, Task)
    assert loaded.event_relation is EventRelation.BEFORE
    assert loaded.created_at == dt("2026-11-16T12:00")

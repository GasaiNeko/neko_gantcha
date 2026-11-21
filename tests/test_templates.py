"""イベントのテンプレート(フェーズ10)のテスト。"""

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest
from conftest import must

from neko_gantcha.data import db, transfer
from neko_gantcha.data.repositories import TemplateRepository
from neko_gantcha.domain.duplicate import EventPlan
from neko_gantcha.domain.models import (
    Event,
    EventRelation,
    EventTemplate,
    Freq,
    Project,
    Recurrence,
    Task,
    TaskStatus,
    TemplateTask,
)
from neko_gantcha.domain.template import (
    describe_offset,
    make_template,
    plan_from_template,
    suggest_start,
    template_tree,
)
from neko_gantcha.services.store import TemplateNameError

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
RAID = Event(
    id=5,
    category_id=1,
    name="協力バトル",
    start_at=dt("2026-11-20T19:00"),
    end_at=dt("2026-11-27T23:59"),
    url="https://example.com",
    note="本戦は4日目から",
)


def make_task(task_id: int, title: str, **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-10T09:00")), **overrides
    )


# --- ドメイン ---


def test_make_template_stores_offsets_and_drops_progress() -> None:
    tasks = [
        make_task(
            1,
            "報酬交換",
            event_id=5,
            event_relation=EventRelation.DURING,
            progress_target=40,
            progress_current=12,
            status=TaskStatus.DOING,
            project_id=9,
            priority=2,
        ),
        make_task(
            2,
            "編成",
            event_id=5,
            event_relation=EventRelation.BEFORE,
            start_at=dt("2026-11-18T00:00"),
        ),
        make_task(3, "武器", parent_task_id=2, due_at=dt("2026-11-19T21:30")),
        make_task(4, "素材", parent_task_id=3),
        make_task(6, "ポイント集め", event_id=5),
    ]
    recurrences = {
        6: Recurrence(
            6,
            Freq.DAILY,
            active_from=dt("2026-11-21T00:00"),
            active_until=dt("2026-11-26T00:00"),
            event_id=5,
        )
    }
    template, items = make_template(RAID, tasks, recurrences, name="  ", now=NOW)
    assert template.name == "協力バトル"  # 空なら元のイベント名
    assert template.duration == timedelta(days=7, hours=4, minutes=59)
    assert template.start_time == time(19, 0)
    assert (template.url, template.note, template.created_at) == (RAID.url, RAID.note, NOW)

    by_title = {item.title: item for item in items}
    box = by_title["報酬交換"]
    assert box.linked
    assert box.event_relation is EventRelation.DURING
    assert (box.progress_target, box.priority) == (40, 2)
    assert by_title["編成"].start_offset == -timedelta(days=2, hours=19)
    assert by_title["武器"].due_offset == -timedelta(hours=21, minutes=30)
    assert not by_title["武器"].linked
    assert by_title["武器"].event_relation is None
    assert by_title["素材"].parent_id == 3
    meat = by_title["ポイント集め"]
    assert meat.freq is Freq.DAILY
    assert meat.active_from_offset == timedelta(hours=5)
    assert meat.active_until_offset == timedelta(days=5, hours=5)
    # 親が子より先
    order = [item.id for item in items]
    assert order.index(2) < order.index(3) < order.index(4)


def test_plan_from_template_applies_offsets() -> None:
    template = EventTemplate(
        id=1,
        category_id=2,
        name="協力バトル",
        duration=timedelta(days=7),
        start_time=time(19, 0),
        url="u",
        note="n",
        created_at=NOW,
    )
    items = [
        TemplateTask(id=11, category_id=2, title="子", parent_id=10, sort_order=0),
        TemplateTask(
            id=10,
            category_id=2,
            title="編成",
            linked=True,
            event_relation=EventRelation.BEFORE,
            start_offset=-timedelta(days=2),
        ),
        TemplateTask(id=12, category_id=2, title="箱", linked=True, progress_target=40),
        TemplateTask(
            id=13,
            category_id=2,
            title="肉",
            linked=True,
            freq=Freq.DAILY,
            active_from_offset=timedelta(hours=5),
        ),
    ]
    start = dt("2026-12-18T19:00")
    event, copies = plan_from_template(
        template,
        items,
        plan=EventPlan("協力バトル(12月)", start, start + template.duration),
        now=NOW,
    )
    assert (event.name, event.category_id, event.url, event.note) == (
        "協力バトル(12月)",
        2,
        "u",
        "n",
    )
    assert event.end_at == dt("2026-12-25T19:00")
    assert [c.source_id for c in copies].index(10) < [c.source_id for c in copies].index(11)
    by_title = {c.task.title: c for c in copies}
    prep = by_title["編成"]
    assert prep.linked
    assert prep.task.start_at == dt("2026-12-16T19:00")
    assert prep.task.event_relation is EventRelation.BEFORE
    child = by_title["子"]
    assert not child.linked
    assert child.task.parent_task_id == 10
    assert by_title["箱"].task.progress_current == 0
    assert by_title["箱"].task.created_at == NOW
    assert by_title["肉"].freq is Freq.DAILY
    assert by_title["肉"].active_from == dt("2026-12-19T00:00")


def test_template_tree_depths() -> None:
    items = [
        TemplateTask(id=3, category_id=1, title="孫", parent_id=2),
        TemplateTask(id=1, category_id=1, title="親", sort_order=0),
        TemplateTask(id=2, category_id=1, title="子", parent_id=1),
        TemplateTask(id=4, category_id=1, title="別", sort_order=1),
    ]
    assert [(t.title, d) for t, d in template_tree(items)] == [
        ("親", 0),
        ("子", 1),
        ("孫", 2),
        ("別", 0),
    ]


def test_describe_offset_and_suggest_start() -> None:
    assert describe_offset(timedelta(0)) == "開始時刻"
    assert describe_offset(-timedelta(days=2)) == "開始の2日前"
    assert describe_offset(timedelta(days=3, hours=4, minutes=30)) == "開始の3日4時間30分後"
    template = EventTemplate(
        category_id=1, name="t", duration=timedelta(0), start_time=time(19, 0), created_at=NOW
    )
    assert suggest_start(template, date(2026, 12, 1)) == dt("2026-12-01T19:00")


# --- リポジトリ ---


def test_repository_save_replaces_tasks_and_remaps_parents(conn: sqlite3.Connection) -> None:
    repo = TemplateRepository(conn)
    template = EventTemplate(
        category_id=1, name="協力バトル", duration=timedelta(days=7), created_at=NOW
    )
    items = [
        TemplateTask(id=100, category_id=1, title="親", linked=True),
        TemplateTask(id=200, category_id=1, title="子", parent_id=100),
        TemplateTask(
            id=300, category_id=1, title="孫", parent_id=200, due_offset=timedelta(hours=-3)
        ),
    ]
    repo.save(template, items)
    saved = repo.tasks(template.saved_id)
    by_title = {t.title: t for t in saved}
    assert by_title["子"].parent_id == by_title["親"].id
    assert by_title["孫"].parent_id == by_title["子"].id
    assert by_title["孫"].due_offset == timedelta(hours=-3)
    assert by_title["親"].linked
    assert repo.task_counts() == {template.id: 3}
    assert must(repo.get_by_name("協力バトル")).duration == timedelta(days=7)

    # 置き換え
    repo.save(template, [TemplateTask(id=1, category_id=1, title="だけ")])
    assert [t.title for t in repo.tasks(template.saved_id)] == ["だけ"]

    # 親より子を先に渡すと拒み、何も変えない
    with pytest.raises(ValueError, match="親タスクは子タスクより先に並べる"):
        repo.save(template, [TemplateTask(id=2, category_id=1, title="子", parent_id=1)])
    assert [t.title for t in repo.tasks(template.saved_id)] == ["だけ"]

    # 削除でタスクも消える
    repo.delete(template.saved_id)
    assert conn.execute("SELECT COUNT(*) FROM template_tasks").fetchone()[0] == 0


# --- AppStore ---


def fill(store: AppStore) -> Event:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    prep = store.add_task("編成", 1, event_id=raid.id, event_relation=EventRelation.BEFORE)
    weapon = store.add_subtask(prep.saved_id, "武器")
    store.add_subtask(weapon.saved_id, "素材")
    box = store.add_task("報酬交換", 1, event_id=raid.id, progress_target=40)
    store.adjust_progress(box.saved_id, 5)
    store.add_task("ポイント集め", 1, event_id=raid.id, freq=Freq.DAILY, progress_target=3)
    project = store.save_project(Project(category_id=1, name="編成の強化"))
    task = must(store.task(prep.saved_id))
    task.project_id = project.id
    store.update_task(task)
    return raid


def test_store_save_and_create_from_template(store: AppStore) -> None:
    raid = fill(store)
    recorder = []
    store.templates_changed.connect(lambda: recorder.append(1))
    template = store.save_template_from_event(raid.saved_id, "協力バトル")
    assert recorder
    assert [t.name for t in store.templates()] == ["協力バトル"]
    assert store.template_task_counts() == {template.id: 5}

    start = dt("2026-12-18T19:00")
    event = store.create_event_from_template(
        template.saved_id, name="協力バトル(12月)", start_at=start, end_at=start + template.duration
    )
    assert must(store.get_event(event.saved_id)).end_at == dt("2026-12-25T23:59")
    linked = {t.title: t for t in store.event_tasks(event.saved_id)}
    assert set(linked) == {"編成", "報酬交換", "ポイント集め"}
    assert linked["編成"].event_relation is EventRelation.BEFORE
    assert linked["編成"].project_id is None  # プロジェクトは引き継がない
    assert linked["報酬交換"].progress_current == 0
    assert must(store.recurrence(linked["ポイント集め"].saved_id)).event_id == event.id
    weapon = store.subtasks(linked["編成"].saved_id)
    assert [t.title for t in weapon] == ["武器"]
    assert [t.title for t in store.subtasks(weapon[0].saved_id)] == ["素材"]
    assert must(store.todo_row(linked["編成"].saved_id)).due == start  # イベントまで


def test_store_template_name_conflict_replace_rename_delete(store: AppStore) -> None:
    raid = fill(store)
    first = store.save_template_from_event(raid.saved_id, "協力バトル")
    with pytest.raises(TemplateNameError):
        store.save_template_from_event(raid.saved_id, " 協力バトル ")
    store.add_task("追加", 1, event_id=raid.id)
    replaced = store.save_template_from_event(raid.saved_id, "協力バトル", replace=True)
    assert replaced.id == first.id
    assert replaced.created_at == first.created_at
    assert store.template_task_counts()[first.saved_id] == 6

    other = store.save_template_from_event(raid.saved_id, "協力バトル(短縮)")
    with pytest.raises(TemplateNameError):
        store.rename_template(other.saved_id, "協力バトル")
    with pytest.raises(ValueError, match="テンプレート名は空にできない"):
        store.rename_template(other.saved_id, " ")
    store.rename_template(other.saved_id, "協力バトルB")
    assert [t.name for t in store.templates()] == ["協力バトル", "協力バトルB"]
    store.delete_template(first.saved_id)
    assert [t.name for t in store.templates()] == ["協力バトルB"]


def test_store_create_from_template_validation(store: AppStore) -> None:
    raid = fill(store)
    template = store.save_template_from_event(raid.saved_id, "協力バトル")
    with pytest.raises(ValueError, match="終了日時は開始日時より後にする"):
        store.create_event_from_template(
            template.saved_id, name="x", start_at=dt("2026-12-02"), end_at=dt("2026-12-01")
        )
    with pytest.raises(ValueError, match="テンプレートが存在しない"):
        store.create_event_from_template(
            999, name="x", start_at=dt("2026-12-01"), end_at=dt("2026-12-02")
        )


def test_category_used_by_template_cannot_be_deleted(store: AppStore) -> None:
    raid = store.save_event(
        Event(
            category_id=3,
            name="通院",
            start_at=dt("2026-11-20T10:00"),
            end_at=dt("2026-11-20T11:00"),
        )
    )
    store.save_template_from_event(raid.saved_id, "通院")
    store.delete_event(raid.saved_id)
    assert store.category_usage(3) == 1
    with pytest.raises(ValueError, match="使用中の分類は削除できない"):
        store.delete_category(3)


def test_duplicate_still_links_tasks(store: AppStore) -> None:
    raid = fill(store)
    copy = store.duplicate_event(
        raid.saved_id, name="次", start_at=dt("2026-12-18T19:00"), end_at=dt("2026-12-25T23:59")
    )
    assert {t.title for t in store.event_tasks(copy.saved_id)} == {
        "編成",
        "報酬交換",
        "ポイント集め",
    }


# --- JSON ---


def test_export_import_keeps_templates(store: AppStore, tmp_path: Path) -> None:
    raid = fill(store)
    store.save_template_from_event(raid.saved_id, "協力バトル")
    path = tmp_path / "export.json"
    store.export_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["event_templates"]) == 1
    assert len(data["template_tasks"]) == 5
    other = db.open_database(":memory:")
    try:
        transfer.import_data(other, transfer.read_export(path))
        assert transfer.export_data(other, NOW) == transfer.export_data(store._conn, NOW)
    finally:
        other.close()


def test_import_old_file_without_template_tables(store: AppStore, tmp_path: Path) -> None:
    raid = fill(store)
    store.save_template_from_event(raid.saved_id, "協力バトル")
    path = tmp_path / "old.json"
    store.export_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["event_templates"], data["template_tasks"]
    data["schema_version"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    store.import_json(path, tmp_path / "backup")
    assert store.templates() == []
    assert store.get_event(raid.saved_id) is not None

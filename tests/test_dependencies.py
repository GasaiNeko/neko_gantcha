"""タスク間の前後関係(フェーズ11)のテスト。"""

import json
from dataclasses import replace
from datetime import datetime, time
from typing import TYPE_CHECKING

import pytest
from conftest import must

from neko_gantcha.data import db, transfer
from neko_gantcha.domain.dependency import creates_cycle, internal_links, reachable
from neko_gantcha.domain.gantt import GanttInputs, build_gantt
from neko_gantcha.domain.models import (
    Category,
    Dependency,
    Event,
    EventRelation,
    Freq,
    Task,
    TaskStatus,
)
from neko_gantcha.domain.todo import TodoContext, build_row

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {1: Category(id=1, name="ソシャゲA", sort_order=0, daily_reset_time=time(5, 0))}
D = Dependency


# --- ドメイン ---


def test_reachable_and_cycles() -> None:
    deps = [D(1, 2), D(2, 3), D(3, 4), D(10, 11)]
    assert reachable(1, deps) == {2, 3, 4}
    assert reachable(4, deps) == set()
    assert creates_cycle(deps, 4, 1)  # 1→2→3→4→1
    assert creates_cycle(deps, 3, 2)
    assert creates_cycle(deps, 5, 5)  # 自分自身
    assert not creates_cycle(deps, 1, 4)  # 近道は循環ではない
    assert not creates_cycle(deps, 4, 10)
    # 菱形(1→2, 1→3, 2→4, 3→4)も循環ではない
    assert not creates_cycle([D(1, 2), D(1, 3), D(2, 4)], 3, 4)


def test_internal_links() -> None:
    deps = [D(1, 2), D(2, 3), D(3, 9)]
    assert internal_links(deps, {1, 2, 3}) == [D(1, 2), D(2, 3)]


def make_task(task_id: int, title: str, **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-10T09:00")), **overrides
    )


def test_gantt_links_only_between_shown_bars_and_conflicts() -> None:
    tasks = [
        make_task(1, "素材", start_at=dt("2026-11-17T00:00"), due_at=dt("2026-11-19T00:00")),
        make_task(2, "武器", start_at=dt("2026-11-19T00:00"), due_at=dt("2026-11-21T00:00")),
        make_task(3, "編成", start_at=dt("2026-11-20T00:00"), due_at=dt("2026-11-22T00:00")),
        make_task(4, "日付なし"),
        make_task(
            5,
            "済み",
            start_at=dt("2026-11-12T00:00"),
            due_at=dt("2026-11-13T00:00"),
            status=TaskStatus.DONE,
        ),
    ]
    rows = [build_row(t, TodoContext({}, {}, CATEGORIES, {}, NOW)) for t in tasks]
    deps = [D(1, 2), D(2, 3), D(4, 3), D(5, 1)]
    data = build_gantt(GanttInputs(rows, [], CATEGORIES, NOW, dependencies=deps, show_done=True))
    by_task = {row.task.id: index for index, row in enumerate(data.rows) if row.task}
    links = {
        (link.dependency.predecessor_id, link.dependency.successor_id): link for link in data.links
    }
    assert set(links) == {(1, 2), (2, 3), (5, 1)}  # 帯のない「日付なし」は矢印にしない
    assert links[1, 2].from_row == by_task[1]
    assert links[1, 2].to_row == by_task[2]
    assert not links[1, 2].conflict  # 終わりと同時に始まるのはよい
    assert links[2, 3].conflict  # 武器が終わる前に編成が始まる
    assert links[5, 1].done
    assert not links[1, 2].done


def test_gantt_conflict_ignores_provisional_start_of_due_only_task() -> None:
    tasks = [
        make_task(1, "前", start_at=dt("2026-11-20T00:00"), due_at=dt("2026-11-22T00:00")),
        # 期限だけ: 帯は作成日時(11/10)から始まるが、開始としては比べない
        make_task(2, "後(期限だけ)", due_at=dt("2026-11-25T00:00")),
        # 期限が前のタスクの終わりより前 → 食い違い
        make_task(3, "後(先に締切)", due_at=dt("2026-11-21T00:00")),
        # 開始だけ(期限なし)の前のタスクは終わりが決まっていない
        make_task(4, "前(期限なし)", start_at=dt("2026-11-23T00:00")),
        make_task(5, "後", start_at=dt("2026-11-23T00:00"), due_at=dt("2026-11-24T00:00")),
    ]
    rows = [build_row(t, TodoContext({}, {}, CATEGORIES, {}, NOW)) for t in tasks]
    data = build_gantt(
        GanttInputs(rows, [], CATEGORIES, NOW, dependencies=[D(1, 2), D(1, 3), D(4, 5)])
    )
    conflict = {
        (link.dependency.predecessor_id, link.dependency.successor_id): link.conflict
        for link in data.links
    }
    assert conflict == {(1, 2): False, (1, 3): True, (4, 5): False}


# --- AppStore ---


def test_store_add_remove_and_queries(store: AppStore) -> None:
    a = store.add_task("素材", 3)
    b = store.add_task("武器", 3)
    c = store.add_task("編成", 3)
    calls = []
    store.tasks_changed.connect(lambda: calls.append(1))
    store.add_dependency(a.saved_id, b.saved_id)
    store.add_dependency(b.saved_id, c.saved_id)
    assert calls
    assert [t.title for t in store.predecessors(b.saved_id)] == ["素材"]
    assert [t.title for t in store.successors(b.saved_id)] == ["編成"]
    assert [t.title for t in store.waiting_on(c.saved_id)] == ["武器"]
    store.set_task_done(b.saved_id, done=True)
    assert store.waiting_on(c.saved_id) == []

    with pytest.raises(ValueError, match="すでに同じ前後関係がある"):
        store.add_dependency(a.saved_id, b.saved_id)  # 重複
    with pytest.raises(ValueError, match="前後関係が循環する"):
        store.add_dependency(c.saved_id, a.saved_id)  # 循環
    with pytest.raises(ValueError, match="前後関係が循環する"):
        store.add_dependency(a.saved_id, a.saved_id)
    with pytest.raises(ValueError, match="タスクが存在しない"):
        store.add_dependency(a.saved_id, 999)

    # 候補: 自分、既存、循環するものを除く
    assert [t.title for t in store.dependency_candidates(c.saved_id, as_predecessor=False)] == []
    assert [t.title for t in store.dependency_candidates(a.saved_id, as_predecessor=True)] == []
    assert {t.title for t in store.dependency_candidates(c.saved_id, as_predecessor=True)} == {
        "素材"
    }

    store.remove_dependency(a.saved_id, b.saved_id)
    assert store.predecessors(b.saved_id) == []


def test_store_rejects_recurring_and_deletes_with_task(store: AppStore) -> None:
    a = store.add_task("素材", 3)
    b = store.add_task("武器", 3)
    daily = store.add_task("日課", 3, freq=Freq.DAILY)
    with pytest.raises(ValueError, match="繰り返しタスクには前後関係を付けられない"):
        store.add_dependency(daily.saved_id, a.saved_id)
    assert daily.id not in {
        t.id for t in store.dependency_candidates(a.saved_id, as_predecessor=True)
    }
    store.add_dependency(a.saved_id, b.saved_id)
    store.delete_task(a.saved_id)
    assert store.dependencies() == []


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
    box = store.add_task("報酬交換", 1, event_id=raid.id)
    outside = store.add_task("別件", 3)
    store.add_dependency(weapon.saved_id, box.saved_id)
    store.add_dependency(prep.saved_id, box.saved_id)
    store.add_dependency(outside.saved_id, prep.saved_id)  # イベントの外とつながる関係は写さない
    return raid


def titles(store: AppStore, dependencies: Iterable[Dependency]) -> set[tuple[str, str]]:
    return {
        (must(store.task(d.predecessor_id)).title, must(store.task(d.successor_id)).title)
        for d in dependencies
    }


def test_duplicate_copies_internal_dependencies(store: AppStore) -> None:
    raid = fill(store)
    copy = store.duplicate_event(
        raid.saved_id, name="次", start_at=dt("2026-12-18T19:00"), end_at=dt("2026-12-25T23:59")
    )
    new_ids = {t.id for t in store.event_tasks(copy.saved_id)}
    new_ids |= {c.id for t in store.event_tasks(copy.saved_id) for c in store.subtasks(t.saved_id)}
    copied = [d for d in store.dependencies() if d.successor_id in new_ids]
    assert titles(store, copied) == {("武器", "報酬交換"), ("編成", "報酬交換")}
    assert len(store.dependencies()) == 5


def test_template_keeps_dependencies(store: AppStore) -> None:
    raid = fill(store)
    template = store.save_template_from_event(raid.saved_id, "協力バトル")
    assert len(store.template_dependencies(template.saved_id)) == 2
    event = store.create_event_from_template(
        template.saved_id, name="次", start_at=dt("2026-12-18T19:00"), end_at=dt("2026-12-25T23:59")
    )
    box = next(t for t in store.event_tasks(event.saved_id) if t.title == "報酬交換")
    assert {t.title for t in store.predecessors(box.saved_id)} == {"編成", "武器"}


def test_export_import_keeps_dependencies(store: AppStore, tmp_path: Path) -> None:
    raid = fill(store)
    store.save_template_from_event(raid.saved_id, "協力バトル")
    path = tmp_path / "export.json"
    store.export_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["task_dependencies"]) == 3
    assert len(data["template_dependencies"]) == 2
    other = db.open_database(":memory:")
    try:
        transfer.import_data(other, transfer.read_export(path))
        assert transfer.export_data(other, NOW) == transfer.export_data(store._conn, NOW)
    finally:
        other.close()

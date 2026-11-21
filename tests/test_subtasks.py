"""多階層サブタスク(フェーズ8)のテスト。"""

from dataclasses import replace
from datetime import datetime, time
from typing import TYPE_CHECKING

import pytest
from conftest import must

from neko_gantcha.data import db, transfer
from neko_gantcha.domain.duplicate import EventPlan, plan_duplicate
from neko_gantcha.domain.gantt import GanttInputs, RowKind, build_gantt
from neko_gantcha.domain.models import (
    Category,
    Event,
    EventRelation,
    Freq,
    Project,
    Task,
    TaskStatus,
)
from neko_gantcha.domain.project import (
    ancestor_ids,
    descendants,
    leaf_tasks,
    project_progress,
    subtask_progress,
    tree_order,
)
from neko_gantcha.domain.todo import Section, TodoContext, build_row, build_sections
from neko_gantcha.services.store import ItemKind, ItemRef

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from neko_gantcha.domain.gantt import GanttRow
    from neko_gantcha.domain.todo import SectionData
    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {1: Category(id=1, name="ソシャゲA", sort_order=0, daily_reset_time=time(5, 0))}


def make_task(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-10T09:00")), **overrides
    )


def done(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return make_task(task_id, title, status=TaskStatus.DONE, **overrides)


# 1 親
# ├ 2 子A
# │ ├ 3 孫A1(完了)
# │ └ 4 孫A2
# │   └ 5 ひ孫(完了)
# └ 6 子B(完了)
TREE = [
    make_task(1, "親"),
    make_task(2, "子A", parent_task_id=1, sort_order=0),
    done(3, "孫A1", parent_task_id=2, sort_order=0),
    make_task(4, "孫A2", parent_task_id=2, sort_order=1),
    done(5, "ひ孫", parent_task_id=4),
    done(6, "子B", parent_task_id=1, sort_order=1),
]


# --- 階層の扱い ---


def test_descendants_and_ancestors() -> None:
    assert [t.id for t in descendants(1, TREE)] == [2, 3, 4, 5, 6]
    assert [t.id for t in descendants(4, TREE)] == [5]
    assert descendants(6, TREE) == []
    by_id = {t.saved_id: t for t in TREE}
    assert ancestor_ids(by_id[5], by_id) == [4, 2, 1]
    assert ancestor_ids(by_id[1], by_id) == []
    # 途中の親がなければそこで止まる
    del by_id[2]
    assert ancestor_ids(by_id[5], by_id) == [4]


def test_ancestor_ids_stops_on_cycle() -> None:
    a = make_task(1, parent_task_id=2)
    b = make_task(2, parent_task_id=1)
    assert ancestor_ids(a, {1: a, 2: b}) == [2, 1]


def test_tree_order_puts_children_right_after_parent() -> None:
    shuffled = [TREE[5], TREE[4], TREE[0], TREE[3], TREE[2], TREE[1]]
    assert [(t.id, depth) for t, depth in tree_order(shuffled)] == [
        (1, 0),
        (2, 1),
        (3, 2),
        (4, 2),
        (5, 3),
        (6, 1),
    ]
    # 親が含まれないものは最上位
    assert [(t.id, d) for t, d in tree_order(TREE[3:5])] == [(4, 0), (5, 1)]


def test_subtask_progress_counts_leaves_at_any_depth() -> None:
    progress = subtask_progress(TREE)
    # 親: 末端は 孫A1(完了)、ひ孫(完了)、子B(完了) → 3/3
    assert (progress[1].done, progress[1].total) == (3, 3)
    assert (progress[2].done, progress[2].total) == (2, 2)
    assert (progress[4].done, progress[4].total) == (1, 1)
    assert 3 not in progress
    assert 5 not in progress

    reopened = [*TREE[:4], make_task(5, "ひ孫", parent_task_id=4), TREE[5]]
    assert (subtask_progress(reopened)[1].done, subtask_progress(reopened)[1].total) == (2, 3)


def test_project_progress_counts_only_leaves_of_deep_trees() -> None:
    tasks = [
        make_task(1, project_id=10),
        make_task(2, project_id=10, parent_task_id=1),
        done(3, project_id=10, parent_task_id=2),
        make_task(4, project_id=10, parent_task_id=2),
    ]
    assert [t.id for t in leaf_tasks(tasks, set())] == [3, 4]
    progress = project_progress(tasks, set())[10]
    assert (progress.done, progress.total) == (1, 2)


# --- ToDo の入れ子 ---


def todo_sections(tasks: Iterable[Task], **kwargs: bool) -> list[SectionData]:
    return build_sections(tasks, TodoContext({}, {}, CATEGORIES, {}, NOW), **kwargs)


def test_todo_nests_all_levels_under_the_section_row() -> None:
    sections = todo_sections(TREE, show_done=True)
    standing = next(s for s in sections if s.section is Section.STANDING)
    assert [row.task.id for row in standing.rows] == [1]
    children = standing.children
    assert [r.task.id for r in children[1]] == [2, 6]
    assert [r.task.id for r in children[2]] == [3, 4]
    assert [r.task.id for r in children[4]] == [5]
    # 入れ子のものは完了済みのセクションには出ない
    assert all(s.section is not Section.DONE for s in sections)


def test_todo_child_of_hidden_parent_becomes_own_row_with_its_children() -> None:
    tasks = [
        done(1, "親(完了)"),
        make_task(2, "子", parent_task_id=1),
        make_task(3, "孫", parent_task_id=2),
    ]
    # show_done でないときは完了済みを渡さない(store.tasks() と同じ)
    sections = todo_sections([t for t in tasks if t.status is not TaskStatus.DONE])
    standing = next(s for s in sections if s.section is Section.STANDING)
    assert [row.task.id for row in standing.rows] == [2]
    assert [r.task.id for r in standing.children[2]] == [3]


# --- ガントの入れ子 ---


def gantt_rows(tasks: Iterable[Task]) -> list[GanttRow]:
    rows = [build_row(t, TodoContext({}, {}, CATEGORIES, {}, NOW)) for t in tasks]
    return build_gantt(GanttInputs(rows, [], CATEGORIES, NOW)).rows


def test_gantt_nests_deep_subtasks_and_skips_undated_middle() -> None:
    tasks = [
        make_task(1, "親", start_at=dt("2026-11-18T00:00"), due_at=dt("2026-11-25T00:00")),
        make_task(2, "子(日付なし)", parent_task_id=1),
        make_task(3, "孫", parent_task_id=2, due_at=dt("2026-11-20T00:00")),
        make_task(4, "ひ孫", parent_task_id=3, due_at=dt("2026-11-19T00:00")),
    ]
    rows = [(r.label, r.depth) for r in gantt_rows(tasks) if r.kind is RowKind.TASK]
    # 子は帯がないので出ず、孫は親の直下、ひ孫は孫の下に並ぶ
    assert rows == [("親", 2), ("孫", 3), ("ひ孫", 4)]


def test_gantt_orphan_when_no_ancestor_has_bar() -> None:
    tasks = [
        make_task(1, "親(日付なし)"),
        make_task(2, "子", parent_task_id=1, due_at=dt("2026-11-20T00:00")),
    ]
    rows = [(r.label, r.depth) for r in gantt_rows(tasks) if r.kind is RowKind.TASK]
    assert rows == [("子", 2)]


# --- 複製の並び ---


def test_plan_duplicate_orders_every_level_after_its_parent() -> None:
    event = Event(
        id=5, category_id=1, name="協力バトル", start_at=NOW, end_at=dt("2026-11-20T00:00")
    )
    tasks = [
        make_task(30, "孫", parent_task_id=20),
        make_task(20, "子", parent_task_id=10),
        make_task(10, "親", event_id=5, event_relation=EventRelation.DURING),
    ]
    _, copies = plan_duplicate(
        event, tasks, {}, plan=EventPlan("次", dt("2026-12-16T12:00"), dt("2026-12-20")), now=NOW
    )
    assert [c.source_id for c in copies] == [10, 20, 30]


# --- AppStore ---


def make_tree(store: AppStore) -> tuple[Task, Task, Task, Task]:
    parent = store.add_task("親", 1)
    child = store.add_subtask(parent.saved_id, "子")
    grandchild = store.add_subtask(child.saved_id, "孫")
    great = store.add_subtask(grandchild.saved_id, "ひ孫")
    return parent, child, grandchild, great


def test_store_adds_subtasks_at_any_depth(store: AppStore) -> None:
    parent, child, grandchild, great = make_tree(store)
    assert must(store.task(great.saved_id)).parent_task_id == grandchild.id
    assert [t.id for t in store.subtasks(child.saved_id)] == [grandchild.id]
    assert store.descendant_count(parent.saved_id) == 3
    progress = store.subtask_progress()
    assert (progress[parent.saved_id].done, progress[parent.saved_id].total) == (0, 1)

    store.set_task_done(great.saved_id, done=True)
    assert store.subtask_progress()[parent.saved_id].done == 1


def test_store_subtask_rules_with_recurring(store: AppStore) -> None:
    _, child, grandchild, _ = make_tree(store)
    daily = store.add_task("日課", 1, freq=Freq.DAILY)
    with pytest.raises(ValueError, match="繰り返しタスクにはサブタスクを作れない"):
        store.add_subtask(daily.saved_id, "日課の子")
    # 途中の階層のタスクも末端のタスクも繰り返しにできない
    with pytest.raises(
        ValueError, match="サブタスクと、サブタスクを持つタスクは繰り返しにできない"
    ):
        store.update_task(must(store.task(child.saved_id)), freq=Freq.DAILY)
    with pytest.raises(
        ValueError, match="サブタスクと、サブタスクを持つタスクは繰り返しにできない"
    ):
        store.update_task(must(store.task(grandchild.saved_id)), freq=Freq.WEEKLY)


def test_store_category_and_project_follow_to_all_levels(store: AppStore) -> None:
    parent, child, grandchild, great = make_tree(store)
    project = store.save_project(Project(category_id=3, name="家の用事"))
    task = must(store.task(parent.saved_id))
    task.category_id = 3
    task.project_id = project.id
    store.update_task(task)
    for task_id in (child.saved_id, grandchild.saved_id, great.saved_id):
        saved = must(store.task(task_id))
        assert (saved.category_id, saved.project_id) == (3, project.id)


def test_store_delete_removes_all_levels_and_clears_selection(store: AppStore) -> None:
    parent, child, grandchild, great = make_tree(store)
    other = store.add_task("別", 1)
    store.select(ItemRef(ItemKind.TASK, great.saved_id))
    store.delete_task(child.saved_id)
    assert store.task(grandchild.saved_id) is None
    assert store.task(great.saved_id) is None
    assert store.task(parent.saved_id) is not None
    assert store.task(other.saved_id) is not None
    assert store.selected is None


def test_store_duplicate_event_copies_all_levels(store: AppStore) -> None:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    parent = store.add_task("編成", 1, event_id=raid.id)
    child = store.add_subtask(parent.saved_id, "武器")
    store.add_subtask(child.saved_id, "素材")
    new_event = store.duplicate_event(
        raid.saved_id,
        name="協力バトル(次)",
        start_at=dt("2026-12-18T19:00"),
        end_at=dt("2026-12-25T23:59"),
    )
    new_parent = next(t for t in store.event_tasks(new_event.saved_id))
    new_child = store.subtasks(new_parent.saved_id)
    assert [t.title for t in new_child] == ["武器"]
    assert [t.title for t in store.subtasks(new_child[0].saved_id)] == ["素材"]
    # 元の木はそのまま
    assert [t.title for t in store.subtasks(child.saved_id)] == ["素材"]


def test_export_import_keeps_deep_tree(store: AppStore, tmp_path: Path) -> None:
    parent, _, _, great = make_tree(store)
    path = tmp_path / "export.json"
    store.export_json(path)
    other = db.open_database(":memory:")
    try:
        transfer.import_data(other, transfer.read_export(path))
        rows = dict(other.execute("SELECT id, parent_task_id FROM tasks").fetchall())
    finally:
        other.close()
    assert rows[parent.id] is None
    chain = [great.id]
    while rows[chain[-1]] is not None:
        chain.append(rows[chain[-1]])
    assert chain[-1] == parent.id
    assert len(chain) == 4

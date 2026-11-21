"""プロジェクト、サブタスク、イベント複製のテスト。"""

from dataclasses import replace
from datetime import date, datetime, time
from typing import TYPE_CHECKING

import pytest
from conftest import Recorder, must

from neko_gantcha.domain.duplicate import EventPlan, plan_duplicate
from neko_gantcha.domain.gantt import GanttInputs, RowKind, build_gantt, row_index
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
from neko_gantcha.domain.project import (
    Progress,
    leaf_tasks,
    project_deadline,
    project_progress,
    project_range,
    subtask_progress,
)
from neko_gantcha.domain.todo import Section, TodoContext, build_row, build_sections
from neko_gantcha.services.store import ItemKind, ItemRef

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from neko_gantcha.domain.todo import SectionData, TodoRow
    from neko_gantcha.services.store import AppStore


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


NOW = dt("2026-11-16T12:00")
CATEGORIES = {
    1: Category(id=1, name="ソシャゲA", sort_order=0, daily_reset_time=time(5, 0)),
    3: Category(id=3, name="日常", sort_order=2),
}


def make_task(task_id: int, title: str = "タスク", **overrides: object) -> Task:
    return replace(
        Task(id=task_id, category_id=1, title=title, created_at=dt("2026-11-10T09:00")), **overrides
    )


def done(task_id: int, **overrides: object) -> Task:
    return replace(make_task(task_id), status=TaskStatus.DONE, **overrides)


# --- 完了率とサブタスクの集計 ---


def test_progress_ratio_and_text() -> None:
    assert Progress(1, 4).ratio == pytest.approx(0.25)
    assert Progress(1, 3).percent_text() == "33%"
    assert Progress(0, 0).ratio is None
    assert Progress(0, 0).percent_text() == "—"


def test_leaf_tasks_skip_parents_and_recurring() -> None:
    tasks = [
        make_task(1),
        make_task(2, parent_task_id=1),
        make_task(3, parent_task_id=1),
        make_task(4),
        make_task(5),
    ]
    assert [t.id for t in leaf_tasks(tasks, {5})] == [2, 3, 4]


def test_project_progress_counts_leaves_per_project() -> None:
    tasks = [
        make_task(1, project_id=10),
        done(2, project_id=10, parent_task_id=1),
        make_task(3, project_id=10, parent_task_id=1),
        done(4, project_id=10),
        make_task(5, project_id=10),  # 繰り返しは数えない
        done(6, project_id=20),
        make_task(7),
    ]
    result = project_progress(tasks, {5})
    assert result == {10: Progress(2, 3), 20: Progress(1, 1)}


def test_subtask_progress() -> None:
    tasks = [make_task(1), done(2, parent_task_id=1), make_task(3, parent_task_id=1)]
    assert subtask_progress(tasks) == {1: Progress(1, 2)}


# --- プロジェクトの期間 ---


def make_project(**overrides: object) -> Project:
    return replace(Project(id=10, category_id=1, name="編成の強化"), **overrides)


def test_project_deadline_prefers_target_date_then_event() -> None:
    event = Event(
        id=5,
        category_id=1,
        name="協力バトル",
        start_at=dt("2026-12-01T19:00"),
        end_at=dt("2026-12-08T23:59"),
    )
    assert project_deadline(make_project(target_date=date(2026, 11, 30)), event) == dt(
        "2026-11-30T23:59"
    )
    assert project_deadline(make_project(event_id=5), event) == event.start_at
    assert project_deadline(make_project(), None) is None


def test_project_range_from_first_task_to_deadline() -> None:
    project = make_project(target_date=date(2026, 11, 30))
    ranges = [
        (dt("2026-11-14T00:00"), dt("2026-11-20T00:00")),
        (dt("2026-11-12T00:00"), dt("2026-11-13T00:00")),
    ]
    assert project_range(project, ranges, [], event=None) == (
        dt("2026-11-12T00:00"),
        dt("2026-11-30T23:59"),
        False,
    )


def test_project_range_without_dated_tasks_starts_at_creation() -> None:
    project = make_project(target_date=date(2026, 11, 30))
    result = project_range(
        project, [], [dt("2026-11-11T10:00"), dt("2026-11-09T08:00")], event=None
    )
    assert result == (dt("2026-11-09T08:00"), dt("2026-11-30T23:59"), False)


def test_project_range_without_deadline_is_open_ended() -> None:
    project = make_project()
    ranges = [(dt("2026-11-14T00:00"), dt("2026-11-20T00:00"))]
    assert project_range(project, ranges, [], event=None) == (
        dt("2026-11-14T00:00"),
        dt("2026-11-20T00:00"),
        True,
    )
    assert project_range(project, [], [], event=None) is None


def test_project_range_only_deadline_is_one_day() -> None:
    project = make_project(target_date=date(2026, 11, 30))
    start, end, open_ended = must(project_range(project, [], [], event=None))
    assert (end - start).days == 1
    assert not open_ended


# --- ToDo のサブタスク ---


def sections_of(tasks: Iterable[Task], *, show_done: bool = False) -> list[SectionData]:
    return build_sections(tasks, TodoContext({}, {}, CATEGORIES, {}, NOW), show_done=show_done)


def test_subtasks_are_nested_under_parent() -> None:
    tasks = [
        make_task(1, "親", due_at=dt("2026-11-20T00:00")),
        make_task(3, "子B", parent_task_id=1, sort_order=1),
        make_task(2, "子A", parent_task_id=1, sort_order=0, due_at=dt("2026-11-17T00:00")),
        make_task(4, "恒常"),
    ]
    sections = {data.section: data for data in sections_of(tasks)}
    dated = sections[Section.DATED]
    assert [row.task.title for row in dated.rows] == ["親"]
    assert [row.task.title for row in dated.children[1]] == ["子A", "子B"]
    assert [row.task.title for row in sections[Section.STANDING].rows] == ["恒常"]


def test_subtask_of_hidden_parent_is_listed_on_its_own() -> None:
    # 親が完了して一覧に出ないときは、サブタスクを自分の行として出す
    tasks = [make_task(2, "子", parent_task_id=1)]
    sections = sections_of(tasks)
    assert [row.task.title for row in sections[0].rows] == ["子"]
    assert sections[0].children == {}


# --- ガントのプロジェクト行 ---


def row_of(task: Task, events: Mapping[int, Event] | None = None) -> TodoRow:
    return build_row(task, TodoContext(events or {}, {}, CATEGORIES, {}, NOW))


def test_gantt_groups_project_tasks_and_nests_subtasks() -> None:
    event = Event(
        id=5,
        category_id=1,
        name="協力バトル",
        start_at=dt("2026-11-20T19:00"),
        end_at=dt("2026-11-27T23:59"),
    )
    project = make_project(target_date=date(2026, 11, 30))
    tasks = [
        make_task(1, "編成", project_id=10, due_at=dt("2026-11-25T00:00")),
        make_task(2, "武器", project_id=10, parent_task_id=1, due_at=dt("2026-11-18T00:00")),
        make_task(3, "報酬交換", event_id=5, event_relation=EventRelation.DURING),
        make_task(4, "準備", project_id=10, event_id=5, event_relation=EventRelation.BEFORE),
        make_task(5, "買い物", category_id=3, due_at=dt("2026-11-18T00:00")),
    ]
    events = {5: event}
    rows = [row_of(task, events) for task in tasks]
    data = build_gantt(
        GanttInputs(
            rows,
            [event],
            CATEGORIES,
            NOW,
            projects=[project],
            project_rows={10: [rows[0], rows[1], rows[3]]},
            progress={10: Progress(1, 4)},
            all_events=events,
        )
    )
    assert [(r.kind, r.label, r.depth) for r in data.rows] == [
        (RowKind.CATEGORY, "ソシャゲA", 0),
        (RowKind.EVENT, "協力バトル", 1),
        (RowKind.TASK, "報酬交換", 2),
        (RowKind.PROJECT, "編成の強化", 1),
        (RowKind.TASK, "準備", 2),  # プロジェクトに属するタスクはプロジェクトの下
        (RowKind.TASK, "編成", 2),
        (RowKind.TASK, "武器", 3),  # サブタスクは親のすぐ下
        (RowKind.CATEGORY, "日常", 0),
        (RowKind.GROUP, "イベントなし", 1),
        (RowKind.TASK, "買い物", 2),
    ]
    project_row = data.rows[3]
    assert project_row.progress == pytest.approx(0.25)
    assert project_row.start == dt("2026-11-10T09:00")  # 最初のタスクの帯(作成日時から)
    assert project_row.end == dt("2026-11-30T23:59")
    assert row_index(data, project_id=10) == 3


def test_gantt_hides_done_and_archived_projects() -> None:
    active = make_project(id=10, name="進行中")
    finished = make_project(
        id=11, name="完了", status=ProjectStatus.DONE, target_date=date(2026, 11, 20)
    )
    archived = make_project(id=12, name="アーカイブ", target_date=date(2026, 11, 20))
    archived.status = ProjectStatus.ARCHIVED
    labels = [
        r.label
        for r in build_gantt(
            GanttInputs([], [], CATEGORIES, NOW, projects=[active, finished, archived])
        ).rows
    ]
    assert labels == ["ソシャゲA", "進行中"]
    labels = [
        r.label
        for r in build_gantt(
            GanttInputs(
                [], [], CATEGORIES, NOW, projects=[active, finished, archived], show_done=True
            )
        ).rows
    ]
    assert labels == ["ソシャゲA", "完了", "進行中"]


# --- イベントの複製 ---


def test_plan_duplicate_shifts_dates_and_resets_progress() -> None:
    event = Event(
        id=5,
        category_id=1,
        name="協力バトル",
        start_at=dt("2026-11-20T19:00"),
        end_at=dt("2026-11-27T23:59"),
        url="https://example.com",
        note="メモ",
    )
    tasks = [
        done(
            1,
            title="報酬交換",
            event_id=5,
            event_relation=EventRelation.DURING,
            progress_target=40,
            progress_current=40,
            completed_at=dt("2026-11-27T00:00"),
            project_id=10,
        ),
        make_task(
            2,
            "編成",
            event_id=5,
            event_relation=EventRelation.BEFORE,
            start_at=dt("2026-11-15T00:00"),
        ),
        make_task(3, "ポイント集め", event_id=5),
        make_task(4, "子", parent_task_id=2),
    ]
    recurrences = {3: Recurrence(task_id=3, freq=Freq.DAILY, event_id=5)}
    new_event, copies = plan_duplicate(
        event,
        tasks,
        recurrences,
        plan=EventPlan(" 協力バトル(次回) ", dt("2026-12-18T19:00"), dt("2026-12-25T23:59")),
        now=NOW,
    )
    assert new_event.id is None
    assert new_event.name == "協力バトル(次回)"
    assert (new_event.url, new_event.note) == ("https://example.com", "メモ")
    by_title = {copy.task.title: copy for copy in copies}
    box = by_title["報酬交換"].task
    assert box.status is TaskStatus.TODO
    assert box.progress_current == 0
    assert box.completed_at is None
    assert box.project_id is None
    assert box.id is None
    assert box.created_at == NOW
    assert by_title["編成"].task.start_at == dt("2026-12-13T00:00")  # 28日ずらす
    assert by_title["ポイント集め"].freq is Freq.DAILY
    titles = [copy.task.title for copy in copies]
    assert titles.index("子") == titles.index("編成") + 1  # サブタスクは親のすぐ後
    assert by_title["子"].source_id == 4
    assert by_title["子"].task.parent_task_id == 2


# --- AppStore ---


def test_store_subtasks(store: AppStore) -> None:
    parent = store.add_task("編成を組む", 1)
    project = store.save_project(Project(category_id=1, name="編成の強化"))
    parent.project_id = project.id
    store.update_task(parent)
    child = store.add_subtask(parent.saved_id, "武器を集める")
    second = store.add_subtask(parent.saved_id, "サポート")
    assert child.parent_task_id == parent.id
    assert child.project_id == project.id
    assert [t.id for t in store.subtasks(parent.saved_id)] == [child.id, second.id]
    store.set_task_done(child.saved_id, done=True)
    assert store.subtask_progress()[parent.saved_id] == Progress(1, 2)
    assert store.project_progress()[project.saved_id] == Progress(1, 2)  # 親は数えない

    # 親の分類を変えるとサブタスクも合わせる
    parent = must(store.task(parent.saved_id))
    parent.category_id = 3
    store.update_task(parent)
    assert must(store.task(child.saved_id)).category_id == 3

    # 繰り返しとサブタスクは組み合わせられない
    with pytest.raises(
        ValueError, match="サブタスクと、サブタスクを持つタスクは繰り返しにできない"
    ):
        store.update_task(must(store.task(parent.saved_id)), freq=Freq.DAILY)
    with pytest.raises(
        ValueError, match="サブタスクと、サブタスクを持つタスクは繰り返しにできない"
    ):
        store.update_task(must(store.task(child.saved_id)), freq=Freq.DAILY)
    daily = store.add_task("日課", 1, freq=Freq.DAILY)
    with pytest.raises(ValueError, match="繰り返しタスクにはサブタスクを作れない"):
        store.add_subtask(daily.saved_id, "子")

    # 親を消すとサブタスクも消え、選択も外れる
    store.select(ItemRef(ItemKind.TASK, child.saved_id))
    store.delete_task(parent.saved_id)
    assert store.task(child.saved_id) is None
    assert store.selected is None


def test_store_projects_list_and_filter(store: AppStore) -> None:
    a = store.save_project(Project(category_id=1, name="B", target_date=date(2026, 12, 1)))
    b = store.save_project(Project(category_id=1, name="A", target_date=date(2026, 11, 20)))
    c = store.save_project(Project(category_id=3, name="家計簿"))
    d = store.save_project(Project(category_id=1, name="済", status=ProjectStatus.DONE))
    assert [p.id for p in store.projects()] == [b.id, a.id, c.id]
    store.set_filter(show_done=True)
    assert d.id in [p.id for p in store.projects()]
    store.set_filter(show_done=False, category_id=1)
    assert [p.id for p in store.projects()] == [b.id, a.id]

    # プロジェクトを選ぶと分類も合わせ、分類を変えると外れる
    store.set_filter(project_id=c.id)
    assert store.filter.category_id == 3
    assert store.filter.project_id == c.id
    store.set_filter(category_id=1)
    assert store.filter.project_id is None
    store.set_filter(category_id=None, project_id=a.id)
    assert store.filter.category_id is None
    assert store.filter.project_id == a.id


def test_store_project_filter_limits_views(store: AppStore) -> None:
    project = store.save_project(Project(category_id=1, name="編成の強化"))
    store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    inside = store.add_task("編成", 1, project_id=project.id, due_at=dt("2026-11-20T00:00"))
    store.add_task("買い物", 3, due_at=dt("2026-11-18T00:00"))
    store.set_filter(project_id=project.id)
    assert [t.id for t in store.tasks()] == [inside.id]
    labels = [row.label for row in store.gantt().rows]
    assert labels == ["ソシャゲA", "編成の強化", "編成"]
    assert store.calendar_month(2026, 11).weeks  # イベントは出ないが落ちない
    assert store.shown_in_gantt(ItemRef(ItemKind.PROJECT, project.saved_id))


def test_store_save_and_delete_project(store: AppStore) -> None:
    projects = Recorder(store.projects_changed)
    project = store.save_project(Project(category_id=1, name="  編成の強化 "))
    assert project.name == "編成の強化"
    assert projects.count == 1
    task = store.add_task("編成", 1, project_id=project.id)
    with pytest.raises(ValueError, match="プロジェクト名は空にできない"):
        store.save_project(Project(category_id=1, name=" "))

    # 分類を変えると配下タスクも合わせる
    project.category_id = 3
    store.save_project(project)
    assert must(store.task(task.saved_id)).category_id == 3

    store.select(ItemRef(ItemKind.PROJECT, project.saved_id))
    store.set_filter(project_id=project.id)
    store.delete_project(project.saved_id)
    assert store.project(project.saved_id) is None
    assert must(store.task(task.saved_id)).project_id is None
    assert store.selected is None
    assert store.filter.project_id is None


@pytest.mark.usefixtures("clock")
def test_store_duplicate_event(store: AppStore) -> None:
    raid = store.save_event(
        Event(
            category_id=1,
            name="協力バトル",
            start_at=dt("2026-11-20T19:00"),
            end_at=dt("2026-11-27T23:59"),
        )
    )
    box = store.add_task("報酬交換", 1, event_id=raid.id, progress_target=40)
    store.adjust_progress(box.saved_id, 40)
    prep = store.add_task("編成", 1, event_id=raid.id, event_relation=EventRelation.BEFORE)
    child = store.add_subtask(prep.saved_id, "武器")
    store.add_task("ポイント集め", 1, event_id=raid.id, freq=Freq.DAILY)
    store.add_task("無関係", 1)

    start = store.suggest_duplicate_start(raid)
    assert start == dt("2026-12-18T19:00")
    new = store.duplicate_event(
        raid.saved_id, name="協力バトル", start_at=start, end_at=dt("2026-12-25T23:59")
    )
    assert new.id != raid.id
    assert must(store.get_event(new.saved_id)).start_at == start
    copied = {t.title: t for t in store.event_tasks(new.saved_id)}
    assert set(copied) == {"報酬交換", "編成", "ポイント集め"}
    assert copied["報酬交換"].status is TaskStatus.TODO
    assert copied["報酬交換"].progress_current == 0
    assert copied["編成"].event_relation is EventRelation.BEFORE
    recurrence = must(store.recurrence(copied["ポイント集め"].saved_id))
    assert recurrence.freq is Freq.DAILY
    assert recurrence.event_id == new.id
    sub = store.subtasks(copied["編成"].saved_id)
    assert [t.title for t in sub] == ["武器"]
    assert sub[0].id != child.id
    # 元のイベントのタスクはそのまま
    assert must(store.task(box.saved_id)).status is TaskStatus.DONE
    assert len(store.event_tasks(raid.saved_id)) == 3

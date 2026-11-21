"""イベントのテンプレート。協力バトルのように毎回同じタスク構成になるイベントを、ひな形として残す。

複製(duplicate.py)が「前回のイベントから日付をずらして写す」のに対し、テンプレートは
日付を持たない形(イベント開始からのずれ)で保存しておき、好きな日程でイベントを作る。
Qt にも DB にも依存しない。保存と作成は AppStore が行う。
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from neko_gantcha.domain.duplicate import TaskCopy
from neko_gantcha.domain.models import Event, EventTemplate, Task, TaskStatus, TemplateTask
from neko_gantcha.domain.project import tree_order

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from neko_gantcha.domain.duplicate import EventPlan
    from neko_gantcha.domain.models import Recurrence


def _offset(value: datetime | None, origin: datetime) -> timedelta | None:
    """基準の日時 origin からのずれを返す。分単位に切り捨てる(保存は分単位のため)。

    Returns:
        ずれ。value が None なら None。
    """
    if value is None:
        return None
    return timedelta(minutes=(value - origin) // timedelta(minutes=1))


def make_template(
    event: Event,
    tasks: Iterable[Task],
    recurrences: Mapping[int, Recurrence],
    *,
    name: str,
    now: datetime,
) -> tuple[EventTemplate, list[TemplateTask]]:
    """イベントと配下タスクからテンプレートを作る。

    完了状態と進捗は持たない。プロジェクトへの所属はそのときのものなので残さない。
    返すタスクは親が子より先に来る順で、id と parent_id は元のタスクの id(仮の番号)。

    Args:
        event: もとにするイベント。
        tasks: イベントに紐付くタスクと、そのサブタスク(孫以下も)。
        recurrences: タスク id → 繰り返しの設定。
        name: テンプレートの名前。空ならイベントの名前を使う。
        now: 現在の日時(テンプレートの作成日時になる)。

    Returns:
        (テンプレート, テンプレートの中のタスク)。
    """
    template = EventTemplate(
        category_id=event.category_id,
        name=name.strip() or event.name,
        duration=event.end_at - event.start_at,
        start_time=event.start_at.time().replace(second=0, microsecond=0),
        url=event.url,
        note=event.note,
        created_at=now,
    )
    origin = event.start_at
    result: list[TemplateTask] = []
    for task, _ in tree_order(tasks):
        recurrence = recurrences.get(task.id)
        until = recurrence.active_until if recurrence else None
        result.append(
            TemplateTask(
                id=task.id,
                parent_id=task.parent_task_id,
                category_id=task.category_id,
                title=task.title,
                note=task.note,
                priority=task.priority,
                linked=task.event_id == event.id,
                event_relation=task.event_relation if task.event_id == event.id else None,
                start_offset=_offset(task.start_at, origin),
                due_offset=_offset(task.due_at, origin),
                progress_target=task.progress_target,
                freq=recurrence.freq if recurrence else None,
                active_from_offset=_offset(recurrence.active_from, origin) if recurrence else None,
                active_until_offset=_offset(until, origin) if recurrence else None,
                sort_order=task.sort_order,
            )
        )
    # 親がテンプレートに入らなかったもの(ありえないが念のため)は最上位にする
    ids = {task.id for task in result}
    for task in result:
        if task.parent_id not in ids:
            task.parent_id = None
    return template, result


def plan_from_template(
    template: EventTemplate,
    tasks: Iterable[TemplateTask],
    *,
    plan: EventPlan,
    now: datetime,
) -> tuple[Event, list[TaskCopy]]:
    """テンプレートから作るイベントと、配下タスクの中身を返す。

    TaskCopy.source_id はテンプレートの中のタスクの id、task.parent_task_id はその親の id
    (保存時に新しいタスクの id へ付け替える)。イベントに紐付くタスクは linked が真になる。

    Args:
        template: もとにするテンプレート。
        tasks: テンプレートの中のタスク。
        plan: 新しいイベントの名前と日程。名前が空ならテンプレートの名前を使う。
        now: 現在の日時(タスクの作成日時になる)。

    Returns:
        (新しいイベント, 配下タスクの中身)。
    """
    event = Event(
        category_id=template.category_id,
        name=plan.name.strip() or template.name,
        start_at=plan.start_at,
        end_at=plan.end_at,
        url=template.url,
        note=template.note,
    )
    by_id = {task.id: task for task in tasks}
    start_at = plan.start_at
    copies: list[TaskCopy] = []
    for item in _tree(by_id.values()):
        task = Task(
            category_id=item.category_id,
            title=item.title,
            created_at=now,
            note=item.note,
            status=TaskStatus.TODO,
            priority=item.priority,
            start_at=_at(start_at, item.start_offset),
            due_at=_at(start_at, item.due_offset),
            event_relation=item.event_relation if item.linked else None,
            parent_task_id=item.parent_id,
            progress_current=0 if item.progress_target is not None else None,
            progress_target=item.progress_target,
            sort_order=item.sort_order,
        )
        copies.append(
            TaskCopy(
                item.saved_id,
                task,
                freq=item.freq,
                active_from=_at(start_at, item.active_from_offset),
                active_until=_at(start_at, item.active_until_offset),
                linked=item.linked,
            )
        )
    return event, copies


def _at(origin: datetime, offset: timedelta | None) -> datetime | None:
    return None if offset is None else origin + offset


def _tree(tasks: Iterable[TemplateTask]) -> list[TemplateTask]:
    """親が子より先に来る順(同じ親の中は並び順)に並べる。

    Returns:
        並べたタスク。
    """
    tasks = list(tasks)
    ids = {task.id for task in tasks}
    children: dict[int | None, list[TemplateTask]] = {}
    for task in tasks:
        parent = task.parent_id if task.parent_id in ids else None
        children.setdefault(parent, []).append(task)
    for siblings in children.values():
        siblings.sort(key=lambda t: (t.sort_order, t.id or 0))
    result: list[TemplateTask] = []
    pending = list(reversed(children.get(None, [])))
    while pending:
        task = pending.pop()
        result.append(task)
        pending.extend(reversed(children.get(task.id, [])))
    return result


def template_tree(tasks: Iterable[TemplateTask]) -> list[tuple[TemplateTask, int]]:
    """表示用に、親が子より先に来る順で (タスク, 深さ) を返す。

    Args:
        tasks: テンプレートの中のタスク。

    Returns:
        (タスク, 深さ) の一覧。
    """
    tasks = list(tasks)
    depth: dict[int | None, int] = {None: -1}
    result: list[tuple[TemplateTask, int]] = []
    for task in _tree(tasks):
        level = depth.get(task.parent_id, -1) + 1
        depth[task.id] = level
        result.append((task, level))
    return result


def suggest_start(template: EventTemplate, today: date) -> datetime:
    """テンプレートから作るときの開始日時の初期値を返す。

    Args:
        template: もとにするテンプレート。
        today: 今日の日付。

    Returns:
        今日の、いつもの開始時刻。
    """
    return datetime.combine(today, template.start_time)


def describe_offset(offset: timedelta) -> str:
    """イベント開始からのずれを表示用の文字列にする。

    Args:
        offset: イベント開始からのずれ。

    Returns:
        `開始の2日前`、`開始時刻`、`開始の3日後` のような文字列。
    """
    if offset == timedelta(0):
        return "開始時刻"
    days, rest = divmod(abs(offset), timedelta(days=1))
    hours = rest // timedelta(hours=1)
    minutes = (rest % timedelta(hours=1)) // timedelta(minutes=1)
    parts = []
    if days:
        parts.append(f"{days}日")
    if hours:
        parts.append(f"{hours}時間")
    if minutes:
        parts.append(f"{minutes}分")
    return f"開始の{''.join(parts)}{'前' if offset < timedelta(0) else '後'}"

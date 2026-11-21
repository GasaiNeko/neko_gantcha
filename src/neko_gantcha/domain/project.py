"""プロジェクトの完了率と期間、サブタスクの階層と集計。Qt には依存しない。

完了率は「繰り返しでなく、サブタスクを持たないタスク」を1件ずつ数える。
サブタスクを持つタスクは、そのサブタスクで進み具合を表すので数えない。
サブタスクは何階層でも作れる。
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import TaskStatus

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from neko_gantcha.domain.models import Event, Project, Task

_MIN_BAR = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Progress:
    """完了数と対象数。

    Attributes:
        done: 完了した件数。
        total: 対象の件数。
    """

    done: int
    total: int

    @property
    def ratio(self) -> float | None:
        """完了率(0〜1)。

        Returns:
            完了率。対象のタスクがなければ None。
        """
        return self.done / self.total if self.total else None

    def percent_text(self) -> str:
        """完了率を表示用の文字列にする。

        Returns:
            `40%` の形の文字列。対象のタスクがなければ `—`。
        """
        ratio = self.ratio
        return "—" if ratio is None else f"{round(ratio * 100)}%"


def leaf_tasks(tasks: Iterable[Task], recurring_ids: Collection[int]) -> list[Task]:
    """完了率の対象のタスク(繰り返しでなく、サブタスクを持たないもの)を返す。

    Args:
        tasks: 候補のタスク。
        recurring_ids: 繰り返しタスクの id。

    Returns:
        対象のタスク。
    """
    tasks = list(tasks)
    parents = {task.parent_task_id for task in tasks if task.parent_task_id is not None}
    return [task for task in tasks if task.id not in recurring_ids and task.id not in parents]


def project_progress(tasks: Iterable[Task], recurring_ids: Collection[int]) -> dict[int, Progress]:
    """プロジェクトごとの完了数と対象数を数える。

    Args:
        tasks: タスク。完了済みも含めて渡す。
        recurring_ids: 繰り返しタスクの id。

    Returns:
        プロジェクト id → 進み具合。
    """
    counts: dict[int, list[int]] = {}
    for task in leaf_tasks(tasks, recurring_ids):
        if task.project_id is None:
            continue
        done_total = counts.setdefault(task.project_id, [0, 0])
        done_total[1] += 1
        if task.status is TaskStatus.DONE:
            done_total[0] += 1
    return {project_id: Progress(done, total) for project_id, (done, total) in counts.items()}


def children_map(tasks: Iterable[Task]) -> dict[int, list[Task]]:
    """親タスク id → 直下のサブタスク(並び順どおり)の対応を作る。

    Args:
        tasks: タスク。

    Returns:
        親タスク id → 直下のサブタスクの一覧。
    """
    result: dict[int, list[Task]] = {}
    for task in tasks:
        if task.parent_task_id is not None:
            result.setdefault(task.parent_task_id, []).append(task)
    for children in result.values():
        children.sort(key=lambda t: (t.sort_order, t.id or 0))
    return result


def descendants(task_id: int, tasks: Iterable[Task]) -> list[Task]:
    """task_id の下にあるすべてのサブタスクを返す。

    Args:
        task_id: 親タスクの id。
        tasks: 探す範囲のタスク。

    Returns:
        孫以下も含むサブタスク。親が先に来る順。
    """
    children = children_map(tasks)
    result: list[Task] = []
    pending = list(reversed(children.get(task_id, [])))
    while pending:
        task = pending.pop()
        result.append(task)
        pending.extend(reversed(children.get(task.id, [])))
    return result


def ancestor_ids(task: Task, tasks_by_id: Mapping[int, Task]) -> list[int]:
    """親、祖父母…の順に id を返す。

    tasks_by_id にない親で止まる。循環していても止まる。

    Args:
        task: 起点のタスク。
        tasks_by_id: id → タスク。

    Returns:
        祖先のタスク id。近い順。
    """
    result: list[int] = []
    parent_id = task.parent_task_id
    while parent_id is not None and parent_id in tasks_by_id and parent_id not in result:
        result.append(parent_id)
        parent_id = tasks_by_id[parent_id].parent_task_id
    return result


def tree_order(tasks: Iterable[Task]) -> list[tuple[Task, int]]:
    """親のすぐ後に子が来る順に (タスク, 深さ) を返す。

    親が tasks に含まれないタスクは最上位(深さ 0)として扱う。最上位どうしは並び順で並べる。

    Args:
        tasks: 並べるタスク。

    Returns:
        (タスク, 深さ) の一覧。
    """
    tasks = list(tasks)
    ids = {task.id for task in tasks}
    children = children_map(t for t in tasks if t.parent_task_id in ids)
    roots = sorted(
        (t for t in tasks if t.parent_task_id not in ids), key=lambda t: (t.sort_order, t.id or 0)
    )
    result: list[tuple[Task, int]] = []
    pending = [(task, 0) for task in reversed(roots)]
    while pending:
        task, depth = pending.pop()
        result.append((task, depth))
        pending.extend((child, depth + 1) for child in reversed(children.get(task.id, [])))
    return result


def subtask_progress(tasks: Iterable[Task]) -> dict[int, Progress]:
    """サブタスクを持つタスクごとの、末端のサブタスク(孫以下を含む)の完了数を数える。

    途中の階層のタスクは、その下のサブタスクで進み具合を表すので数えない。

    Args:
        tasks: タスク。完了済みも含めて渡す。

    Returns:
        親タスク id → 進み具合。
    """
    tasks = list(tasks)
    children = children_map(tasks)
    result: dict[int, Progress] = {}

    def count(task_id: int) -> tuple[int, int]:
        if task_id in result:
            return result[task_id].done, result[task_id].total
        done = total = 0
        for child in children.get(task_id, []):
            if child.id in children:
                child_done, child_total = count(child.id)
            else:
                child_done, child_total = int(child.status is TaskStatus.DONE), 1
            done += child_done
            total += child_total
        result[task_id] = Progress(done, total)
        return done, total

    for parent_id in children:
        count(parent_id)
    return result


def project_deadline(project: Project, event: Event | None) -> datetime | None:
    """プロジェクトの締め切りを返す。

    Args:
        project: 対象のプロジェクト。
        event: プロジェクトに紐付くイベント。なければ None。

    Returns:
        目標日があればその日の終わり、なければ紐付くイベントの開始。どちらもなければ None。
    """
    if project.target_date is not None:
        return datetime.combine(project.target_date, time(23, 59))
    if event is not None and project.event_id == event.id:
        return event.start_at
    return None


def project_range(
    project: Project,
    task_ranges: Iterable[tuple[datetime, datetime]],
    created: Iterable[datetime],
    *,
    event: Event | None,
) -> tuple[datetime, datetime, bool] | None:
    """ガントの帯の(開始, 終了, 終了が仮か)を返す。

    開始は配下タスクの帯の最初、終了は締め切り(目標日かイベント開始)。
    日付のあるタスクがなければ、タスクを作った最初の日時から始める。
    締め切りがなければ配下タスクの帯の最後までとし、終了は仮とする。

    Args:
        project: 対象のプロジェクト。
        task_ranges: 配下タスクの帯の (開始, 終了)。
        created: 配下タスクの作成日時。
        event: プロジェクトに紐付くイベント。なければ None。

    Returns:
        (開始, 終了, 終了が仮か)。帯を引けなければ None。
    """
    ranges = list(task_ranges)
    created = list(created)
    deadline = project_deadline(project, event)
    if ranges:
        start = min(start for start, _ in ranges)
    elif created:
        start = min(created)
    elif deadline is not None:
        start = deadline - _MIN_BAR
    else:
        return None
    if deadline is not None:
        return min(start, deadline - _MIN_BAR), deadline, False
    end = max((end for _, end in ranges), default=start + _MIN_BAR)
    return start, max(end, start + _MIN_BAR), True

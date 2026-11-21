"""イベントの複製。協力バトルのように毎回同じタスク構成になるイベントを、日付をずらして写す。

Qt にも DB にも依存しない。ここでは複製の中身を組み立てるだけで、保存は AppStore が行う。
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import Event, TaskStatus
from neko_gantcha.domain.project import tree_order

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime, timedelta

    from neko_gantcha.domain.models import Freq, Recurrence, Task


@dataclass(frozen=True, slots=True)
class EventPlan:
    """これから作るイベントの名前と日程。"""

    name: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class TaskCopy:
    """複製やテンプレートから作るタスク1件の中身。

    Attributes:
        source_id: 複製元のタスク id(サブタスクの親を付け替えるのに使う)。
        task: id なしのタスク。event_id と parent_task_id は保存時に付け替える。
        freq: 繰り返しなら頻度。
        active_from: 繰り返しの有効期間の始まり。
        active_until: 繰り返しの有効期間の終わり。
        linked: 新しいイベントに紐付けるか。
    """

    source_id: int  # 複製元のタスク id(サブタスクの親を付け替えるのに使う)
    task: Task  # id なし。event_id(linked なら新しいイベント)と parent_task_id は保存時に付け替える
    freq: Freq | None = None
    active_from: datetime | None = None
    active_until: datetime | None = None
    linked: bool = False  # 新しいイベントに紐付けるか


def shift(value: datetime | None, delta: timedelta) -> datetime | None:
    """日時をずらす。None はそのまま返す。

    Args:
        value: ずらす日時。
        delta: ずらす幅。

    Returns:
        ずらした日時。value が None なら None。
    """
    return None if value is None else value + delta


def plan_duplicate(
    event: Event,
    tasks: Iterable[Task],
    recurrences: Mapping[int, Recurrence],
    *,
    plan: EventPlan,
    now: datetime,
) -> tuple[Event, list[TaskCopy]]:
    """複製後のイベントと、配下タスクの写しを返す。

    タスクの日付(個別に持つ開始・期限、繰り返しの有効期間)はイベントの開始の差だけずらす。
    完了状態と進捗は初期化する。プロジェクトへの所属は前回のものなので引き継がない。
    サブタスク(孫以下も)は親より後に並べる(保存時に親の新しい id へ付け替えるため)。

    Args:
        event: 複製元のイベント。
        tasks: 複製元のイベントの配下タスク(サブタスクを含む)。
        recurrences: タスク id → 繰り返しの設定。
        plan: 新しいイベントの名前と日程。名前が空なら元の名前を使う。
        now: 現在の日時(タスクの作成日時になる)。

    Returns:
        (新しいイベント, 配下タスクの写し)。
    """
    delta = plan.start_at - event.start_at
    new_event = Event(
        category_id=event.category_id,
        name=plan.name.strip() or event.name,
        start_at=plan.start_at,
        end_at=plan.end_at,
        url=event.url,
        note=event.note,
    )
    copies: list[TaskCopy] = []
    for task, _ in tree_order(tasks):
        recurrence = recurrences.get(task.id)
        copied = replace(
            task,
            id=None,
            status=TaskStatus.TODO,
            start_at=shift(task.start_at, delta),
            due_at=shift(task.due_at, delta),
            project_id=None,
            progress_current=0 if task.progress_target is not None else None,
            created_at=now,
            completed_at=None,
        )
        copies.append(
            TaskCopy(
                task.saved_id,
                copied,
                freq=recurrence.freq if recurrence else None,
                active_from=shift(recurrence.active_from, delta) if recurrence else None,
                active_until=shift(recurrence.active_until, delta) if recurrence else None,
                linked=task.event_id == event.id,
            )
        )
    return new_event, copies

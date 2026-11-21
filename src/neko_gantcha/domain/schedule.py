"""タスクの実効開始・期限の解決と、表示用の分類。"""

from enum import StrEnum
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import EventRelation, Freq

if TYPE_CHECKING:
    from datetime import datetime

    from neko_gantcha.domain.models import Event, Recurrence, Task


class TaskKind(StrEnum):
    """ToDo などで表示を分けるための、タスクの種類。"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    EVENT_DURING = "event_during"
    EVENT_BEFORE = "event_before"
    DATED = "dated"  # 開始か期限を持つ単発タスク
    STANDING = "standing"  # 恒常タスク


_FREQ_KINDS = {
    Freq.DAILY: TaskKind.DAILY,
    Freq.WEEKLY: TaskKind.WEEKLY,
    Freq.MONTHLY: TaskKind.MONTHLY,
}


def _linked_event(task: Task, event: Event | None) -> Event | None:
    """タスクに紐付くイベント。event が別物なら無視する。

    Returns:
        紐付くイベント。紐付いていなければ None。
    """
    if event is None or task.event_id is None or task.event_relation is None:
        return None
    if event.id != task.event_id:
        return None
    return event


def effective_range(task: Task, event: Event | None) -> tuple[datetime | None, datetime | None]:
    """タスクの実効的な(開始, 期限)を返す。

    - during: 日付未指定の側はイベントの開始・終了を使う。
    - before: 期限は常にイベントの開始日時。
    イベントの日程はここで毎回参照するので、日程を変えるとタスクも追従する。

    Args:
        task: 対象のタスク。
        event: タスクに紐付くイベント。なければ None。

    Returns:
        (開始, 期限)。どちらも決まらなければ None。
    """
    linked = _linked_event(task, event)
    if linked is None:
        return task.start_at, task.due_at
    if task.event_relation is EventRelation.DURING:
        return task.start_at or linked.start_at, task.due_at or linked.end_at
    return task.start_at, linked.start_at


def task_kind(task: Task, recurrence: Recurrence | None, event: Event | None) -> TaskKind:
    """タスクの種類を判定する。

    Args:
        task: 対象のタスク。
        recurrence: タスクの繰り返しの設定。なければ None。
        event: タスクに紐付くイベント。なければ None。

    Returns:
        繰り返し、イベント、日付の有無から決めた種類。
    """
    if recurrence is not None:
        return _FREQ_KINDS[recurrence.freq]
    if _linked_event(task, event) is not None:
        during = task.event_relation is EventRelation.DURING
        return TaskKind.EVENT_DURING if during else TaskKind.EVENT_BEFORE
    dated = task.start_at is not None or task.due_at is not None
    return TaskKind.DATED if dated else TaskKind.STANDING


def is_recurrence_active(recurrence: Recurrence, now: datetime, event: Event | None) -> bool:
    """繰り返しタスクが now の時点で ToDo に出る対象か。

    イベント限定の繰り返しは、そのイベントの開催中だけ有効。
    紐付くイベントが渡されなかった場合は無効として扱う。

    Args:
        recurrence: 繰り返しの設定。
        now: 現在の日時。
        event: 繰り返しが限定されるイベント。なければ None。

    Returns:
        有効期間の中にあれば True。
    """
    if recurrence.active_from is not None and now < recurrence.active_from:
        return False
    if recurrence.active_until is not None and now >= recurrence.active_until:
        return False
    if recurrence.event_id is None:
        return True
    return (
        event is not None
        and event.id == recurrence.event_id
        and event.start_at <= now < event.end_at
    )

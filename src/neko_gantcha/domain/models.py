"""ドメインモデル。Qt にも DB にも依存しない。

日時はすべて JST のタイムゾーンなし datetime で持つ。
"""

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime, timedelta

MAX_WEEKDAY = 6
MAX_MONTH_DAY = 31


class TaskStatus(StrEnum):
    """タスクの状態。"""

    TODO = "todo"
    DOING = "doing"
    DONE = "done"


class EventRelation(StrEnum):
    """イベントに紐付くタスクを、いつまでに終えるか。"""

    DURING = "during"  # イベント期間中に終える
    BEFORE = "before"  # イベント開始までに終える


class Freq(StrEnum):
    """繰り返しタスクの頻度。"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ProjectStatus(StrEnum):
    """プロジェクトの状態。"""

    ACTIVE = "active"
    DONE = "done"
    ARCHIVED = "archived"


class Theme(StrEnum):
    """配色。SYSTEM は Windows のライト・ダーク設定に合わせる。"""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class WeekStart(StrEnum):
    """カレンダーとガントチャートで、週の最初に置く曜日。"""

    MONDAY = "monday"
    SUNDAY = "sunday"

    @property
    def weekday(self) -> int:
        """曜日の番号を返す。

        Returns:
            datetime.weekday() と同じ番号(0=月曜 … 6=日曜)。
        """
        return MAX_WEEKDAY if self is WeekStart.SUNDAY else 0


class _Persisted:
    """DB に保存するモデルの共通部分。保存前は id が None になる。"""

    __slots__ = ()
    id: int | None

    @property
    def saved_id(self) -> int:
        """保存済みのモデルの ID。

        Returns:
            DB の ID。

        Raises:
            ValueError: まだ保存していない場合。
        """
        if self.id is None:
            msg = f"まだ保存していないモデルです：{self!r}"
            raise ValueError(msg)
        return self.id


@dataclass(frozen=True, slots=True)
class ResetRule:
    """繰り返しタスクの期間を区切るリセット設定。"""

    daily_time: time = time(0, 0)
    weekly_weekday: int = 0  # 0=月曜 … 6=日曜(datetime.weekday() と同じ)
    monthly_day: int = 1  # 1〜31。その日がない月は月末日に丸める

    def __post_init__(self) -> None:
        """値の範囲を確かめる。

        Raises:
            ValueError: 曜日や日付が範囲の外にある場合。
        """
        if not 0 <= self.weekly_weekday <= MAX_WEEKDAY:
            msg = "weekly_weekday は 0〜6 で指定する"
            raise ValueError(msg)
        if not 1 <= self.monthly_day <= MAX_MONTH_DAY:
            msg = "monthly_day は 1〜31 で指定する"
            raise ValueError(msg)


@dataclass(slots=True)
class Category(_Persisted):
    """分類(遊んでいるゲームなど)。リセット時刻を持つ。"""

    name: str
    color: str = "#888888"
    daily_reset_time: time = time(0, 0)
    weekly_reset_weekday: int = 0
    monthly_reset_day: int = 1
    sort_order: int = 0
    archived: bool = False
    id: int | None = None

    @property
    def reset_rule(self) -> ResetRule:
        """この分類のリセット設定。

        Returns:
            日・週・月のリセット設定。
        """
        return ResetRule(self.daily_reset_time, self.weekly_reset_weekday, self.monthly_reset_day)


@dataclass(slots=True)
class Event(_Persisted):
    """期間のあるイベント。"""

    category_id: int
    name: str
    start_at: datetime
    end_at: datetime
    url: str = ""
    note: str = ""
    id: int | None = None


@dataclass(slots=True)
class Project(_Persisted):
    """目標に向けたタスクのまとまり。"""

    category_id: int
    name: str
    goal: str = ""
    target_date: date | None = None
    event_id: int | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    id: int | None = None


@dataclass(slots=True)
class Task(_Persisted):
    """タスク。繰り返しの設定は Recurrence に分けて持つ。"""

    category_id: int
    title: str
    created_at: datetime
    note: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: int = 0  # 大きいほど優先
    start_at: datetime | None = None
    due_at: datetime | None = None
    event_id: int | None = None
    event_relation: EventRelation | None = None
    project_id: int | None = None
    parent_task_id: int | None = None
    progress_current: int | None = None
    progress_target: int | None = None  # None なら単純なチェックボックス
    sort_order: int = 0
    completed_at: datetime | None = None
    id: int | None = None


@dataclass(slots=True)
class Recurrence:
    """繰り返しタスクの設定。タスク1件に1つまで。"""

    task_id: int
    freq: Freq
    active_from: datetime | None = None
    active_until: datetime | None = None
    event_id: int | None = None  # 指定があればそのイベント期間中だけ有効


@dataclass(frozen=True, slots=True)
class Dependency:
    """タスク間の前後関係。predecessor を終えてから successor に取りかかる。"""

    predecessor_id: int
    successor_id: int


@dataclass(slots=True)
class EventTemplate(_Persisted):
    """イベントと配下タスクの組のひな形。日付は持たず、長さといつもの開始時刻だけを持つ。"""

    category_id: int
    name: str
    duration: timedelta
    created_at: datetime
    start_time: time = time(0, 0)
    url: str = ""
    note: str = ""
    id: int | None = None


@dataclass(slots=True)
class TemplateTask(_Persisted):
    """テンプレートの中のタスク1件。日時はイベント開始からのずれで持つ。"""

    category_id: int
    title: str
    note: str = ""
    priority: int = 0
    linked: bool = False  # イベントに紐付けるか(サブタスクは紐付かないことがある)
    event_relation: EventRelation | None = None
    start_offset: timedelta | None = None
    due_offset: timedelta | None = None
    progress_target: int | None = None
    freq: Freq | None = None  # 繰り返しなら頻度
    active_from_offset: timedelta | None = None
    active_until_offset: timedelta | None = None
    sort_order: int = 0
    parent_id: int | None = None  # 同じテンプレートの中の親タスク
    template_id: int | None = None
    id: int | None = None


@dataclass(slots=True)
class Completion:
    """繰り返しタスクの、ある期間の達成記録。"""

    task_id: int
    period_key: str
    completed_at: datetime
    count: int = 1

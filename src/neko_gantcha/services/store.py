"""AppStore: UI から見たデータ操作の唯一の窓口。

変更時にシグナルを出し、各ビューはそれを受けて再描画する。
選択状態と絞り込みもここで持ち、3ビューと詳細パネルで共有する。
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict, Unpack

from PySide6.QtCore import QObject, QTimer, Signal

from neko_gantcha.data import backup, transfer
from neko_gantcha.data.repositories import (
    CategoryRepository,
    CompletionRepository,
    DependencyRepository,
    EventRepository,
    ProjectRepository,
    RecurrenceRepository,
    SettingsRepository,
    TaskRepository,
    TemplateRepository,
)
from neko_gantcha.domain.calendar import (
    CalendarInputs,
    build_day,
    build_month,
    build_week,
    month_weeks,
)
from neko_gantcha.domain.dependency import creates_cycle, internal_links
from neko_gantcha.domain.duplicate import EventPlan, plan_duplicate
from neko_gantcha.domain.gantt import (
    GanttInputs,
    RowKind,
    bar_range,
    build_gantt,
    drag_modes,
    dragged_range,
)
from neko_gantcha.domain.models import (
    Dependency,
    EventRelation,
    Freq,
    ProjectStatus,
    Recurrence,
    Task,
    TaskStatus,
    Theme,
    WeekStart,
)
from neko_gantcha.domain.periods import current_period
from neko_gantcha.domain.project import project_progress, subtask_progress
from neko_gantcha.domain.template import make_template, plan_from_template
from neko_gantcha.domain.todo import TodoContext, build_row, build_sections

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from neko_gantcha.domain.calendar import DayDetail, MonthView, WeekView
    from neko_gantcha.domain.duplicate import TaskCopy
    from neko_gantcha.domain.gantt import DragMode, GanttData, GanttRow
    from neko_gantcha.domain.models import Category, Event, EventTemplate, Project, TemplateTask
    from neko_gantcha.domain.periods import Period
    from neko_gantcha.domain.project import Progress
    from neko_gantcha.domain.todo import SectionData, TodoRow

VIEW_TODO = "todo"
VIEW_CALENDAR = "calendar"
VIEW_GANTT = "gantt"
THEME_KEY = "theme"
WEEK_START_KEY = "week_start"


class ItemKind(StrEnum):
    """選択できる項目の種類。"""

    TASK = "task"
    EVENT = "event"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class ItemRef:
    """選択中の項目を指す参照。"""

    kind: ItemKind
    id: int


@dataclass(frozen=True, slots=True)
class Filter:
    """左ペインの絞り込み。3ビュー共通で効く。"""

    category_id: int | None = None
    project_id: int | None = None
    show_done: bool = False


class _Unset:
    """「引数を指定しなかった」ことを表す印。None(繰り返しなし)と区別する。"""


UNSET = _Unset()


class TemplateNameError(ValueError):
    """同じ名前のテンプレートがすでにある。"""


class TaskFields(TypedDict, total=False):
    """タスクを追加するときに指定できる項目。"""

    note: str
    status: TaskStatus
    priority: int
    start_at: datetime | None
    due_at: datetime | None
    event_id: int | None
    event_relation: EventRelation | None
    project_id: int | None
    parent_task_id: int | None
    progress_current: int | None
    progress_target: int | None
    sort_order: int


type FilterValue = int | bool | None


# UI からの唯一の窓口として、画面が使う操作をすべてここに置く設計なので数が多い
class AppStore(QObject):  # noqa: PLR0904
    """UI から見たデータ操作の窓口。変更のシグナル、選択状態、絞り込みを持つ。

    Args:
        conn: DB の接続。
        clock: 現在時刻を返す関数。テストでは偽の時計を渡す。
        parent: 親の QObject。
    """

    categories_changed = Signal()
    events_changed = Signal()
    projects_changed = Signal()
    tasks_changed = Signal()
    templates_changed = Signal()
    selection_changed = Signal(object)  # ItemRef | None
    filter_changed = Signal()
    minute_ticked = Signal()  # 残り時間の表示更新用(1分間隔)
    periods_changed = Signal()  # 日課・週課・月課のいずれかがリセットをまたいだ
    reveal_requested = Signal(str)  # 「○○で表示」: 指定のビューへ切り替える
    theme_changed = Signal(str)  # Theme の値。設定画面で配色を変えた
    week_start_changed = Signal()  # 設定画面で週の始まりを変えた

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Callable[[], datetime] = datetime.now,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._clock = clock
        self._conn = conn
        self._categories = CategoryRepository(conn)
        self._events = EventRepository(conn)
        self._projects = ProjectRepository(conn)
        self._tasks = TaskRepository(conn)
        self._recurrences = RecurrenceRepository(conn)
        self._completions = CompletionRepository(conn)
        self._templates = TemplateRepository(conn)
        self._dependencies = DependencyRepository(conn)
        self._settings = SettingsRepository(conn)
        self._selected: ItemRef | None = None
        self._filter = Filter()
        self._timer: QTimer | None = None
        self._period_signature = self._current_period_signature()

    # --- アプリの設定 ---

    def theme(self) -> Theme:
        """保存されている配色を返す。

        Returns:
            配色。未設定や不明な値は「Windows に合わせる」。
        """
        try:
            return Theme(self._settings.get(THEME_KEY) or Theme.SYSTEM)
        except ValueError:
            return Theme.SYSTEM

    def set_theme(self, theme: Theme | str) -> None:
        """配色を保存し、変わったら theme_changed を出す。

        Args:
            theme: 新しい配色。コンボボックスから来る素の str も受ける。
        """
        theme = Theme(theme)
        if theme == self.theme():
            return
        self._settings.set(THEME_KEY, theme.value)
        self.theme_changed.emit(theme.value)

    def week_start(self) -> WeekStart:
        """保存されている週の始まりを返す。

        Returns:
            週の始まり。未設定や不明な値は月曜。
        """
        try:
            return WeekStart(self._settings.get(WEEK_START_KEY) or WeekStart.MONDAY)
        except ValueError:
            return WeekStart.MONDAY

    def set_week_start(self, week_start: WeekStart | str) -> None:
        """週の始まりを保存し、変わったら week_start_changed を出す。

        Args:
            week_start: 新しい週の始まり。コンボボックスから来る素の str も受ける。
        """
        week_start = WeekStart(week_start)
        if week_start == self.week_start():
            return
        self._settings.set(WEEK_START_KEY, week_start.value)
        self.week_start_changed.emit()

    # --- 時刻とリセット検知 ---

    def now(self) -> datetime:
        """現在時刻を返す。テストでは clock を差し替える。

        Returns:
            秒までの現在時刻。
        """
        return self._clock().replace(microsecond=0)

    def start_clock(self) -> None:
        """1分ごとに tick() を呼ぶタイマーを開始する。"""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(60_000)
            self._timer.timeout.connect(self.tick)
        self._timer.start()

    def tick(self) -> None:
        """時間の経過を反映する。リセットをまたいでいたら periods_changed を出す。"""
        signature = self._current_period_signature()
        if signature != self._period_signature:
            self._period_signature = signature
            self.periods_changed.emit()
        self.minute_ticked.emit()

    def _current_period_signature(self) -> frozenset[tuple[int, str, str]]:
        """分類ごと・頻度ごとの今の期間のキーを集める。リセットをまたいだかの判定に使う。

        Returns:
            (分類 id, 頻度, 期間のキー) の集合。
        """
        now = self.now()
        return frozenset(
            (category.saved_id, freq.value, current_period(freq, now, category.reset_rule).key)
            for category in self._categories.list_all(include_archived=True)
            for freq in Freq
        )

    # --- 選択 ---

    @property
    def selected(self) -> ItemRef | None:
        """選択中の項目。

        Returns:
            選択中の項目。何も選んでいなければ None。
        """
        return self._selected

    def select(self, ref: ItemRef | None) -> None:
        """項目を選択し、変わったら selection_changed を出す。

        Args:
            ref: 選択する項目。None なら選択を外す。
        """
        if ref == self._selected:
            return
        self._selected = ref
        self.selection_changed.emit(ref)

    # --- 絞り込み ---

    @property
    def filter(self) -> Filter:
        """今の絞り込み。

        Returns:
            左ペインの絞り込み。
        """
        return self._filter

    def set_filter(self, **changes: FilterValue) -> None:
        """絞り込みの一部を変え、変わったら filter_changed を出す。

        分類とプロジェクトの絞り込みは食い違わないようにする。プロジェクトを選んだときは
        分類をそれに合わせ、分類を変えたときは別の分類のプロジェクトの絞り込みを外す。

        Args:
            **changes: 変える項目。例: `category_id=1`、`show_done=True`。
        """
        updated = replace(self._filter, **changes)
        if updated.project_id is not None:
            project = self._projects.get(updated.project_id)
            if project is None:
                updated = replace(updated, project_id=None)
            elif updated.category_id not in {None, project.category_id}:
                if "project_id" in changes:
                    updated = replace(updated, category_id=project.category_id)
                else:
                    updated = replace(updated, project_id=None)
        if updated == self._filter:
            return
        self._filter = updated
        self.filter_changed.emit()

    # --- 参照 ---

    def categories(self, *, include_archived: bool = False) -> list[Category]:
        """分類を並び順に返す。

        Args:
            include_archived: 保管した分類も含めるか。

        Returns:
            分類の一覧。
        """
        return self._categories.list_all(include_archived=include_archived)

    def category(self, category_id: int) -> Category | None:
        """分類を1件返す。

        Args:
            category_id: 分類の id。

        Returns:
            分類。なければ None。
        """
        return self._categories.get(category_id)

    def events_by_id(self) -> dict[int, Event]:
        """すべてのイベントを id で引けるようにして返す。

        Returns:
            イベント id → イベント。
        """
        return {event.saved_id: event for event in self._events.list_all()}

    def projects_by_id(self) -> dict[int, Project]:
        """すべてのプロジェクトを id で引けるようにして返す。

        Returns:
            プロジェクト id → プロジェクト。
        """
        return {project.saved_id: project for project in self._projects.list_all()}

    def project(self, project_id: int) -> Project | None:
        """プロジェクトを1件返す。

        Args:
            project_id: プロジェクトの id。

        Returns:
            プロジェクト。なければ None。
        """
        return self._projects.get(project_id)

    def projects(self) -> list[Project]:
        """左ペインに出すプロジェクトを返す(分類の絞り込みが効く)。

        進行中のものだけ。完了したものは show_done のときだけ含める。保管したものは出さない。

        Returns:
            目標日の近い順のプロジェクト(目標日のないものは最後)。
        """
        shown = {ProjectStatus.ACTIVE}
        if self._filter.show_done:
            shown.add(ProjectStatus.DONE)
        projects = [
            project
            for project in self._projects.list_all(category_id=self._filter.category_id)
            if project.status in shown
        ]
        return sorted(projects, key=lambda p: (p.target_date or date.max, p.name, p.saved_id))

    def project_tasks(self, project_id: int) -> list[Task]:
        """プロジェクトに属するタスクを返す。

        Args:
            project_id: プロジェクトの id。

        Returns:
            完了済みも含むタスク。
        """
        return self._tasks.list_all(project_id=project_id)

    def project_progress(self) -> dict[int, Progress]:
        """プロジェクトごとの完了率を数える。

        Returns:
            プロジェクト id → 進み具合。
        """
        return project_progress(self._tasks.list_all(), self._recurrences.by_task_id().keys())

    def subtasks(self, parent_task_id: int) -> list[Task]:
        """直下のサブタスクを返す。

        Args:
            parent_task_id: 親タスクの id。

        Returns:
            完了済みも含む、並び順のサブタスク。
        """
        return self._tasks.children(parent_task_id)

    def descendant_count(self, task_id: int) -> int:
        """孫以下も含めたサブタスクの件数を数える。

        Args:
            task_id: 親タスクの id。

        Returns:
            サブタスクの件数。
        """
        return len(self._tasks.descendants(task_id))

    def subtask_progress(self) -> dict[int, Progress]:
        """サブタスクを持つタスクごとの、末端のサブタスクの完了数を数える。

        Returns:
            親タスク id → 進み具合。
        """
        return subtask_progress(self._tasks.list_all())

    def recurrences(self) -> dict[int, Recurrence]:
        """すべての繰り返しの設定を返す。

        Returns:
            タスク id → 繰り返しの設定。
        """
        return self._recurrences.by_task_id()

    def recurrence(self, task_id: int) -> Recurrence | None:
        """タスクの繰り返しの設定を返す。

        Args:
            task_id: タスクの id。

        Returns:
            繰り返しの設定。繰り返しでなければ None。
        """
        return self._recurrences.get(task_id)

    def task(self, task_id: int) -> Task | None:
        """タスクを1件返す。

        Args:
            task_id: タスクの id。

        Returns:
            タスク。なければ None。
        """
        return self._tasks.get(task_id)

    def tasks(self) -> list[Task]:
        """現在の絞り込みに合うタスクを返す。

        Returns:
            並び順のタスク。
        """
        return self._tasks.list_all(
            category_id=self._filter.category_id,
            project_id=self._filter.project_id,
            include_done=self._filter.show_done,
        )

    def todo_sections(self) -> list[SectionData]:
        """ToDo ビューに出すセクションと行を組み立てる。

        Returns:
            表示順のセクション。
        """
        return build_sections(self.tasks(), self._todo_context(), show_done=self._filter.show_done)

    def todo_row(self, task_id: int) -> TodoRow | None:
        """タスク1件の表示用の情報(実効日程、現在の期間の達成回数など)を組み立てる。

        Args:
            task_id: タスクの id。

        Returns:
            表示用の行。タスクがなければ None。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return build_row(task, self._todo_context())

    def _todo_context(self) -> TodoContext:
        """行の組み立てに使う、今の時点の情報をまとめる。

        Returns:
            イベント、繰り返し、分類、今の期間の達成回数、現在時刻。
        """
        now = self.now()
        categories = self._categories_by_id()
        return TodoContext(
            self.events_by_id(),
            self._recurrences.by_task_id(),
            categories,
            self._current_counts(categories, now),
            now,
        )

    # --- カレンダー ---

    def calendar_month(self, year: int, month: int) -> MonthView:
        """月表示の内容を組み立てる。分類の絞り込みが効く。イベントは終了済みも含めて出す。

        Args:
            year: 年。
            month: 月。

        Returns:
            月表示の内容。
        """
        first_weekday = self.week_start().weekday
        weeks = month_weeks(year, month, first_weekday=first_weekday)
        days = [day for week in weeks for day in week]
        return build_month(year, month, self._calendar_inputs(days), first_weekday=first_weekday)

    def calendar_week(self, first: date) -> WeekView:
        """週表示の内容を組み立てる。絞り込みは月表示と同じ。

        Args:
            first: その週の最初の日(week_start() の曜日)。

        Returns:
            週表示の内容。
        """
        days = [first + timedelta(days=offset) for offset in range(7)]
        return build_week(first, self._calendar_inputs(days))

    def calendar_day(self, day: date) -> DayDetail:
        """日別一覧の内容を組み立てる。

        Args:
            day: 日付。

        Returns:
            その日のイベント、期限のタスク、日課。
        """
        return build_day(day, self._calendar_inputs([day]))

    def _calendar_inputs(self, days: list[date]) -> CalendarInputs:
        """カレンダーの組み立てに使う情報をまとめる。

        Returns:
            絞り込み後の行とイベント、全イベント、分類、指定した日の日課の達成回数、現在時刻。
        """
        ctx = self._todo_context()
        rows = [build_row(task, ctx) for task in self.tasks()]
        keys = [day.isoformat() for day in days]
        counts = {(c.task_id, c.period_key): c.count for c in self._completions.list_for_keys(keys)}
        return CalendarInputs(
            rows, self._filtered_events(ctx.events), ctx.events, ctx.categories, counts, ctx.now
        )

    # --- ガント ---

    def gantt(self) -> GanttData:
        """ガントチャートに並べる行と時間軸の範囲を組み立てる。絞り込みが効く。

        Returns:
            ガントの内容。
        """
        ctx = self._todo_context()
        rows = [build_row(task, ctx) for task in self.tasks()]
        projects = [
            project
            for project in self._projects.list_all(category_id=self._filter.category_id)
            if self._filter.project_id is None or project.id == self._filter.project_id
        ]
        # プロジェクトの帯は、完了済みで隠れたタスクも含めた期間で引く
        project_ids = {project.id for project in projects}
        project_rows: dict[int, list[TodoRow]] = {}
        for task in self._tasks.list_all():
            if task.project_id is not None and task.project_id in project_ids:
                project_rows.setdefault(task.project_id, []).append(build_row(task, ctx))
        return build_gantt(
            GanttInputs(
                rows,
                self._filtered_events(ctx.events),
                ctx.categories,
                ctx.now,
                projects=projects,
                project_rows=project_rows,
                progress=self.project_progress(),
                all_events=ctx.events,
                show_done=self._filter.show_done,
                dependencies=self._dependencies.list_all(),
            )
        )

    # --- タスク間の前後関係 ---

    def dependencies(self) -> list[Dependency]:
        """すべての前後関係を返す。

        Returns:
            前後関係の一覧。
        """
        return self._dependencies.list_all()

    def predecessors(self, task_id: int) -> list[Task]:
        """先に終えるタスク(前のタスク)を返す。

        Args:
            task_id: 後のタスクの id。

        Returns:
            前のタスク。
        """
        ids = [d.predecessor_id for d in self._dependencies.list_all() if d.successor_id == task_id]
        return [task for task in map(self._tasks.get, ids) if task is not None]

    def successors(self, task_id: int) -> list[Task]:
        """終えてから取りかかるタスク(後のタスク)を返す。

        Args:
            task_id: 前のタスクの id。

        Returns:
            後のタスク。
        """
        ids = [d.successor_id for d in self._dependencies.list_all() if d.predecessor_id == task_id]
        return [task for task in map(self._tasks.get, ids) if task is not None]

    def waiting_on(self, task_id: int) -> list[Task]:
        """まだ終わっていない前のタスクを返す。これがあるうちは取りかかれない。

        Args:
            task_id: 後のタスクの id。

        Returns:
            未完了の前のタスク。
        """
        return [t for t in self.predecessors(task_id) if t.status is not TaskStatus.DONE]

    def dependency_candidates(self, task_id: int, *, as_predecessor: bool) -> list[Task]:
        """前後関係を結べるタスクを返す。

        繰り返しタスク、自分自身、すでに結んでいるもの、結ぶと循環するものは除く。
        完了済みのタスクも候補に含める。

        Args:
            task_id: 関係を結ぶ側のタスクの id。
            as_predecessor: 真なら task_id の前のタスクの候補、偽なら後のタスクの候補。

        Returns:
            候補のタスク。未完了が先、その中は期限の近い順。
        """
        recurring = self._recurrences.by_task_id().keys()
        deps = self._dependencies.list_all()
        result: list[Task] = []
        for task in self._tasks.list_all():
            other_id = task.saved_id
            if other_id == task_id or other_id in recurring:
                continue
            pred, succ = (other_id, task_id) if as_predecessor else (task_id, other_id)
            if Dependency(pred, succ) in deps or creates_cycle(deps, pred, succ):
                continue
            result.append(task)
        return sorted(
            result,
            key=lambda t: (
                t.status is TaskStatus.DONE,
                t.due_at or datetime.max,
                t.title,
                t.saved_id,
            ),
        )

    def add_dependency(self, predecessor_id: int, successor_id: int) -> Dependency:
        """「predecessor を終えてから successor に取りかかる」関係を加える。

        Args:
            predecessor_id: 前のタスクの id。
            successor_id: 後のタスクの id。

        Returns:
            加えた前後関係。

        Raises:
            ValueError: タスクがない、繰り返しタスク、すでに同じ関係がある、または循環する場合。
        """
        for task_id in (predecessor_id, successor_id):
            if self._tasks.get(task_id) is None:
                msg = "タスクが存在しない"
                raise ValueError(msg)
            if self._recurrences.get(task_id) is not None:
                msg = "繰り返しタスクには前後関係を付けられない"
                raise ValueError(msg)
        dependency = Dependency(predecessor_id, successor_id)
        deps = self._dependencies.list_all()
        if dependency in deps:
            msg = "すでに同じ前後関係がある"
            raise ValueError(msg)
        if creates_cycle(deps, predecessor_id, successor_id):
            msg = "前後関係が循環する"
            raise ValueError(msg)
        self._dependencies.add(dependency)
        self.tasks_changed.emit()
        return dependency

    def remove_dependency(self, predecessor_id: int, successor_id: int) -> None:
        """前後関係を外す。

        Args:
            predecessor_id: 前のタスクの id。
            successor_id: 後のタスクの id。
        """
        self._dependencies.delete(Dependency(predecessor_id, successor_id))
        self.tasks_changed.emit()

    def apply_gantt_drag(self, row: GanttRow, mode: DragMode, days: int) -> bool:
        """ガントで帯をドラッグした結果を保存する。日数単位で動かし、時刻は保つ。

        Args:
            row: ドラッグした行。
            mode: ドラッグの種類。
            days: 動かした日数。

        Returns:
            保存したら True。動かせない行やずれが 0 日のときは何もせず False。
        """
        if days == 0 or mode not in drag_modes(row) or row.start is None or row.end is None:
            return False
        bar = (row.start, row.end)
        delta = timedelta(days=days)
        moved = False
        if row.kind is RowKind.EVENT and row.event is not None:
            moved = self._drag_event(row.event.saved_id, bar, mode=mode, delta=delta)
        elif row.kind is RowKind.TASK and row.task is not None:
            moved = self._drag_task(row.task.saved_id, bar, mode=mode, delta=delta)
        elif row.kind is RowKind.PROJECT and row.project is not None:
            # 帯の開始は配下タスクから決まる仮のものなので、目標日は開始に縛られずに動かす
            moved = self._move_project_target(row.project.saved_id, (row.end + delta).date())
        return moved

    def _drag_event(
        self, event_id: int, bar: tuple[datetime, datetime], *, mode: DragMode, delta: timedelta
    ) -> bool:
        """イベントの帯のドラッグを保存する。

        Returns:
            保存したら True。イベントがなければ False。
        """
        event = self._events.get(event_id)
        if event is None:
            return False
        start, end = dragged_range((event.start_at, event.end_at), bar, mode=mode, delta=delta)
        event.start_at = event.start_at if start is None else start
        event.end_at = event.end_at if end is None else end
        self.save_event(event)
        return True

    def _drag_task(
        self, task_id: int, bar: tuple[datetime, datetime], *, mode: DragMode, delta: timedelta
    ) -> bool:
        """タスクの帯のドラッグを保存する。

        Returns:
            保存したら True。タスクがなければ False。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.start_at, task.due_at = dragged_range(
            (task.start_at, task.due_at), bar, mode=mode, delta=delta
        )
        self.update_task(task)
        return True

    def _move_project_target(self, project_id: int, target: date) -> bool:
        """プロジェクトの目標日を変えて保存する。

        Returns:
            保存したら True。プロジェクトがなければ False。
        """
        project = self._projects.get(project_id)
        if project is None:
            return False
        project.target_date = target
        self.save_project(project)
        return True

    def _filtered_events(self, events: Mapping[int, Event]) -> list[Event]:
        """カレンダーとガントに出すイベントを選ぶ。プロジェクトで絞り込んでいるときは出さない。

        Returns:
            分類の絞り込みに合うイベント。
        """
        if self._filter.project_id is not None:
            return []
        category_id = self._filter.category_id
        return [
            event
            for event in events.values()
            if category_id is None or event.category_id == category_id
        ]

    # --- ビュー間の移動 ---

    def shown_in_gantt(self, ref: ItemRef | None) -> bool:
        """項目がガントに帯として出るか。

        Args:
            ref: 調べる項目。

        Returns:
            イベント、保管していないプロジェクト、日付のある単発タスクなら True。
        """
        if ref is None:
            return False
        match ref.kind:
            case ItemKind.EVENT:
                shown = self._events.get(ref.id) is not None
            case ItemKind.PROJECT:
                project = self._projects.get(ref.id)
                shown = project is not None and project.status is not ProjectStatus.ARCHIVED
            case ItemKind.TASK:
                row = self.todo_row(ref.id)
                shown = row is not None and bar_range(row) is not None
        return shown

    def reveal(self, ref: ItemRef, view: str) -> None:
        """項目を選択し、指定したビューに切り替えて表示する。

        Args:
            ref: 表示する項目。
            view: 切り替え先のビュー(VIEW_TODO など)。
        """
        self.select(ref)
        self.reveal_requested.emit(view)

    def _categories_by_id(self) -> dict[int, Category]:
        """保管したものも含めて、分類を id で引けるようにする。

        Returns:
            分類 id → 分類。
        """
        return {c.saved_id: c for c in self._categories.list_all(include_archived=True)}

    def _current_counts(
        self, categories: Mapping[int, Category], now: datetime
    ) -> dict[tuple[int, str], int]:
        """今の期間の達成回数を読む。

        Returns:
            (タスク id, 期間のキー) → 達成回数。
        """
        keys = {
            current_period(freq, now, category.reset_rule).key
            for category in categories.values()
            for freq in Freq
        }
        return {(c.task_id, c.period_key): c.count for c in self._completions.list_for_keys(keys)}

    # --- タスクの操作 ---

    def add_task(
        self,
        title: str,
        category_id: int,
        *,
        freq: Freq | None = None,
        **fields: Unpack[TaskFields],
    ) -> Task:
        """タスクを追加する。

        Args:
            title: タイトル。前後の空白は除く。
            category_id: 分類の id。
            freq: 繰り返しにするなら頻度。
            **fields: そのほかの項目。

        Returns:
            追加したタスク。

        Raises:
            ValueError: タイトルが空の場合や、サブタスクを繰り返しにしようとした場合。
        """
        title = title.strip()
        if not title:
            msg = "タイトルが空のタスクは追加できない"
            raise ValueError(msg)
        task = Task(category_id=category_id, title=title, created_at=self.now(), **fields)
        if freq is not None and task.parent_task_id is not None:
            msg = "サブタスクは繰り返しにできない"
            raise ValueError(msg)
        self._normalize(task, previous=None, recurring=freq is not None)
        self._tasks.add(task)
        if freq is not None:
            self._recurrences.set(Recurrence(task.saved_id, freq, event_id=task.event_id))
        self.tasks_changed.emit()
        return task

    def add_subtask(self, parent_task_id: int, title: str) -> Task:
        """サブタスクを追加する。分類とプロジェクトは親に合わせ、親の子の最後に並べる。

        Args:
            parent_task_id: 親タスクの id。
            title: タイトル。

        Returns:
            追加したサブタスク。

        Raises:
            ValueError: 親タスクがない場合や、親が繰り返しタスクの場合。
        """
        parent = self._tasks.get(parent_task_id)
        if parent is None:
            msg = "親タスクが存在しない"
            raise ValueError(msg)
        if self._recurrences.get(parent_task_id) is not None:
            msg = "繰り返しタスクにはサブタスクを作れない"
            raise ValueError(msg)
        siblings = self._tasks.children(parent_task_id)
        return self.add_task(
            title,
            parent.category_id,
            parent_task_id=parent_task_id,
            project_id=parent.project_id,
            sort_order=max((t.sort_order for t in siblings), default=-1) + 1,
        )

    def update_task(self, task: Task, *, freq: Freq | _Unset | None = UNSET) -> Task:
        """タスクを保存する。完了日時、回数管理、イベントとの関係はここで整える。

        サブタスクを持つタスクの分類とプロジェクトを変えると、孫以下も含めたサブタスクも合わせる。

        Args:
            task: 保存するタスク。
            freq: 渡すと繰り返しの設定も変える(None で繰り返しを解除)。

        Returns:
            保存したタスク。

        Raises:
            ValueError: id がない場合、タイトルが空の場合、またはサブタスクを繰り返しにする場合。
        """
        if task.id is None:
            msg = "id のないタスクは更新できない"
            raise ValueError(msg)
        task.title = task.title.strip()
        if not task.title:
            msg = "タイトルは空にできない"
            raise ValueError(msg)
        recurrence = self._recurrences.get(task.id)
        if isinstance(freq, _Unset):
            freq = recurrence.freq if recurrence else None
        children = self._tasks.children(task.id)
        if freq is not None and (task.parent_task_id is not None or children):
            msg = "サブタスクと、サブタスクを持つタスクは繰り返しにできない"
            raise ValueError(msg)
        self._normalize(task, previous=self._tasks.get(task.id), recurring=freq is not None)
        self._tasks.update(task)
        for child in self._tasks.descendants(task.id) if children else []:
            if (child.category_id, child.project_id) != (task.category_id, task.project_id):
                child.category_id = task.category_id
                child.project_id = task.project_id
                self._tasks.update(child)
        if freq is None:
            if recurrence is not None:
                self._recurrences.delete(task.id)
        else:
            self._recurrences.set(
                Recurrence(
                    task.id,
                    freq,
                    active_from=recurrence.active_from if recurrence else None,
                    active_until=recurrence.active_until if recurrence else None,
                    event_id=task.event_id,
                )
            )
        self.tasks_changed.emit()
        return task

    def set_task_done(self, task_id: int, *, done: bool) -> None:
        """タスクを完了または未完了にする。繰り返しタスクは今の期間の達成回数を変える。

        Args:
            task_id: タスクの id。
            done: 完了にするか。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return
        if self._recurrences.get(task_id) is not None:
            self.set_completion_count(task_id, (task.progress_target or 1) if done else 0)
            return
        if (task.status is TaskStatus.DONE) == done:
            return
        task.status = TaskStatus.DONE if done else TaskStatus.TODO
        if done and task.progress_target is not None:
            task.progress_current = task.progress_target
        self.update_task(task)

    def adjust_progress(self, task_id: int, delta: int) -> None:
        """回数つきタスクの進捗を増減する。目標に届いたら完了になる。

        Args:
            task_id: タスクの id。
            delta: 増やす数。負なら減らす。
        """
        task = self._tasks.get(task_id)
        if task is None or task.progress_target is None:
            return
        if self._recurrences.get(task_id) is not None:
            self.set_completion_count(task_id, self.completion_count(task_id) + delta)
            return
        current = (task.progress_current or 0) + delta
        task.progress_current = max(0, min(task.progress_target, current))
        self.update_task(task)

    def delete_task(self, task_id: int) -> None:
        """タスクを削除する。孫以下も含めたサブタスクも一緒に消える。

        Args:
            task_id: タスクの id。
        """
        removed = {task_id} | {child.id for child in self._tasks.descendants(task_id)}
        self._tasks.delete(task_id)
        selected = self._selected
        if selected is not None and selected.kind is ItemKind.TASK and selected.id in removed:
            self.select(None)
        self.tasks_changed.emit()

    def _normalize(self, task: Task, previous: Task | None, *, recurring: bool) -> None:
        """保存の前に、イベントとの関係、回数管理、完了日時を整える。"""
        # イベントとの関係: 繰り返しタスクは関係を持たず、単発タスクは既定で「イベント中」
        if task.event_id is None or recurring:
            task.event_relation = None
        elif task.event_relation is None:
            task.event_relation = EventRelation.DURING

        if task.progress_target is not None:
            task.progress_target = max(1, task.progress_target)

        if recurring:
            _clear_for_recurring(task)
            return
        _sync_progress(task, previous)
        # 完了日時は status に合わせる
        if task.status is not TaskStatus.DONE:
            task.completed_at = None
        elif task.completed_at is None:
            task.completed_at = self.now()

    # --- 繰り返しタスクの達成 ---

    def task_period(self, task_id: int) -> Period | None:
        """繰り返しタスクの現在の期間を返す。

        Args:
            task_id: タスクの id。

        Returns:
            今の期間。繰り返しでなければ None。
        """
        task = self._tasks.get(task_id)
        recurrence = self._recurrences.get(task_id)
        category = None if task is None else self._categories.get(task.category_id)
        if category is None or recurrence is None:
            return None
        return current_period(recurrence.freq, self.now(), category.reset_rule)

    def completion_count(self, task_id: int) -> int:
        """繰り返しタスクの、現在の期間の達成回数を返す。

        Args:
            task_id: タスクの id。

        Returns:
            達成回数。繰り返しでなければ 0。
        """
        period = self.task_period(task_id)
        if period is None:
            return 0
        completion = self._completions.get(task_id, period.key)
        return completion.count if completion else 0

    def set_completion_count(self, task_id: int, count: int) -> None:
        """繰り返しタスクの、現在の期間の達成回数を設定する。0〜目標に収める。

        Args:
            task_id: タスクの id。
            count: 達成回数。
        """
        task = self._tasks.get(task_id)
        period = self.task_period(task_id)
        if task is None or period is None:
            return
        count = max(0, min(task.progress_target or 1, count))
        if count == self.completion_count(task_id):
            return
        self._completions.set_count(task_id, period.key, count, now=self.now())
        self.tasks_changed.emit()

    # --- イベントの操作 ---

    def get_event(self, event_id: int) -> Event | None:
        # 名前を event にすると QObject.event() を上書きしてしまうので避ける
        """イベントを1件返す。

        Args:
            event_id: イベントの id。

        Returns:
            イベント。なければ None。
        """
        return self._events.get(event_id)

    def events(self) -> list[Event]:
        """現在の絞り込みに合うイベントを返す。終了済みは show_done のときだけ含む。

        Returns:
            開始日時順のイベント。
        """
        now = self.now()
        events = self._events.list_all(category_id=self._filter.category_id)
        if self._filter.show_done:
            return events
        return [event for event in events if event.end_at > now]

    def event_tasks(self, event_id: int) -> list[Task]:
        """イベントに紐付くタスク(イベント中・イベント前・期間限定の繰り返し)を返す。

        Args:
            event_id: イベントの id。

        Returns:
            紐付くタスク。
        """
        return self._tasks.list_all(event_id=event_id)

    def save_event(self, event: Event) -> Event:
        """イベントを追加または更新する。

        Args:
            event: 保存するイベント。

        Returns:
            保存したイベント。

        Raises:
            ValueError: 名前が空の場合や、終了が開始より前の場合。
        """
        event.name = event.name.strip()
        if not event.name:
            msg = "イベント名は空にできない"
            raise ValueError(msg)
        if event.end_at < event.start_at:
            msg = "終了日時は開始日時より後にする"
            raise ValueError(msg)
        if event.id is None:
            self._events.add(event)
        else:
            self._events.update(event)
        self.events_changed.emit()
        self.tasks_changed.emit()  # 配下タスクの実効日程が変わる
        return event

    def delete_event(self, event_id: int, *, delete_tasks: bool = False) -> None:
        """イベントを削除する。

        Args:
            event_id: イベントの id。
            delete_tasks: 配下タスクも消すか。偽なら紐付けを外して残す。
        """
        if delete_tasks:
            for task in self._tasks.list_all(event_id=event_id):
                if self._selected == ItemRef(ItemKind.TASK, task.saved_id):
                    self.select(None)
                self._tasks.delete(task.saved_id)
        self._events.delete(event_id)
        if self._selected == ItemRef(ItemKind.EVENT, event_id):
            self.select(None)
        self.events_changed.emit()
        self.tasks_changed.emit()

    def duplicate_event(
        self, event_id: int, *, name: str, start_at: datetime, end_at: datetime
    ) -> Event:
        """イベントを配下タスクごと複製する。タスクの日付は開始の差だけずらす。

        Args:
            event_id: 複製元のイベントの id。
            name: 新しいイベントの名前。空なら元の名前。
            start_at: 新しいイベントの開始。
            end_at: 新しいイベントの終了。

        Returns:
            作ったイベント。

        Raises:
            ValueError: イベントがない場合や、終了が開始より前の場合。
        """
        event = self._events.get(event_id)
        if event is None:
            msg = "イベントが存在しない"
            raise ValueError(msg)
        if end_at < start_at:
            msg = "終了日時は開始日時より後にする"
            raise ValueError(msg)
        tasks = self._event_task_tree(event_id)
        new_event, copies = plan_duplicate(
            event,
            tasks,
            self._recurrences.by_task_id(),
            plan=EventPlan(name, start_at, end_at),
            now=self.now(),
        )
        links = internal_links(self._dependencies.list_all(), {task.saved_id for task in tasks})
        self._add_event_with_tasks(new_event, copies, links)
        return new_event

    def _event_task_tree(self, event_id: int) -> list[Task]:
        """イベントに紐付くタスクと、そのサブタスク(孫以下も。イベントに紐付いていなくても)を集める。

        Returns:
            タスクの一覧。
        """
        tasks = self._tasks.list_all(event_id=event_id)
        task_ids = {task.saved_id for task in tasks}
        for parent_id in sorted(task_ids):
            for child in self._tasks.descendants(parent_id):
                if child.saved_id not in task_ids:
                    task_ids.add(child.saved_id)
                    tasks.append(child)
        return tasks

    def _add_event_with_tasks(
        self, event: Event, copies: list[TaskCopy], links: Iterable[Dependency] = ()
    ) -> None:
        """イベントと、写したタスクを保存する。copies は親が子より先に来る順。

        linked のタスクは新しいイベントに紐付け、サブタスクの親は新しいタスクの id に付け替える。
        links は写したタスクどうしの前後関係(id は TaskCopy.source_id)で、新しいタスクに張り直す。
        """
        self._events.add(event)
        new_ids: dict[int, int] = {}
        for copy in copies:
            task = copy.task
            if copy.linked:
                task.event_id = event.id
            if task.parent_task_id is not None:
                task.parent_task_id = new_ids.get(task.parent_task_id, task.parent_task_id)
            self._normalize(task, previous=None, recurring=copy.freq is not None)
            self._tasks.add(task)
            new_ids[copy.source_id] = task.saved_id
            if copy.freq is not None:
                self._recurrences.set(
                    Recurrence(
                        task.saved_id,
                        copy.freq,
                        active_from=copy.active_from,
                        active_until=copy.active_until,
                        event_id=task.event_id,
                    )
                )
        for link in links:
            if link.predecessor_id in new_ids and link.successor_id in new_ids:
                self._dependencies.add(
                    Dependency(new_ids[link.predecessor_id], new_ids[link.successor_id])
                )
        self.events_changed.emit()
        self.tasks_changed.emit()

    # --- イベントのテンプレート ---

    def templates(self) -> list[EventTemplate]:
        """テンプレートを名前の順に返す。

        Returns:
            テンプレートの一覧。
        """
        return self._templates.list_all()

    def template(self, template_id: int) -> EventTemplate | None:
        """テンプレートを1件返す。

        Args:
            template_id: テンプレートの id。

        Returns:
            テンプレート。なければ None。
        """
        return self._templates.get(template_id)

    def template_tasks(self, template_id: int) -> list[TemplateTask]:
        """テンプレートの中のタスクを返す。

        Args:
            template_id: テンプレートの id。

        Returns:
            並び順のタスク。
        """
        return self._templates.tasks(template_id)

    def template_task_counts(self) -> dict[int, int]:
        """テンプレートごとのタスクの件数を数える。

        Returns:
            テンプレート id → タスクの件数。
        """
        return self._templates.task_counts()

    def template_named(self, name: str) -> EventTemplate | None:
        """名前でテンプレートを探す。

        Args:
            name: テンプレートの名前。前後の空白は除く。

        Returns:
            テンプレート。なければ None。
        """
        return self._templates.get_by_name(name.strip())

    def save_template_from_event(
        self, event_id: int, name: str, *, replace: bool = False
    ) -> EventTemplate:
        """イベントと配下タスクをテンプレートとして保存する。

        Args:
            event_id: もとにするイベントの id。
            name: テンプレートの名前。空ならイベントの名前。
            replace: 同じ名前のテンプレートがあるとき、中身を置き換えるか。

        Returns:
            保存したテンプレート。

        Raises:
            ValueError: イベントがない場合。
            TemplateNameError: 同じ名前のテンプレートがあり、replace が偽の場合。
        """
        event = self._events.get(event_id)
        if event is None:
            msg = "イベントが存在しない"
            raise ValueError(msg)
        source = self._event_task_tree(event_id)
        template, tasks = make_template(
            event, source, self._recurrences.by_task_id(), name=name, now=self.now()
        )
        links = internal_links(self._dependencies.list_all(), {task.saved_id for task in source})
        existing = self._templates.get_by_name(template.name)
        if existing is not None:
            if not replace:
                msg = f"「{template.name}」というテンプレートはすでにある"
                raise TemplateNameError(msg)
            template.id = existing.id
            template.created_at = existing.created_at
        self._templates.save(template, tasks, links)
        self.templates_changed.emit()
        return template

    def template_dependencies(self, template_id: int) -> list[Dependency]:
        """テンプレートの中のタスクどうしの前後関係を返す。

        Args:
            template_id: テンプレートの id。

        Returns:
            前後関係の一覧。
        """
        return self._templates.dependencies(template_id)

    def rename_template(self, template_id: int, name: str) -> EventTemplate:
        """テンプレートの名前を変える。

        Args:
            template_id: テンプレートの id。
            name: 新しい名前。

        Returns:
            名前を変えたテンプレート。

        Raises:
            ValueError: テンプレートがない場合や、名前が空の場合。
            TemplateNameError: 同じ名前のテンプレートがほかにある場合。
        """
        template = self._templates.get(template_id)
        if template is None:
            msg = "テンプレートが存在しない"
            raise ValueError(msg)
        name = name.strip()
        if not name:
            msg = "テンプレート名は空にできない"
            raise ValueError(msg)
        other = self._templates.get_by_name(name)
        if other is not None and other.id != template_id:
            msg = f"「{name}」というテンプレートはすでにある"
            raise TemplateNameError(msg)
        template.name = name
        self._templates.update(template)
        self.templates_changed.emit()
        return template

    def delete_template(self, template_id: int) -> None:
        """テンプレートを削除する。

        Args:
            template_id: テンプレートの id。
        """
        self._templates.delete(template_id)
        self.templates_changed.emit()

    def create_event_from_template(
        self, template_id: int, *, name: str, start_at: datetime, end_at: datetime
    ) -> Event:
        """テンプレートからイベントと配下タスクを作る。タスクの日付は開始日時からのずれで決まる。

        Args:
            template_id: テンプレートの id。
            name: イベントの名前。空ならテンプレートの名前。
            start_at: イベントの開始。
            end_at: イベントの終了。

        Returns:
            作ったイベント。

        Raises:
            ValueError: テンプレートがない場合や、終了が開始より前の場合。
        """
        template = self._templates.get(template_id)
        if template is None:
            msg = "テンプレートが存在しない"
            raise ValueError(msg)
        if end_at < start_at:
            msg = "終了日時は開始日時より後にする"
            raise ValueError(msg)
        event, copies = plan_from_template(
            template,
            self._templates.tasks(template_id),
            plan=EventPlan(name, start_at, end_at),
            now=self.now(),
        )
        self._add_event_with_tasks(event, copies, self._templates.dependencies(template_id))
        return event

    @staticmethod
    def suggest_duplicate_start(event: Event) -> datetime:
        """複製の開始日時の初期値を返す。

        Args:
            event: 複製元のイベント。

        Returns:
            元のイベントの4週間後。
        """
        return event.start_at + timedelta(weeks=4)

    # --- プロジェクトの操作 ---

    def save_project(self, project: Project) -> Project:
        """プロジェクトを追加または更新する。分類を変えると配下タスクの分類も合わせる。

        Args:
            project: 保存するプロジェクト。

        Returns:
            保存したプロジェクト。

        Raises:
            ValueError: 名前が空の場合。
        """
        project.name = project.name.strip()
        if not project.name:
            msg = "プロジェクト名は空にできない"
            raise ValueError(msg)
        if project.id is None:
            self._projects.add(project)
        else:
            previous = self._projects.get(project.id)
            self._projects.update(project)
            if previous is not None and previous.category_id != project.category_id:
                for task in self._tasks.list_all(project_id=project.id):
                    task.category_id = project.category_id
                    self._tasks.update(task)
        if self._filter.project_id == project.id and self._filter.category_id not in {
            None,
            project.category_id,
        }:
            self.set_filter(category_id=project.category_id, project_id=project.id)
        self.projects_changed.emit()
        self.tasks_changed.emit()  # 完了率や表示名が変わる
        return project

    def delete_project(self, project_id: int) -> None:
        """プロジェクトを削除する。タスクは残し、所属だけを外す。

        Args:
            project_id: プロジェクトの id。
        """
        self._projects.delete(project_id)
        if self._selected == ItemRef(ItemKind.PROJECT, project_id):
            self.select(None)
        if self._filter.project_id == project_id:
            self.set_filter(project_id=None)
        self.projects_changed.emit()
        self.tasks_changed.emit()

    # --- 分類の操作(設定画面) ---

    def category_usage(self, category_id: int) -> int:
        """分類を使っているイベント・プロジェクト・タスクの件数を数える。

        Args:
            category_id: 分類の id。

        Returns:
            使っている件数。
        """
        return self._categories.usage(category_id)

    def save_category(self, category: Category) -> Category:
        """分類を追加または更新する。

        Args:
            category: 保存する分類。

        Returns:
            保存した分類。

        Raises:
            ValueError: 名前が空の場合や、ほかの分類と名前が重なる場合。
        """
        category.name = category.name.strip()
        if not category.name:
            msg = "分類名は空にできない"
            raise ValueError(msg)
        for other in self._categories.list_all(include_archived=True):
            if other.name == category.name and other.id != category.id:
                msg = f"「{category.name}」という分類はすでにある"
                raise ValueError(msg)
        if category.id is None:
            existing = self._categories.list_all(include_archived=True)
            category.sort_order = max((c.sort_order for c in existing), default=-1) + 1
            self._categories.add(category)
        else:
            self._categories.update(category)
            if category.archived and self._filter.category_id == category.id:
                self.set_filter(category_id=None)
        self._after_categories_changed()
        return category

    def delete_category(self, category_id: int) -> None:
        """使われていない分類を削除する。隠したいときは保管する。

        Args:
            category_id: 分類の id。

        Raises:
            ValueError: 分類が使われている場合。
        """
        if self._categories.usage(category_id):
            msg = "使用中の分類は削除できない"
            raise ValueError(msg)
        self._categories.delete(category_id)
        if self._filter.category_id == category_id:
            self.set_filter(category_id=None)
        self._after_categories_changed()

    def move_category(self, category_id: int, offset: int) -> None:
        """分類の並び順を動かす。

        Args:
            category_id: 分類の id。
            offset: 動かす数。-1 で上へ、+1 で下へ。
        """
        categories = self._categories.list_all(include_archived=True)
        index = next((i for i, c in enumerate(categories) if c.id == category_id), None)
        if index is None:
            return
        target = index + offset
        if not 0 <= target < len(categories):
            return
        categories.insert(target, categories.pop(index))
        for order, category in enumerate(categories):
            if category.sort_order != order:
                category.sort_order = order
                self._categories.update(category)
        self._after_categories_changed()

    def _after_categories_changed(self) -> None:
        """分類が変わったことを各ビューに知らせる。"""
        # リセット時刻が変わると、繰り返しタスクの今の期間も変わる
        self._period_signature = self._current_period_signature()
        self.categories_changed.emit()
        self.tasks_changed.emit()
        self.periods_changed.emit()

    # --- バックアップとエクスポート ---

    def backup_now(self, folder: Path, *, label: str = "manual") -> Path:
        """今の DB をバックアップする。

        Args:
            folder: バックアップのフォルダ。
            label: ファイル名に付ける印。

        Returns:
            作ったファイルのパス。
        """
        return backup.create_backup(self._conn, folder, self.now(), label=label)

    def auto_backup(self, folder: Path) -> Path | None:
        """その日の自動バックアップがまだなければとる。

        Args:
            folder: バックアップのフォルダ。

        Returns:
            とったファイルのパス。その日の分がすでにあれば None。
        """
        return backup.auto_backup(self._conn, folder, self.now())

    def export_json(self, path: Path) -> None:
        """全データを JSON に書き出す。

        Args:
            path: 書き出すファイルのパス。
        """
        transfer.write_export(self._conn, path, self.now())

    def import_json(self, path: Path, backup_folder: Path) -> dict[str, int]:
        """JSON の内容で今のデータを置き換える。先に今のデータをバックアップする。

        形式が合わなければ transfer.TransferError がそのまま伝わり、データは変えない。

        Args:
            path: 読み込むファイルのパス。
            backup_folder: 置き換える前のバックアップを置くフォルダ。

        Returns:
            テーブル名 → 取り込んだ行数。
        """
        data = transfer.read_export(path)
        self.backup_now(backup_folder, label="before-import")
        counts = transfer.import_data(self._conn, data)
        self.reload()
        return counts

    def reload(self) -> None:
        """DB の内容が丸ごと変わったあとに、選択と絞り込みを戻して全ビューを作り直させる。"""
        self.select(None)
        self._filter = Filter()
        self._period_signature = self._current_period_signature()
        self.filter_changed.emit()
        self.categories_changed.emit()
        self.events_changed.emit()
        self.projects_changed.emit()
        self.tasks_changed.emit()
        self.templates_changed.emit()
        self.periods_changed.emit()


def _clear_for_recurring(task: Task) -> None:
    """繰り返しタスクの日付と完了状態を消す。

    達成は completions で期間ごとに持つので、タスク自体は日付も完了状態も持たない。
    """
    task.start_at = None
    task.due_at = None
    task.status = TaskStatus.TODO
    task.completed_at = None
    task.progress_current = None


def _sync_progress(task: Task, previous: Task | None) -> None:
    """回数管理の現在値を目標に収め、進捗が変わったときは完了・未完了を合わせる。

    目標がなければ現在値も持たない。現在値は 0〜目標に収める。
    """
    if task.progress_target is None:
        task.progress_current = None
        return
    task.progress_current = max(0, min(task.progress_target, task.progress_current or 0))
    old_current = previous.progress_current if previous is not None else None
    old_target = previous.progress_target if previous is not None else None
    if (task.progress_current, task.progress_target) == (old_current, old_target):
        return
    if task.progress_current >= task.progress_target:
        task.status = TaskStatus.DONE
    elif task.status is TaskStatus.DONE:
        task.status = TaskStatus.TODO

"""ガントチャートに並べる行と、帯の期間の計算。Qt には依存しない。

行の並びは 分類 > イベント・プロジェクト > タスク。プロジェクトに属するタスクは
プロジェクトの下に、それ以外でイベントに属さない日付つきタスクは分類ごとの「イベントなし」にまとめる。
日付を持たない恒常タスクと繰り返しタスクは出さない。サブタスクは親の行のすぐ下に字下げして並べる。
孫以下も同じで、親が帯を持たないときは帯を持ついちばん近い先祖の下に並べる。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import EventRelation, ProjectStatus
from neko_gantcha.domain.project import ancestor_ids, project_range

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from neko_gantcha.domain.models import Category, Dependency, Event, Project, Task
    from neko_gantcha.domain.project import Progress
    from neko_gantcha.domain.todo import TodoRow

_MIN_BAR = timedelta(days=1)  # 開始か期限の片方しかないときの帯の長さ
_PAD_BEFORE = timedelta(days=3)
_PAD_AFTER = timedelta(days=7)
_MIN_SPAN_DAYS = 42

# 帯の開始、終了、終了が仮か
type Span = tuple[datetime, datetime, bool]


class RowKind(StrEnum):
    """ガントの行の種類。"""

    CATEGORY = "category"
    EVENT = "event"
    PROJECT = "project"
    GROUP = "group"  # イベントに属さないタスクの見出し
    TASK = "task"


@dataclass(frozen=True, slots=True)
class GanttRow:
    """ガントの行1つ。

    Attributes:
        kind: 行の種類。
        label: 見出しの文字列。
        depth: ツリーの字下げ(0=分類)。
        category_id: 行が属する分類の id。
        start: 帯の開始。None なら帯なし。
        end: 帯の終了。
        event: イベントの行ならそのイベント。
        task: タスクの行ならそのタスク。
        project: プロジェクトの行ならそのプロジェクト。
        relation: タスクとイベントの関係。
        done: 完了したタスク、終了したイベント、または完了したプロジェクトか。
        progress: 回数つきタスクの進捗、またはプロジェクトの完了率(0〜1)。
        open_ended: 期限がなく、帯の終わりが仮のものか。
        has_start: 帯の始まりが実際の開始か(期限だけのタスクは作成日時からの仮の帯)。
    """

    kind: RowKind
    label: str
    depth: int
    category_id: int
    start: datetime | None = None
    end: datetime | None = None
    event: Event | None = None
    task: Task | None = None
    project: Project | None = None
    relation: EventRelation | None = None
    done: bool = False
    progress: float | None = None
    open_ended: bool = False
    has_start: bool = True


@dataclass(frozen=True, slots=True)
class GanttLink:
    """前後関係の矢印1本。先のタスクの帯の終わりから、後のタスクの帯の始まりへ引く。

    Attributes:
        from_row: 先のタスクの行番号。
        to_row: 後のタスクの行番号。
        dependency: もとの前後関係。
        conflict: 後のタスクが、先のタスクの終わりより前に始まる(か、終わる)予定か。
        done: 先のタスクが完了しているか。
    """

    from_row: int
    to_row: int
    dependency: Dependency
    conflict: bool
    done: bool


@dataclass(frozen=True, slots=True)
class GanttData:
    """ガントに描く内容。

    Attributes:
        rows: 上から順の行。
        start: 時間軸の左端。
        end: 時間軸の右端(この日を含む)。
        links: 前後関係の矢印。
    """

    rows: list[GanttRow]
    start: date
    end: date
    links: list[GanttLink] = field(default_factory=list)

    @property
    def days(self) -> int:
        """時間軸の日数。

        Returns:
            左端から右端までの日数(両端を含む)。
        """
        return (self.end - self.start).days + 1


@dataclass(frozen=True, slots=True)
class GanttInputs:
    """ガントの組み立てに使う情報。

    Attributes:
        rows: 絞り込み後のタスクの行。
        events: 絞り込み後のイベント。終了したものは、タスクが残っているか show_done のときだけ
            出す。
        categories: 分類 id → 分類。
        now: 現在の日時。
        projects: 絞り込み後のプロジェクト。
        project_rows: プロジェクト id → 配下の全タスクの行(完了済みを含む)。帯の期間を決めるのに
            使う。
        progress: プロジェクト id → 完了率。
        all_events: 全イベント(プロジェクトの締め切りを決めるのに使う)。
        show_done: 終了したイベントと完了したプロジェクトも出すか。
        dependencies: 前後関係。両方のタスクが並んでいるものを矢印にする。
    """

    rows: list[TodoRow]
    events: list[Event]
    categories: Mapping[int, Category]
    now: datetime
    projects: list[Project] = field(default_factory=list)
    project_rows: Mapping[int, list[TodoRow]] = field(default_factory=dict)
    progress: Mapping[int, Progress] = field(default_factory=dict)
    all_events: Mapping[int, Event] = field(default_factory=dict)
    show_done: bool = False
    dependencies: list[Dependency] = field(default_factory=list)


def bar_range(row: TodoRow) -> Span | None:
    """タスクの帯の(開始, 終了, 終了が仮か)を返す。

    期限だけを持つタスクは、作成した日時から期限までを帯にする。
    開始だけを持つタスクは、開始から1日ぶんの帯にする。

    Args:
        row: 対象の行。

    Returns:
        帯の期間。ガントに出さないタスクは None。
    """
    start, due = row.start, row.due
    if row.is_recurring or (start is None and due is None):
        return None
    if due is None:
        return None if start is None else (start, start + _MIN_BAR, True)
    if start is None:
        start = min(row.task.created_at, due - _MIN_BAR)
    if start > due:
        start = due - _MIN_BAR
    return start, due, False


def _task_row(row: TodoRow, span: Span, *, depth: int, category_id: int) -> GanttRow:
    """タスクの行を作る。

    Returns:
        ガントの行。
    """
    start, end, open_ended = span
    target = row.target
    progress = None if target is None or target == 0 else min(1.0, (row.current or 0) / target)
    return GanttRow(
        RowKind.TASK,
        row.task.title,
        depth,
        category_id,
        start=start,
        end=end,
        task=row.task,
        relation=row.task.event_relation if row.event is not None else None,
        done=row.done,
        progress=progress,
        open_ended=open_ended,
        has_start=row.start is not None,
    )


def build_gantt(inputs: GanttInputs) -> GanttData:
    """ガントに並べる行と時間軸の範囲を返す。

    Args:
        inputs: ガントの組み立てに使う情報。

    Returns:
        行、時間軸の範囲、前後関係の矢印。
    """
    rows = _GanttBuilder(inputs).build()
    start, end = _time_range(rows, inputs.now)
    return GanttData(rows, start, end, gantt_links(rows, inputs.dependencies))


def _project_shown(project: Project, *, show_done: bool) -> bool:
    """プロジェクトをガントに出すか。

    Returns:
        保管したものは出さず、完了したものは show_done のときだけ出す。
    """
    if project.status is ProjectStatus.ARCHIVED:
        return False
    return show_done or project.status is not ProjectStatus.DONE


class _GanttBuilder:
    """ガントの行を上から順に並べる。build_gantt の中だけで使う。

    Args:
        inputs: ガントの組み立てに使う情報。
    """

    def __init__(self, inputs: GanttInputs) -> None:
        self._in = inputs
        self._result: list[GanttRow] = []
        self._projects = [
            p for p in inputs.projects if _project_shown(p, show_done=inputs.show_done)
        ]
        self._spans: dict[int, Span] = {}
        for row in inputs.rows:
            span = bar_range(row)
            if span is not None:
                self._spans[row.task.saved_id] = span
        self._children: dict[int, list[TodoRow]] = {}
        self._by_event: dict[int, list[TodoRow]] = {}
        self._by_project: dict[int, list[TodoRow]] = {}
        self._loose: dict[int, list[TodoRow]] = {}
        self._group_rows()

    def _group_rows(self) -> None:
        """帯を持つタスクを、親・プロジェクト・イベント・分類のどの下に並べるか振り分ける。"""
        event_ids = {event.id for event in self._in.events}
        project_ids = {project.id for project in self._projects}
        tasks_by_id = {row.task.saved_id: row.task for row in self._in.rows}
        for row in self._in.rows:
            task = row.task
            if task.saved_id not in self._spans:
                continue
            # 帯を持ついちばん近い先祖の下に並べる。日付のない中間のタスクは飛ばす
            ancestors = ancestor_ids(task, tasks_by_id)
            holder = next((i for i in ancestors if i in self._spans), None)
            event_id = _linked_event_id(row)
            if holder is not None:
                self._children.setdefault(holder, []).append(row)
            elif task.project_id is not None and task.project_id in project_ids:
                self._by_project.setdefault(task.project_id, []).append(row)
            elif event_id is not None and event_id in event_ids:
                self._by_event.setdefault(event_id, []).append(row)
            else:
                self._loose.setdefault(task.category_id, []).append(row)

    def build(self) -> list[GanttRow]:
        """行を並べる。

        Returns:
            上から順の行。
        """
        categories = sorted(self._in.categories.values(), key=lambda c: (c.sort_order, c.id or 0))
        for category in categories:
            self._add_category(category)
        return self._result

    def _add_category(self, category: Category) -> None:
        """分類の見出しと、その下のイベント・プロジェクト・タスクを並べる。"""
        now, category_id = self._in.now, category.saved_id
        events = [
            event
            for event in self._in.events
            if event.category_id == category_id
            and (event.end_at > now or self._in.show_done or event.id in self._by_event)
        ]
        projects = [p for p in self._projects if p.category_id == category_id]
        loose = self._loose.get(category_id, [])
        if not events and not projects and not loose:
            return
        self._result.append(GanttRow(RowKind.CATEGORY, category.name, 0, category_id))
        for event in sorted(events, key=lambda e: (e.start_at, e.id or 0)):
            self._result.append(
                GanttRow(
                    RowKind.EVENT,
                    event.name,
                    1,
                    category_id,
                    start=event.start_at,
                    end=event.end_at,
                    event=event,
                    done=event.end_at <= now,
                )
            )
            self._add_tasks(
                self._by_event.get(event.saved_id, []), depth=2, category_id=category_id
            )
        self._add_projects(projects, category_id)
        if loose:
            self._result.append(GanttRow(RowKind.GROUP, "イベントなし", 1, category_id))
            self._add_tasks(loose, depth=2, category_id=category_id)

    def _add_projects(self, projects: list[Project], category_id: int) -> None:
        """プロジェクトの行と、その下のタスクを締め切りの順に並べる。"""
        entries = [(project, self._project_span(project)) for project in projects]
        for project, span in sorted(entries, key=_project_order):
            start, end, open_ended = (None, None, False) if span is None else span
            stats = self._in.progress.get(project.saved_id)
            self._result.append(
                GanttRow(
                    RowKind.PROJECT,
                    project.name,
                    1,
                    category_id,
                    start=start,
                    end=end,
                    project=project,
                    done=project.status is ProjectStatus.DONE,
                    progress=None if stats is None else stats.ratio,
                    open_ended=open_ended,
                )
            )
            members = self._by_project.get(project.saved_id, [])
            self._add_tasks(members, depth=2, category_id=category_id)

    def _project_span(self, project: Project) -> Span | None:
        """プロジェクトの帯の期間を、配下の全タスクから決める。

        Returns:
            帯の期間。帯を引けなければ None。
        """
        members = self._in.project_rows.get(project.saved_id, [])
        spans = [span for span in (bar_range(m) for m in members) if span is not None]
        event_id = project.event_id
        event = None if event_id is None else self._in.all_events.get(event_id)
        return project_range(
            project,
            [(start, end) for start, end, _ in spans],
            [m.task.created_at for m in members],
            event=event,
        )

    def _add_tasks(self, rows: list[TodoRow], *, depth: int, category_id: int) -> None:
        """タスクの行と、その下のサブタスクの行を並べる。"""
        for row in sorted(rows, key=self._task_order):
            task_id = row.task.saved_id
            span = self._spans[task_id]
            self._result.append(_task_row(row, span, depth=depth, category_id=category_id))
            children = self._children.get(task_id, [])
            self._add_tasks(children, depth=depth + 1, category_id=category_id)

    def _task_order(self, row: TodoRow) -> tuple[bool, datetime, datetime, int]:
        """タスクの並び順。「イベントまで」の準備を先に、その後「イベント中」を開始順に。

        Returns:
            並べ替えのキー。
        """
        start, end, _ = self._spans[row.task.saved_id]
        before = row.task.event_relation is EventRelation.BEFORE
        return (not before, start, end, row.task.saved_id)


def gantt_links(rows: list[GanttRow], dependencies: Iterable[Dependency]) -> list[GanttLink]:
    """両方のタスクが帯つきで並んでいる前後関係だけを矢印にする。

    Args:
        rows: ガントの行。
        dependencies: 前後関係。

    Returns:
        矢印。
    """
    task_rows = {
        row.task.id: index
        for index, row in enumerate(rows)
        if row.kind is RowKind.TASK and row.task is not None and row.start is not None
    }
    links: list[GanttLink] = []
    for dep in dependencies:
        source = task_rows.get(dep.predecessor_id)
        target = task_rows.get(dep.successor_id)
        if source is None or target is None:
            continue
        before, after = rows[source], rows[target]
        links.append(
            GanttLink(source, target, dep, conflict=_conflicts(before, after), done=before.done)
        )
    return links


def _conflicts(before: GanttRow, after: GanttRow) -> bool:
    """後のタスクが前のタスクの終わりより前に始まる(か、終わる)予定になっているか。

    期限だけのタスクの帯の始まり(作成日時)は仮のものなので、開始としては比べない。
    期限のない前のタスクは終わりが決まっていないので、食い違いとはしない。

    Returns:
        食い違っていれば True。
    """
    if before.open_ended or before.end is None:
        return False
    if after.has_start and after.start is not None and after.start < before.end:
        return True
    return not after.open_ended and after.end is not None and after.end < before.end


def _linked_event_id(row: TodoRow) -> int | None:
    """行のタスクが紐付くイベントの id。

    Returns:
        イベントの id。紐付いていなければ None。
    """
    if row.event is None or row.task.event_relation is None:
        return None
    return row.event.id


def _project_order(entry: tuple[Project, Span | None]) -> tuple[datetime, str, int]:
    """プロジェクトの並び順。締め切りの早い順。

    Returns:
        並べ替えのキー。
    """
    project, span = entry
    end = datetime.max if span is None else span[1]
    return (end, project.name, project.id or 0)


def _time_range(rows: list[GanttRow], now: datetime) -> tuple[date, date]:
    """帯すべてと今日が収まる範囲に、前後の余白を足す。

    Returns:
        (左端の日, 右端の日)。
    """
    starts = [row.start for row in rows if row.start is not None] + [now]
    ends = [row.end for row in rows if row.end is not None] + [now]
    start = (min(starts) - _PAD_BEFORE).date()
    end = (max(ends) + _PAD_AFTER).date()
    if (end - start).days + 1 < _MIN_SPAN_DAYS:
        end = start + timedelta(days=_MIN_SPAN_DAYS - 1)
    return start, end


def row_index(
    data: GanttData,
    *,
    task_id: int | None = None,
    event_id: int | None = None,
    project_id: int | None = None,
) -> int:
    """タスク、イベント、プロジェクトの行番号を返す。

    Args:
        data: ガントの内容。
        task_id: 探すタスクの id。
        event_id: 探すイベントの id。
        project_id: 探すプロジェクトの id。

    Returns:
        行番号。見つからなければ -1。
    """
    for index, row in enumerate(data.rows):
        if (
            project_id is not None
            and row.kind is RowKind.PROJECT
            and row.project is not None
            and row.project.id == project_id
        ):
            return index
        if task_id is not None and row.task is not None and row.task.id == task_id:
            return index
        if (
            event_id is not None
            and row.kind is RowKind.EVENT
            and row.event is not None
            and row.event.id == event_id
        ):
            return index
    return -1


# --- 帯のドラッグによる日程変更 ---


class DragMode(StrEnum):
    """帯のドラッグの種類。"""

    MOVE = "move"  # 帯ごと動かす
    START = "start"  # 左端(開始)を動かす
    END = "end"  # 右端(終了・期限)を動かす


def drag_modes(row: GanttRow) -> frozenset[DragMode]:
    """行の帯に許すドラッグの種類を返す。

    イベントと、イベントに紐付かないタスクは移動・伸縮できる。
    イベントに紐付くタスクは日程がイベントに従うので動かせない。
    プロジェクトは右端(目標日)だけを動かせる。

    Args:
        row: 対象の行。

    Returns:
        許すドラッグの種類。
    """
    if row.start is None or row.end is None:
        return frozenset()
    if row.kind is RowKind.EVENT or (row.kind is RowKind.TASK and row.relation is None):
        return frozenset(DragMode)
    if row.kind is RowKind.PROJECT:
        return frozenset({DragMode.END})
    return frozenset()


def dragged_range(
    own: tuple[datetime | None, datetime | None],
    bar: tuple[datetime, datetime],
    *,
    mode: DragMode,
    delta: timedelta,
) -> tuple[datetime | None, datetime | None]:
    """ドラッグ後の(開始, 終了)を返す。

    移動では、持っている値だけをずらす(期限だけのタスクは期限だけが動く)。
    伸縮では、動かした側の値を帯の端から決める(持っていなければ新たに持たせる)。
    開始が終了を追い越さないように止める。

    Args:
        own: 項目が自分で持つ (開始, 終了)。持たない側は None。
        bar: 描いている帯の (開始, 終了)。
        mode: ドラッグの種類。
        delta: 動かした幅。

    Returns:
        ドラッグ後の (開始, 終了)。
    """
    start, end = own
    bar_start, bar_end = bar
    if mode is DragMode.MOVE:
        return (
            None if start is None else start + delta,
            None if end is None else end + delta,
        )
    if mode is DragMode.START:
        return min(bar_start + delta, bar_end), end
    return start, max(bar_end + delta, bar_start)

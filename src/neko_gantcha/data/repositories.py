"""各テーブルの CRUD。ドメインモデルと行の相互変換もここで行う。

書き込みは操作ごとにコミットする。日時は ISO 8601 文字列(秒まで)で保存する。
テーブル名と列名は、クラスに書いた固定の名前とモデルの項目から作る。値は必ずプレースホルダで渡す。
"""

from datetime import date, datetime, time, timedelta
from itertools import starmap
from typing import TYPE_CHECKING, Protocol, override

from neko_gantcha.domain.models import (
    Category,
    Completion,
    Dependency,
    Event,
    EventRelation,
    EventTemplate,
    Freq,
    Project,
    ProjectStatus,
    Recurrence,
    Task,
    TaskStatus,
    TemplateTask,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable

# DB に書く値
type SqlValue = str | int | float | None
type SqlRow = dict[str, SqlValue]


class _HasId(Protocol):
    """id を持つモデル。"""

    id: int | None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _dt(text: str | None) -> datetime | None:
    if text is None or not text:
        return None
    return datetime.fromisoformat(text)


def _inserted_id(cursor: sqlite3.Cursor) -> int:
    """INSERT で採番された id。

    Returns:
        採番された id。

    Raises:
        RuntimeError: id が採番されなかった場合。
    """
    if cursor.lastrowid is None:
        msg = "追加した行の id を取得できません"
        raise RuntimeError(msg)
    return cursor.lastrowid


def _insert(conn: sqlite3.Connection, table: str, row: SqlRow) -> int:
    """行を1件追加する。トランザクションは呼び出し側で張る。

    Returns:
        採番された id。
    """
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    sql = f"INSERT INTO {table} ({columns}) VALUES ({marks})"  # noqa: S608
    return _inserted_id(conn.execute(sql, tuple(row.values())))


def _update(conn: sqlite3.Connection, table: str, row: SqlRow, *, item_id: int) -> None:
    """指定した id の行を書き換える。トランザクションは呼び出し側で張る。"""
    assignments = ", ".join(f"{column} = ?" for column in row)
    sql = f"UPDATE {table} SET {assignments} WHERE id = ?"  # noqa: S608
    conn.execute(sql, (*row.values(), item_id))


class _Repository[T: _HasId]:
    """id を主キーに持つテーブルの共通処理。

    Args:
        conn: DB の接続。
    """

    table: str
    order_by: str = "id"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _to_row(self, obj: T) -> SqlRow:
        """モデルを書き込む行にする。

        Raises:
            NotImplementedError: 派生クラスで定義していない場合。
        """
        raise NotImplementedError

    def _from_row(self, row: sqlite3.Row) -> T:
        """読んだ行をモデルにする。

        Raises:
            NotImplementedError: 派生クラスで定義していない場合。
        """
        raise NotImplementedError

    def _select(self, where: str = "", params: Iterable[SqlValue] = ()) -> list[T]:
        """条件に合う行を並び順どおりに読む。

        Returns:
            読んだモデル。
        """
        sql = f"SELECT * FROM {self.table}"  # noqa: S608
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {self.order_by}"
        return [self._from_row(row) for row in self._conn.execute(sql, tuple(params))]

    def get(self, item_id: int) -> T | None:
        """指定した id の行を1件読む。

        Args:
            item_id: 読む行の id。

        Returns:
            読んだモデル。なければ None。
        """
        found = self._select("id = ?", (item_id,))
        return found[0] if found else None

    def add(self, obj: T) -> T:
        """追加し、採番した id を obj に設定する。

        Args:
            obj: 追加するモデル。

        Returns:
            id を設定した obj。
        """
        row = self._to_row(obj)
        with self._conn:
            obj.id = _insert(self._conn, self.table, row)
        return obj

    def update(self, obj: T) -> None:
        """保存済みのモデルを書き換える。

        Args:
            obj: 書き換えるモデル。

        Raises:
            ValueError: id のないモデルを渡した場合。
        """
        if obj.id is None:
            msg = "id のないオブジェクトは更新できない"
            raise ValueError(msg)
        row = self._to_row(obj)
        with self._conn:
            _update(self._conn, self.table, row, item_id=obj.id)

    def delete(self, item_id: int) -> None:
        """指定した id の行を消す。

        Args:
            item_id: 消す行の id。
        """
        with self._conn:
            self._conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (item_id,))  # noqa: S608


class CategoryRepository(_Repository[Category]):
    """分類のテーブル。"""

    table = "categories"
    order_by = "sort_order, id"

    @override
    def _to_row(self, obj: Category) -> SqlRow:
        return {
            "name": obj.name,
            "color": obj.color,
            "daily_reset_time": obj.daily_reset_time.strftime("%H:%M"),
            "weekly_reset_weekday": obj.weekly_reset_weekday,
            "monthly_reset_day": obj.monthly_reset_day,
            "sort_order": obj.sort_order,
            "archived": int(obj.archived),
        }

    @override
    def _from_row(self, row: sqlite3.Row) -> Category:
        return Category(
            id=row["id"],
            name=row["name"],
            color=row["color"],
            daily_reset_time=time.fromisoformat(row["daily_reset_time"]),
            weekly_reset_weekday=row["weekly_reset_weekday"],
            monthly_reset_day=row["monthly_reset_day"],
            sort_order=row["sort_order"],
            archived=bool(row["archived"]),
        )

    def list_all(self, *, include_archived: bool = False) -> list[Category]:
        """分類を並び順に読む。

        Args:
            include_archived: 保管した分類も含めるか。

        Returns:
            分類の一覧。
        """
        return self._select("" if include_archived else "archived = 0")

    def usage(self, category_id: int) -> int:
        """分類を使っているイベント・プロジェクト・タスク・テンプレートの件数を数える。

        Args:
            category_id: 分類の id。

        Returns:
            使っている行の合計。
        """
        tables = ("events", "projects", "tasks", "event_templates", "template_tasks")
        return sum(
            int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE category_id = ?",  # noqa: S608
                    (category_id,),
                ).fetchone()[0]
            )
            for table in tables
        )


class EventRepository(_Repository[Event]):
    """イベントのテーブル。"""

    table = "events"
    order_by = "start_at, id"

    @override
    def _to_row(self, obj: Event) -> SqlRow:
        return {
            "category_id": obj.category_id,
            "name": obj.name,
            "start_at": _iso(obj.start_at),
            "end_at": _iso(obj.end_at),
            "url": obj.url,
            "note": obj.note,
        }

    @override
    def _from_row(self, row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            category_id=row["category_id"],
            name=row["name"],
            start_at=datetime.fromisoformat(row["start_at"]),
            end_at=datetime.fromisoformat(row["end_at"]),
            url=row["url"],
            note=row["note"],
        )

    def list_all(self, *, category_id: int | None = None) -> list[Event]:
        """イベントを開始の順に読む。

        Args:
            category_id: 指定すればその分類のものだけ。

        Returns:
            イベントの一覧。
        """
        if category_id is None:
            return self._select()
        return self._select("category_id = ?", (category_id,))

    def list_overlapping(self, start: datetime, end: datetime) -> list[Event]:
        """期間 [start, end) と重なるイベントを読む。

        Args:
            start: 期間の始まり。
            end: 期間の終わり(含まない)。

        Returns:
            重なるイベント。
        """
        return self._select("start_at < ? AND end_at > ?", (_iso(end), _iso(start)))

    def list_active(self, now: datetime) -> list[Event]:
        """開催中のイベントを読む。

        Args:
            now: 現在の日時。

        Returns:
            now の時点で開催中のイベント。
        """
        return self._select("start_at <= ? AND end_at > ?", (_iso(now), _iso(now)))


class ProjectRepository(_Repository[Project]):
    """プロジェクトのテーブル。"""

    table = "projects"

    @override
    def _to_row(self, obj: Project) -> SqlRow:
        return {
            "category_id": obj.category_id,
            "name": obj.name,
            "goal": obj.goal,
            "target_date": obj.target_date.isoformat() if obj.target_date is not None else None,
            "event_id": obj.event_id,
            "status": obj.status.value,
        }

    @override
    def _from_row(self, row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            category_id=row["category_id"],
            name=row["name"],
            goal=row["goal"],
            target_date=date.fromisoformat(row["target_date"]) if row["target_date"] else None,
            event_id=row["event_id"],
            status=ProjectStatus(row["status"]),
        )

    def list_all(self, *, category_id: int | None = None) -> list[Project]:
        """プロジェクトを作った順に読む。

        Args:
            category_id: 指定すればその分類のものだけ。

        Returns:
            プロジェクトの一覧。
        """
        if category_id is None:
            return self._select()
        return self._select("category_id = ?", (category_id,))


class TaskRepository(_Repository[Task]):
    """タスクのテーブル。"""

    table = "tasks"
    order_by = "sort_order, id"

    @override
    def _to_row(self, obj: Task) -> SqlRow:
        relation = obj.event_relation
        return {
            "category_id": obj.category_id,
            "title": obj.title,
            "note": obj.note,
            "status": obj.status.value,
            "priority": obj.priority,
            "start_at": _iso(obj.start_at),
            "due_at": _iso(obj.due_at),
            "event_id": obj.event_id,
            "event_relation": relation.value if relation is not None else None,
            "project_id": obj.project_id,
            "parent_task_id": obj.parent_task_id,
            "progress_current": obj.progress_current,
            "progress_target": obj.progress_target,
            "sort_order": obj.sort_order,
            "created_at": _iso(obj.created_at),
            "completed_at": _iso(obj.completed_at),
        }

    @override
    def _from_row(self, row: sqlite3.Row) -> Task:
        relation = row["event_relation"]
        return Task(
            id=row["id"],
            category_id=row["category_id"],
            title=row["title"],
            note=row["note"],
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            start_at=_dt(row["start_at"]),
            due_at=_dt(row["due_at"]),
            event_id=row["event_id"],
            event_relation=EventRelation(relation) if relation else None,
            project_id=row["project_id"],
            parent_task_id=row["parent_task_id"],
            progress_current=row["progress_current"],
            progress_target=row["progress_target"],
            sort_order=row["sort_order"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=_dt(row["completed_at"]),
        )

    def list_all(
        self,
        *,
        category_id: int | None = None,
        event_id: int | None = None,
        project_id: int | None = None,
        include_done: bool = True,
    ) -> list[Task]:
        """タスクを並び順に読む。

        Args:
            category_id: 指定すればその分類のものだけ。
            event_id: 指定すればそのイベントに紐付くものだけ。
            project_id: 指定すればそのプロジェクトに属するものだけ。
            include_done: 完了したタスクも含めるか。

        Returns:
            タスクの一覧。
        """
        conditions: list[str] = []
        params: list[SqlValue] = []
        for column, value in (
            ("category_id", category_id),
            ("event_id", event_id),
            ("project_id", project_id),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        if not include_done:
            conditions.append("status != 'done'")
        return self._select(" AND ".join(conditions), params)

    def children(self, parent_task_id: int) -> list[Task]:
        """直下のサブタスクを読む。

        Args:
            parent_task_id: 親タスクの id。

        Returns:
            直下のサブタスク。
        """
        return self._select("parent_task_id = ?", (parent_task_id,))

    def descendants(self, task_id: int) -> list[Task]:
        """孫以下も含めたすべてのサブタスクを読む。

        Args:
            task_id: 親タスクの id。

        Returns:
            すべてのサブタスク。
        """
        return self._select(
            """
            id IN (
                WITH RECURSIVE down(id) AS (
                    SELECT id FROM tasks WHERE parent_task_id = ?
                    UNION
                    SELECT t.id FROM tasks t JOIN down ON t.parent_task_id = down.id
                )
                SELECT id FROM down
            )
            """,
            (task_id,),
        )

    @override
    def add(self, obj: Task) -> Task:
        """親タスクを確かめてから追加する。

        Args:
            obj: 追加するタスク。

        Returns:
            id を設定した obj。
        """
        self._check_parent(obj)
        return super().add(obj)

    @override
    def update(self, obj: Task) -> None:
        """親タスクを確かめてから書き換える。

        Args:
            obj: 書き換えるタスク。
        """
        self._check_parent(obj)
        super().update(obj)

    def _check_parent(self, task: Task) -> None:
        """親が存在し、親子関係が循環しないことを確かめる(サブタスクは何階層でもよい)。

        Raises:
            ValueError: 親が自分自身、存在しない、または自分のサブタスクの場合。
        """
        if task.parent_task_id is None:
            return
        if task.id is not None and task.parent_task_id == task.id:
            msg = "自分自身を親タスクにはできない"
            raise ValueError(msg)
        if self.get(task.parent_task_id) is None:
            msg = "親タスクが存在しない"
            raise ValueError(msg)
        if task.id is None:
            return
        row = self._conn.execute(
            """
            WITH RECURSIVE up(id) AS (
                SELECT ?
                UNION
                SELECT t.parent_task_id FROM tasks t JOIN up ON t.id = up.id
                WHERE t.parent_task_id IS NOT NULL
            )
            SELECT 1 FROM up WHERE id = ?
            """,
            (task.parent_task_id, task.id),
        ).fetchone()
        if row is not None:
            msg = "自分のサブタスクを親タスクにはできない"
            raise ValueError(msg)


class RecurrenceRepository:
    """繰り返しタスクの設定のテーブル。主キーはタスクの id。

    Args:
        conn: DB の接続。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Recurrence:
        return Recurrence(
            task_id=row["task_id"],
            freq=Freq(row["freq"]),
            active_from=_dt(row["active_from"]),
            active_until=_dt(row["active_until"]),
            event_id=row["event_id"],
        )

    def get(self, task_id: int) -> Recurrence | None:
        """タスクの繰り返しの設定を読む。

        Args:
            task_id: タスクの id。

        Returns:
            繰り返しの設定。なければ None。
        """
        row = self._conn.execute(
            "SELECT * FROM recurrences WHERE task_id = ?", (task_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def by_task_id(self) -> dict[int, Recurrence]:
        """全件を読む。

        Returns:
            タスク id → 繰り返しの設定。
        """
        rows = self._conn.execute("SELECT * FROM recurrences")
        return {int(row["task_id"]): self._from_row(row) for row in rows}

    def set(self, recurrence: Recurrence) -> None:
        """タスクの繰り返しの設定を書く(既にあれば置き換える)。

        Args:
            recurrence: 書く設定。
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO recurrences (task_id, freq, active_from, active_until, event_id)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(task_id) DO UPDATE SET freq = excluded.freq,"
                " active_from = excluded.active_from, active_until = excluded.active_until,"
                " event_id = excluded.event_id",
                (
                    recurrence.task_id,
                    recurrence.freq.value,
                    _iso(recurrence.active_from),
                    _iso(recurrence.active_until),
                    recurrence.event_id,
                ),
            )

    def delete(self, task_id: int) -> None:
        """タスクの繰り返しの設定を消す。

        Args:
            task_id: タスクの id。
        """
        with self._conn:
            self._conn.execute("DELETE FROM recurrences WHERE task_id = ?", (task_id,))


class CompletionRepository:
    """繰り返しタスクの達成記録のテーブル。

    Args:
        conn: DB の接続。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Completion:
        return Completion(
            task_id=row["task_id"],
            period_key=row["period_key"],
            completed_at=datetime.fromisoformat(row["completed_at"]),
            count=row["count"],
        )

    def get(self, task_id: int, period_key: str) -> Completion | None:
        """ある期間の達成記録を読む。

        Args:
            task_id: タスクの id。
            period_key: 期間のキー。

        Returns:
            達成記録。なければ None。
        """
        row = self._conn.execute(
            "SELECT * FROM completions WHERE task_id = ? AND period_key = ?",
            (task_id, period_key),
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_task(self, task_id: int) -> list[Completion]:
        """タスクの達成記録を期間の順に読む。

        Args:
            task_id: タスクの id。

        Returns:
            達成記録の一覧。
        """
        rows = self._conn.execute(
            "SELECT * FROM completions WHERE task_id = ? ORDER BY period_key", (task_id,)
        )
        return [self._from_row(row) for row in rows]

    def list_for_keys(self, period_keys: Iterable[str]) -> list[Completion]:
        """指定した period_key のいずれかに属する達成記録を読む。

        Args:
            period_keys: 期間のキー。

        Returns:
            達成記録の一覧。
        """
        keys = list(period_keys)
        if not keys:
            return []
        marks = ", ".join("?" for _ in keys)
        rows = self._conn.execute(
            f"SELECT * FROM completions WHERE period_key IN ({marks})"  # noqa: S608
            " ORDER BY period_key, task_id",
            keys,
        )
        return [self._from_row(row) for row in rows]

    def set_count(self, task_id: int, period_key: str, count: int, *, now: datetime) -> None:
        """その期間の達成回数を設定する。0 以下なら記録を消す(未達成に戻す)。

        Args:
            task_id: タスクの id。
            period_key: 期間のキー。
            count: 達成回数。
            now: 現在の日時(達成した日時として書く)。
        """
        if count <= 0:
            self.clear(task_id, period_key)
            return
        with self._conn:
            self._conn.execute(
                "INSERT INTO completions (task_id, period_key, completed_at, count)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(task_id, period_key) DO UPDATE SET"
                " count = excluded.count, completed_at = excluded.completed_at",
                (task_id, period_key, _iso(now), count),
            )

    def clear(self, task_id: int, period_key: str) -> None:
        """ある期間の達成記録を消す。

        Args:
            task_id: タスクの id。
            period_key: 期間のキー。
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM completions WHERE task_id = ? AND period_key = ?",
                (task_id, period_key),
            )


def _minutes(value: timedelta | None) -> int | None:
    return None if value is None else int(value.total_seconds() // 60)


def _offset(minutes: int | None) -> timedelta | None:
    return None if minutes is None else timedelta(minutes=minutes)


class TemplateRepository(_Repository[EventTemplate]):
    """イベントのテンプレートと、その中のタスク。"""

    table = "event_templates"
    order_by = "name, id"

    @override
    def _to_row(self, obj: EventTemplate) -> SqlRow:
        return {
            "category_id": obj.category_id,
            "name": obj.name,
            "duration_minutes": _minutes(obj.duration),
            "start_time": obj.start_time.strftime("%H:%M"),
            "url": obj.url,
            "note": obj.note,
            "created_at": _iso(obj.created_at),
        }

    @override
    def _from_row(self, row: sqlite3.Row) -> EventTemplate:
        return EventTemplate(
            id=row["id"],
            category_id=row["category_id"],
            name=row["name"],
            duration=timedelta(minutes=row["duration_minutes"]),
            start_time=time.fromisoformat(row["start_time"]),
            url=row["url"],
            note=row["note"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _task_to_row(task: TemplateTask) -> SqlRow:
        relation = task.event_relation
        return {
            "template_id": task.template_id,
            "parent_id": task.parent_id,
            "category_id": task.category_id,
            "title": task.title,
            "note": task.note,
            "priority": task.priority,
            "linked": int(task.linked),
            "event_relation": relation.value if relation is not None else None,
            "start_offset_minutes": _minutes(task.start_offset),
            "due_offset_minutes": _minutes(task.due_offset),
            "progress_target": task.progress_target,
            "freq": task.freq.value if task.freq is not None else None,
            "active_from_offset_minutes": _minutes(task.active_from_offset),
            "active_until_offset_minutes": _minutes(task.active_until_offset),
            "sort_order": task.sort_order,
        }

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TemplateTask:
        relation = row["event_relation"]
        freq = row["freq"]
        return TemplateTask(
            id=row["id"],
            template_id=row["template_id"],
            parent_id=row["parent_id"],
            category_id=row["category_id"],
            title=row["title"],
            note=row["note"],
            priority=row["priority"],
            linked=bool(row["linked"]),
            event_relation=EventRelation(relation) if relation else None,
            start_offset=_offset(row["start_offset_minutes"]),
            due_offset=_offset(row["due_offset_minutes"]),
            progress_target=row["progress_target"],
            freq=Freq(freq) if freq else None,
            active_from_offset=_offset(row["active_from_offset_minutes"]),
            active_until_offset=_offset(row["active_until_offset_minutes"]),
            sort_order=row["sort_order"],
        )

    def list_all(self) -> list[EventTemplate]:
        """テンプレートを名前の順に読む。

        Returns:
            テンプレートの一覧。
        """
        return self._select()

    def get_by_name(self, name: str) -> EventTemplate | None:
        """名前でテンプレートを読む。

        Args:
            name: テンプレートの名前。

        Returns:
            テンプレート。なければ None。
        """
        found = self._select("name = ?", (name,))
        return found[0] if found else None

    def tasks(self, template_id: int) -> list[TemplateTask]:
        """テンプレートの中のタスクを並び順に読む。

        Args:
            template_id: テンプレートの id。

        Returns:
            テンプレートの中のタスク。
        """
        rows = self._conn.execute(
            "SELECT * FROM template_tasks WHERE template_id = ? ORDER BY sort_order, id",
            (template_id,),
        )
        return [self._task_from_row(row) for row in rows]

    def task_counts(self) -> dict[int, int]:
        """テンプレートごとのタスクの件数を数える。

        Returns:
            テンプレート id → タスクの件数。
        """
        rows = self._conn.execute(
            "SELECT template_id, COUNT(*) FROM template_tasks GROUP BY template_id"
        )
        return {int(template_id): int(count) for template_id, count in rows}

    def dependencies(self, template_id: int) -> list[Dependency]:
        """テンプレートの中のタスクどうしの前後関係を読む。

        Args:
            template_id: テンプレートの id。

        Returns:
            前後関係(id はテンプレートの中のタスクの id)。
        """
        rows = self._conn.execute(
            "SELECT d.predecessor_id, d.successor_id FROM template_dependencies d"
            " JOIN template_tasks t ON t.id = d.predecessor_id"
            " WHERE t.template_id = ? ORDER BY d.predecessor_id, d.successor_id",
            (template_id,),
        )
        return list(starmap(Dependency, rows))

    def save(
        self,
        template: EventTemplate,
        tasks: Iterable[TemplateTask],
        dependencies: Iterable[Dependency] = (),
    ) -> EventTemplate:
        """テンプレートを追加または更新し、中のタスクと前後関係をすべて置き換える。

        1トランザクションで行う。tasks は親が子より先に来る順で渡す。各タスクの id と
        parent_id は呼び出し側の仮の番号で、保存時に採番し直す。dependencies も同じ仮の番号で渡す。
        保存後、template.id と各タスクの id・template_id・parent_id を更新する。

        Args:
            template: 保存するテンプレート。
            tasks: テンプレートの中のタスク。
            dependencies: タスクどうしの前後関係。

        Returns:
            id を設定した template。
        """
        row = self._to_row(template)
        with self._conn:
            if template.id is None:
                template.id = _insert(self._conn, self.table, row)
            else:
                _update(self._conn, self.table, row, item_id=template.id)
                self._conn.execute(
                    "DELETE FROM template_tasks WHERE template_id = ?", (template.id,)
                )
            new_ids = self._insert_tasks(template.id, tasks)
            self._insert_dependencies(new_ids, dependencies)
        return template

    def _insert_tasks(self, template_id: int, tasks: Iterable[TemplateTask]) -> dict[int, int]:
        """テンプレートの中のタスクを入れ、id と parent_id を採番し直す。

        Returns:
            仮の番号 → 採番した id。

        Raises:
            ValueError: 子タスクが親タスクより先に並んでいる場合。
        """
        new_ids: dict[int, int] = {}
        for task in tasks:
            old_id = task.id
            task.template_id = template_id
            if task.parent_id is not None:
                if task.parent_id not in new_ids:
                    msg = "親タスクは子タスクより先に並べる"
                    raise ValueError(msg)
                task.parent_id = new_ids[task.parent_id]
            task.id = _insert(self._conn, "template_tasks", self._task_to_row(task))
            if old_id is not None:
                new_ids[old_id] = task.id
        return new_ids

    def _insert_dependencies(
        self, new_ids: dict[int, int], dependencies: Iterable[Dependency]
    ) -> None:
        """テンプレートの中の前後関係を、採番し直した id で入れる。

        Raises:
            ValueError: 前後関係のタスクがテンプレートにない場合。
        """
        for dep in dependencies:
            if dep.predecessor_id not in new_ids or dep.successor_id not in new_ids:
                msg = "前後関係のタスクがテンプレートにない"
                raise ValueError(msg)
            self._conn.execute(
                "INSERT OR IGNORE INTO template_dependencies (predecessor_id, successor_id)"
                " VALUES (?, ?)",
                (new_ids[dep.predecessor_id], new_ids[dep.successor_id]),
            )


class DependencyRepository:
    """タスク間の前後関係。

    Args:
        conn: DB の接続。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_all(self) -> list[Dependency]:
        """前後関係をすべて読む。

        Returns:
            前後関係の一覧。
        """
        rows = self._conn.execute(
            "SELECT predecessor_id, successor_id FROM task_dependencies"
            " ORDER BY predecessor_id, successor_id"
        )
        return list(starmap(Dependency, rows))

    def add(self, dependency: Dependency) -> None:
        """前後関係を追加する。すでにあれば何もしない。

        Args:
            dependency: 追加する前後関係。
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (predecessor_id, successor_id)"
                " VALUES (?, ?)",
                (dependency.predecessor_id, dependency.successor_id),
            )

    def delete(self, dependency: Dependency) -> None:
        """前後関係を消す。

        Args:
            dependency: 消す前後関係。
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM task_dependencies WHERE predecessor_id = ? AND successor_id = ?",
                (dependency.predecessor_id, dependency.successor_id),
            )


class SettingsRepository:
    """アプリの設定(キーと値の組)。

    Args:
        conn: DB の接続。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> str | None:
        """設定の値を読む。

        Args:
            key: 設定のキー。

        Returns:
            設定の値。なければ None。
        """
        row = self._conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set(self, key: str, value: str) -> None:
        """設定の値を書く。

        Args:
            key: 設定のキー。
            value: 設定の値。
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

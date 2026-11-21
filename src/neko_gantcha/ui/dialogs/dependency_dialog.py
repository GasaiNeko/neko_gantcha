"""タスク間の前後関係を結ぶときの、相手のタスクの選択。"""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QInputDialog, QMessageBox

from neko_gantcha.domain.models import TaskStatus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from neko_gantcha.domain.models import Category, Task
    from neko_gantcha.services.store import AppStore


def task_choice_label(task: Task, categories: dict[int, Category]) -> str:
    """候補のタスクを表示用の文字列にする。

    Args:
        task: 候補のタスク。
        categories: 分類 id → 分類。

    Returns:
        `編成を組む(ソシャゲA / 期限 11/25 23:59)` の形の文字列。
    """
    parts: list[str] = []
    category = categories.get(task.category_id)
    if category is not None:
        parts.append(category.name)
    if task.due_at is not None:
        parts.append(f"期限 {task.due_at:%m/%d %H:%M}")
    if task.status is TaskStatus.DONE:
        parts.append("完了")
    return f"{task.title}({' / '.join(parts)})" if parts else task.title


def add_dependency_interactive(
    parent: QWidget, store: AppStore, task_id: int, *, as_predecessor: bool
) -> bool:
    """相手のタスクを選ばせて前後関係を加える。

    Args:
        parent: ダイアログの親。
        store: データ操作の窓口。
        task_id: 関係を結ぶ側のタスクの id。
        as_predecessor: 真なら task_id の前に終えるタスクを、偽なら後に続くタスクを選ぶ。

    Returns:
        加えたら True。
    """
    task = store.task(task_id)
    if task is None:
        return False
    title = "前のタスクを追加" if as_predecessor else "後のタスクを追加"
    candidates = store.dependency_candidates(task_id, as_predecessor=as_predecessor)
    if not candidates:
        QMessageBox.information(parent, title, "前後関係を結べるタスクがありません。")
        return False
    categories = {c.saved_id: c for c in store.categories(include_archived=True)}
    labels: list[str] = []
    seen: dict[str, int] = {}
    for candidate in candidates:
        label = task_choice_label(candidate, categories)
        # 同じ表示が並ぶと選び分けられないので番号を付ける
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f"{label} #{seen[label]}")
    prompt = (
        f"「{task.title}」より先に終えるタスク"
        if as_predecessor
        else f"「{task.title}」を終えてから取りかかるタスク"
    )
    choice, accepted = QInputDialog.getItem(parent, title, prompt, labels, 0, False)
    if not accepted or choice not in labels:
        return False
    other = candidates[labels.index(choice)]
    if as_predecessor:
        store.add_dependency(other.saved_id, task_id)
    else:
        store.add_dependency(task_id, other.saved_id)
    return True

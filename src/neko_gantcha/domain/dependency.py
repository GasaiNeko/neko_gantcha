"""タスク間の前後関係(依存関係)。Qt にも DB にも依存しない。

関係は「先のタスク(predecessor)を終えてから、後のタスク(successor)に取りかかる」の1種類だけ。
循環(A→B→A など)は作らせない。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from neko_gantcha.domain.models import Dependency


def reachable(start: int, dependencies: Iterable[Dependency]) -> set[int]:
    """タスク start から後ろ向きに(successor の方向へ)たどれるタスクの id を返す。

    Args:
        start: たどり始めるタスクの id。
        dependencies: すべての前後関係。

    Returns:
        たどれるタスクの id。start 自身は含まない。
    """
    following: dict[int, list[int]] = {}
    for dep in dependencies:
        following.setdefault(dep.predecessor_id, []).append(dep.successor_id)
    seen: set[int] = set()
    pending = list(following.get(start, []))
    while pending:
        task_id = pending.pop()
        if task_id in seen:
            continue
        seen.add(task_id)
        pending.extend(following.get(task_id, []))
    return seen


def creates_cycle(
    dependencies: Iterable[Dependency], predecessor_id: int, successor_id: int
) -> bool:
    """関係 predecessor → successorを足すと循環するか。

    Args:
        dependencies: いまある前後関係。
        predecessor_id: 先のタスクの id。
        successor_id: 後のタスクの id。

    Returns:
        循環するなら True。自分自身への関係も循環とみなす。
    """
    if predecessor_id == successor_id:
        return True
    return predecessor_id in reachable(successor_id, dependencies)


def internal_links(
    dependencies: Iterable[Dependency], task_ids: Collection[int]
) -> list[Dependency]:
    """両端とも task_ids に含まれる関係を返す(複製やテンプレートで一緒に写すもの)。

    Args:
        dependencies: すべての前後関係。
        task_ids: 対象のタスクの id。

    Returns:
        両端が対象に含まれる関係。
    """
    return [
        dep
        for dep in dependencies
        if dep.predecessor_id in task_ids and dep.successor_id in task_ids
    ]

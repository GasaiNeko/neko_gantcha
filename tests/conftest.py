"""テスト共通の fixture と補助。"""

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from neko_gantcha.data import db
from neko_gantcha.services.store import AppStore

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

    from PySide6.QtCore import SignalInstance


class Clock:
    """テスト用の時計。now を書き換えて時間を進める。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Recorder:
    """シグナルが出た回数と引数を記録する。"""

    def __init__(self, signal: SignalInstance) -> None:
        self.calls: list[tuple[object, ...]] = []
        signal.connect(lambda *args: self.calls.append(args))

    @property
    def count(self) -> int:
        return len(self.calls)


def must[T](value: T | None) -> T:
    """値があることを確かめて返す。なければテストを失敗にする。"""
    if value is None:
        pytest.fail("値がありません")
    return value


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """最新スキーマまで適用したメモリ上の DB。"""
    connection = db.open_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 11, 16, 12, 0, 0))


@pytest.fixture
def store(conn: sqlite3.Connection, clock: Clock) -> AppStore:
    return AppStore(conn, clock)

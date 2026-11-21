"""リセット時刻で区切った「期間」と period_key の計算。

期間は [start, end) の半開区間。end は次のリセット日時と一致する。

- 日課: リセット時刻から翌日のリセット時刻まで。キーは開始日 `2026-11-16`。
- 週課: リセット曜日のリセット時刻から1週間。キーは開始日の ISO 週 `2026-W47`。
- 月課: リセット日のリセット時刻から翌月のリセットまで。キーは開始月 `2026-11`。
  リセット日がその月にない場合(31日など)は月末日に丸める。
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from neko_gantcha.domain.models import Freq

if TYPE_CHECKING:
    from neko_gantcha.domain.models import ResetRule


@dataclass(frozen=True, slots=True)
class Period:
    """繰り返しタスクの1期間。[start, end) の半開区間。

    Attributes:
        key: 期間を表す文字列(達成記録の period_key)。
        start: 期間の始まり。
        end: 次のリセット日時(この時刻は期間に含まない)。
    """

    key: str
    start: datetime
    end: datetime  # 次のリセット日時(この時刻は期間に含まない)

    def contains(self, moment: datetime) -> bool:
        """日時がこの期間に含まれるか。

        Args:
            moment: 調べる日時。

        Returns:
            start 以上 end 未満なら True。
        """
        return self.start <= moment < self.end


def current_period(freq: Freq, now: datetime, rule: ResetRule) -> Period:
    """日時 now が属する期間を返す。

    Args:
        freq: 繰り返しの頻度。
        now: 現在の日時。
        rule: リセットの設定。

    Returns:
        now を含む期間。

    Raises:
        ValueError: freq が未知の値の場合。
    """
    match freq:
        case Freq.DAILY:
            return _daily(now, rule)
        case Freq.WEEKLY:
            return _weekly(now, rule)
        case Freq.MONTHLY:
            return _monthly(now, rule)
    msg = f"未知の freq：{freq!r}"
    raise ValueError(msg)


def period_key(freq: Freq, now: datetime, rule: ResetRule) -> str:
    """日時 now が属する期間のキーを返す。

    Args:
        freq: 繰り返しの頻度。
        now: 現在の日時。
        rule: リセットの設定。

    Returns:
        日課なら `2026-11-16`、週課なら `2026-W47`、月課なら `2026-11` の形のキー。
    """
    return current_period(freq, now, rule).key


def next_reset(freq: Freq, now: datetime, rule: ResetRule) -> datetime:
    """次にリセットされる日時を返す。

    Args:
        freq: 繰り返しの頻度。
        now: 現在の日時。
        rule: リセットの設定。

    Returns:
        now を含む期間の終わり(次の期間の始まり)。
    """
    return current_period(freq, now, rule).end


def _reset_offset(rule: ResetRule) -> timedelta:
    t = rule.daily_time
    return timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)


def _daily(now: datetime, rule: ResetRule) -> Period:
    # リセット時刻ぶん巻き戻した日付が、その時点の「ゲーム内の日付」
    day = (now - _reset_offset(rule)).date()
    start = datetime.combine(day, rule.daily_time)
    return Period(day.isoformat(), start, start + timedelta(days=1))


def _weekly(now: datetime, rule: ResetRule) -> Period:
    day = (now - _reset_offset(rule)).date()
    start_day = day - timedelta(days=(day.weekday() - rule.weekly_weekday) % 7)
    start = datetime.combine(start_day, rule.daily_time)
    iso = start_day.isocalendar()
    return Period(f"{iso.year}-W{iso.week:02d}", start, start + timedelta(days=7))


def _monthly_reset_at(year: int, month: int, rule: ResetRule) -> datetime:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.combine(date(year, month, min(rule.monthly_day, last_day)), rule.daily_time)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _monthly(now: datetime, rule: ResetRule) -> Period:
    year, month = now.year, now.month
    start = _monthly_reset_at(year, month, rule)
    if now < start:
        year, month = _shift_month(year, month, -1)
        start = _monthly_reset_at(year, month, rule)
    end = _monthly_reset_at(*_shift_month(year, month, 1), rule)
    return Period(f"{year:04d}-{month:02d}", start, end)

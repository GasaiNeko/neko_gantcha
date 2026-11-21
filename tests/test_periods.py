"""period_key と期間境界のテスト。"""

from datetime import datetime, time, timedelta

import pytest

from neko_gantcha.domain.models import Freq, ResetRule
from neko_gantcha.domain.periods import current_period, next_reset, period_key

GAME = ResetRule(time(5, 0), 0, 1)  # 毎日 5:00・月曜・毎月1日
LIFE = ResetRule(time(0, 0), 0, 1)  # 0:00・月曜・1日


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --- 日課 ---


def test_daily_before_reset_belongs_to_previous_day() -> None:
    # 計画書の例: リセット 5:00 なら 11/17 の 3:00 はまだ 11/16 の日課
    assert period_key(Freq.DAILY, dt("2026-11-17T03:00"), GAME) == "2026-11-16"


def test_daily_just_before_and_at_reset() -> None:
    assert period_key(Freq.DAILY, dt("2026-11-17T04:59:59"), GAME) == "2026-11-16"
    assert period_key(Freq.DAILY, dt("2026-11-17T05:00:00"), GAME) == "2026-11-17"


def test_daily_bounds_and_next_reset() -> None:
    period = current_period(Freq.DAILY, dt("2026-11-17T03:00"), GAME)
    assert period.start == dt("2026-11-16T05:00")
    assert period.end == dt("2026-11-17T05:00")
    assert next_reset(Freq.DAILY, dt("2026-11-17T03:00"), GAME) == dt("2026-11-17T05:00")


def test_daily_midnight_reset() -> None:
    assert period_key(Freq.DAILY, dt("2026-11-16T23:59:59"), LIFE) == "2026-11-16"
    assert period_key(Freq.DAILY, dt("2026-11-17T00:00:00"), LIFE) == "2026-11-17"


def test_daily_month_end_and_year_crossing() -> None:
    assert period_key(Freq.DAILY, dt("2026-12-01T04:00"), GAME) == "2026-11-30"
    assert period_key(Freq.DAILY, dt("2027-01-01T04:59"), GAME) == "2026-12-31"
    assert period_key(Freq.DAILY, dt("2027-01-01T05:00"), GAME) == "2027-01-01"
    # うるう年の 2/29
    assert period_key(Freq.DAILY, dt("2028-03-01T02:00"), GAME) == "2028-02-29"


# --- 週課 ---


def test_weekly_key_matches_iso_week() -> None:
    # 2026-11-16 は月曜で ISO 第47週
    assert period_key(Freq.WEEKLY, dt("2026-11-16T12:00"), GAME) == "2026-W47"


def test_weekly_boundary_at_monday_reset() -> None:
    assert period_key(Freq.WEEKLY, dt("2026-11-16T04:59:59"), GAME) == "2026-W46"
    assert period_key(Freq.WEEKLY, dt("2026-11-16T05:00:00"), GAME) == "2026-W47"
    # 日曜の深夜〜月曜リセット前までは同じ週
    assert period_key(Freq.WEEKLY, dt("2026-11-22T23:59"), GAME) == "2026-W47"
    assert period_key(Freq.WEEKLY, dt("2026-11-23T04:59"), GAME) == "2026-W47"
    assert period_key(Freq.WEEKLY, dt("2026-11-23T05:00"), GAME) == "2026-W48"


def test_weekly_bounds() -> None:
    period = current_period(Freq.WEEKLY, dt("2026-11-19T10:00"), GAME)
    assert period.start == dt("2026-11-16T05:00")
    assert period.end == dt("2026-11-23T05:00")


def test_weekly_non_monday_reset() -> None:
    thursday = ResetRule(time(5, 0), 3, 1)
    # 2026-11-19 は木曜
    before = current_period(Freq.WEEKLY, dt("2026-11-19T04:59"), thursday)
    after = current_period(Freq.WEEKLY, dt("2026-11-19T05:00"), thursday)
    assert before.start == dt("2026-11-12T05:00")
    assert before.key == "2026-W46"
    assert after.start == dt("2026-11-19T05:00")
    assert after.key == "2026-W47"


def test_weekly_year_crossing() -> None:
    # 2026-12-28(月)に始まる週は ISO 2026-W53。元日はまだその週に属する
    assert period_key(Freq.WEEKLY, dt("2026-12-28T05:00"), GAME) == "2026-W53"
    assert period_key(Freq.WEEKLY, dt("2027-01-01T12:00"), GAME) == "2026-W53"
    assert period_key(Freq.WEEKLY, dt("2027-01-04T04:59"), GAME) == "2026-W53"
    assert period_key(Freq.WEEKLY, dt("2027-01-04T05:00"), GAME) == "2027-W01"


# --- 月課 ---


def test_monthly_boundary_at_first_day_reset() -> None:
    assert period_key(Freq.MONTHLY, dt("2026-12-01T04:59:59"), GAME) == "2026-11"
    assert period_key(Freq.MONTHLY, dt("2026-12-01T05:00:00"), GAME) == "2026-12"


def test_monthly_year_crossing() -> None:
    assert period_key(Freq.MONTHLY, dt("2027-01-01T03:00"), GAME) == "2026-12"
    period = current_period(Freq.MONTHLY, dt("2027-01-01T05:00"), GAME)
    assert period.key == "2027-01"
    assert period.start == dt("2027-01-01T05:00")
    assert period.end == dt("2027-02-01T05:00")


def test_monthly_reset_day_missing_in_month_is_clamped() -> None:
    day31 = ResetRule(time(5, 0), 0, 31)
    # 2027年2月は28日まで。リセットは 2/28 5:00 に丸める
    mid_feb = current_period(Freq.MONTHLY, dt("2027-02-15T12:00"), day31)
    assert mid_feb.key == "2027-01"
    assert mid_feb.start == dt("2027-01-31T05:00")
    assert mid_feb.end == dt("2027-02-28T05:00")

    feb = current_period(Freq.MONTHLY, dt("2027-02-28T05:00"), day31)
    assert feb.key == "2027-02"
    assert feb.end == dt("2027-03-31T05:00")

    # 30日までの月
    april = current_period(Freq.MONTHLY, dt("2027-04-30T05:00"), day31)
    assert april.key == "2027-04"
    assert april.start == dt("2027-04-30T05:00")
    assert april.end == dt("2027-05-31T05:00")


def test_monthly_clamp_in_leap_year() -> None:
    day30 = ResetRule(time(0, 0), 0, 30)
    period = current_period(Freq.MONTHLY, dt("2028-02-29T00:00"), day30)
    assert period.key == "2028-02"
    assert period.end == dt("2028-03-30T00:00")


def test_monthly_mid_month_reset_day() -> None:
    day15 = ResetRule(time(5, 0), 0, 15)
    assert period_key(Freq.MONTHLY, dt("2026-11-15T04:59"), day15) == "2026-10"
    assert period_key(Freq.MONTHLY, dt("2026-11-15T05:00"), day15) == "2026-11"
    assert period_key(Freq.MONTHLY, dt("2026-12-14T23:00"), day15) == "2026-11"


# --- 共通の性質 ---


@pytest.mark.parametrize("freq", list(Freq))
@pytest.mark.parametrize(
    "rule",
    [GAME, LIFE, ResetRule(time(5, 0), 3, 31), ResetRule(time(23, 30), 6, 29)],
)
def test_periods_are_contiguous_and_keys_unique(freq: Freq, rule: ResetRule) -> None:
    """2年ぶんを順にたどり、期間が隙間なく続き、キーが重複しないことを確かめる。"""
    moment = dt("2026-01-01T00:00")
    stop = dt("2028-03-01T00:00")
    seen: set[str] = set()
    while moment < stop:
        period = current_period(freq, moment, rule)
        assert period.contains(moment)
        assert period.key not in seen
        seen.add(period.key)
        # 終了直前は同じ期間、終了時刻ちょうどは次の期間で、その開始と一致する
        assert period_key(freq, period.end - timedelta(seconds=1), rule) == period.key
        following = current_period(freq, period.end, rule)
        assert following.start == period.end
        moment = period.end


def test_reset_rule_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match=r"weekly_weekday\ は\ 0〜6\ で指定する"):
        ResetRule(time(5, 0), 7, 1)
    with pytest.raises(ValueError, match=r"monthly_day\ は\ 1〜31\ で指定する"):
        ResetRule(time(5, 0), 0, 0)
    with pytest.raises(ValueError, match=r"monthly_day\ は\ 1〜31\ で指定する"):
        ResetRule(time(5, 0), 0, 32)

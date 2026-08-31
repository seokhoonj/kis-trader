from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.execution import (
    TWAPSchedule,
    TWAPSlice,
    make_twap_schedule,
    parse_duration,
    split_quantity,
)

_KST = timezone(timedelta(hours=9))


def _scheduled_at(hour, minute):
    return datetime(2026, 8, 31, hour, minute, tzinfo=_KST)


def test_split_quantity_even():
    assert split_quantity(100, 10) == [10] * 10


def test_split_quantity_distributes_remainder_to_front():
    assert split_quantity(103, 10) == [11, 11, 11, 10, 10, 10, 10, 10, 10, 10]


@pytest.mark.parametrize("total,parts", [(0, 5), (5, 0), (3, 5)])
def test_split_quantity_rejects_nonpositive_or_undersized(total, parts):
    with pytest.raises(KISUsageError):
        split_quantity(total, parts)


@pytest.mark.parametrize("text,expected", [
    ("30m", timedelta(minutes=30)), ("1h", timedelta(hours=1)),
    ("90s", timedelta(seconds=90)), ("1h30m", timedelta(hours=1, minutes=30)),
])
def test_parse_duration_strings(text, expected):
    assert parse_duration(text) == expected


def test_parse_duration_passthrough_timedelta():
    assert parse_duration(timedelta(minutes=5)) == timedelta(minutes=5)


@pytest.mark.parametrize("bad", ["", "abc", "0m", "-5m", "1x"])
def test_parse_duration_rejects_bad_strings(bad):
    with pytest.raises(KISUsageError):
        parse_duration(bad)


@pytest.mark.parametrize("bad", [timedelta(0), timedelta(seconds=-1)])
def test_parse_duration_rejects_nonpositive_timedelta(bad):
    with pytest.raises(KISUsageError):
        parse_duration(bad)


def test_parse_duration_rejects_overflow():
    with pytest.raises(KISUsageError):
        parse_duration("100000000000h")


def test_make_schedule_slice_times_and_quantities():
    schedule = make_twap_schedule(symbol="005930", side="buy", quantity=100,
                                  duration="30m", slices=3, now=_scheduled_at(10, 0))
    assert isinstance(schedule, TWAPSchedule)
    assert schedule.total_quantity == 100
    assert [s.quantity for s in schedule.slices] == [34, 33, 33]
    assert [s.at for s in schedule.slices] == [
        _scheduled_at(10, 0), _scheduled_at(10, 10), _scheduled_at(10, 20)]
    assert schedule.slices[0] == TWAPSlice(at=_scheduled_at(10, 0), quantity=34)


def test_make_schedule_start_overrides_now():
    schedule = make_twap_schedule(symbol="005930", side="sell", quantity=10, duration="10m",
                                  slices=2, start=_scheduled_at(13, 0), now=_scheduled_at(9, 30))
    assert schedule.slices[0].at == _scheduled_at(13, 0)


def test_make_schedule_allows_exact_open_boundary():
    schedule = make_twap_schedule(symbol="005930", side="buy", quantity=2, duration="60m",
                                  slices=2, now=_scheduled_at(9, 0))
    assert schedule.slices[0].at == _scheduled_at(9, 0)          # exactly 09:00 is in-session


def test_make_schedule_allows_exact_close_boundary():
    schedule = make_twap_schedule(symbol="005930", side="buy", quantity=1, duration="10m",
                                  slices=1, start=_scheduled_at(15, 30), now=_scheduled_at(9, 0))
    assert schedule.slices[0].at == _scheduled_at(15, 30)        # exactly 15:30 is in-session


def test_make_schedule_rejects_session_spill():
    # now 15:20 + 30m over 3 slices -> last slice 15:40 > 15:30 close -> reject
    with pytest.raises(KISUsageError, match="정규장"):
        make_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                           slices=3, now=_scheduled_at(15, 20))


def test_make_schedule_rejects_before_open():
    with pytest.raises(KISUsageError, match="정규장"):
        make_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                           slices=3, now=_scheduled_at(8, 30))


def test_make_schedule_rejects_cross_day_slice():
    # 48h over 2 slices -> slice2 lands next day at an in-session wall-clock; date must not be dropped.
    with pytest.raises(KISUsageError, match="정규장"):
        make_twap_schedule(symbol="005930", side="buy", quantity=2, duration="48h",
                           slices=2, now=_scheduled_at(10, 0))


def test_make_schedule_rejects_past_start():
    with pytest.raises(KISUsageError, match="과거"):
        make_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                           slices=3, start=_scheduled_at(9, 30), now=_scheduled_at(10, 0))


def test_make_schedule_rejects_naive_datetime():
    with pytest.raises(KISUsageError, match="naive"):
        make_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                           slices=3, now=datetime(2026, 8, 31, 10, 0))  # noqa: DTZ001 -- naive on purpose

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.execution import (
    TwapSchedule,
    TwapSlice,
    build_twap_schedule,
    parse_duration,
    split_quantity,
)

_KST = timezone(timedelta(hours=9))


def _at(h, m):
    return datetime(2026, 8, 31, h, m, tzinfo=_KST)


def test_split_quantity_even():
    assert split_quantity(100, 10) == [10] * 10


def test_split_quantity_distributes_remainder_to_front():
    assert split_quantity(103, 10) == [11, 11, 11, 10, 10, 10, 10, 10, 10, 10]


@pytest.mark.parametrize("total,parts", [(0, 5), (5, 0), (3, 5)])
def test_split_quantity_rejects_bad(total, parts):
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
def test_parse_duration_rejects_bad(bad):
    with pytest.raises(KISUsageError):
        parse_duration(bad)


def test_build_schedule_slice_times_and_quantities():
    sched = build_twap_schedule(symbol="005930", side="buy", quantity=100,
                                duration="30m", slices=3, now=_at(10, 0))
    assert isinstance(sched, TwapSchedule)
    assert sched.total_quantity == 100
    assert [s.quantity for s in sched.slices] == [34, 33, 33]
    assert [s.at for s in sched.slices] == [_at(10, 0), _at(10, 10), _at(10, 20)]
    assert sched.slices[0] == TwapSlice(at=_at(10, 0), quantity=34)


def test_build_schedule_start_overrides_now():
    sched = build_twap_schedule(symbol="005930", side="sell", quantity=10, duration="10m",
                                slices=2, start=_at(13, 0), now=_at(9, 30))
    assert sched.slices[0].at == _at(13, 0)


def test_build_schedule_rejects_session_spill():
    # now 15:20 + 30m over 3 slices -> last slice 15:40 > 15:30 close -> reject
    with pytest.raises(KISUsageError, match="정규장"):
        build_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                            slices=3, now=_at(15, 20))


def test_build_schedule_rejects_before_open():
    with pytest.raises(KISUsageError, match="정규장"):
        build_twap_schedule(symbol="005930", side="buy", quantity=9, duration="30m",
                            slices=3, now=_at(8, 30))

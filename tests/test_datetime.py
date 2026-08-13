"""날짜/시각 와이어 변환 헬퍼(:mod:`kis_trader._datetime`) 회귀 테스트."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

import pytest

from kis_trader._datetime import _to_yyyymmdd, parse_optional_kst_date
from kis_trader.errors import KISError, KISUsageError


def test_parse_optional_kst_date_none_is_none():
    # B-02: JSON null/None 은 str() 전에 갈라 "None" 문자열 파싱을 막는다.
    assert parse_optional_kst_date(None) is None


def test_parse_optional_kst_date_empty_and_sentinel_are_none():
    assert parse_optional_kst_date("") is None
    assert parse_optional_kst_date("00000000") is None


def test_parse_optional_kst_date_valid_returns_date():
    result = parse_optional_kst_date("20201210")
    assert result == date(2020, 12, 10)
    assert type(result) is date              # datetime 이 아니라 순수 date

    hyphenated = parse_optional_kst_date("2020-12-10")
    assert hyphenated == date(2020, 12, 10)


def test_parse_optional_kst_date_malformed_raises():
    # 달력에 없는 날짜는 조용히 None 이 아니라 fail-closed.
    with pytest.raises(KISError):
        parse_optional_kst_date("20230230")
    with pytest.raises(KISError):
        parse_optional_kst_date("2023")      # 8자리 아님


def test_parse_optional_kst_date_required_raises_on_missing():
    with pytest.raises(KISError):
        parse_optional_kst_date(None, required=True)
    with pytest.raises(KISError):
        parse_optional_kst_date("00000000", required=True)


def test_to_yyyymmdd_accepts_date_and_strings():
    assert _to_yyyymmdd(date(2020, 12, 10), "d") == "20201210"
    assert _to_yyyymmdd(datetime(2020, 12, 10, 9, 30, tzinfo=_KST), "d") == "20201210"
    assert _to_yyyymmdd("20201210", "d") == "20201210"
    assert _to_yyyymmdd("2020-12-10", "d") == "20201210"


def test_to_yyyymmdd_rejects_impossible_date():
    # A-32: 8자리 숫자여도 달력에 없는 날짜는 거부.
    with pytest.raises(KISUsageError):
        _to_yyyymmdd("20230230", "d")


def test_to_yyyymmdd_rejects_non_str_non_date():
    # A-32: int 가 str() 을 타고 통과하던 버그 -- 이제 타입 자체를 거부.
    with pytest.raises(KISUsageError):
        _to_yyyymmdd(20201210, "d")

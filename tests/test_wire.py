"""와이어 값 강제변환 -- 전일대비 부호 복원의 fail-closed 계약.

``_apply_change_sign`` 은 KIS 대비부호(prdy_vrss_sign)로 크기의 방향을 정한다. 하락(4/5)은
음수, 상승/보합(1/2/3)과 빈 부호("")는 양수로 두되, **알 수 없는** 부호는 조용히 양수로 두지
않고 예외로 막는다(잘못된 부호는 대비값을 통째로 뒤집으므로).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kis_trader._internal._wire import (
    _apply_change_sign,
    decimal_or_zero,
    format_wire_decimal,
    optional_decimal,
    optional_int,
    required_decimal,
    required_int,
)
from kis_trader.errors import KISError


@pytest.mark.parametrize("sign", ["1", "2", "3"])
def test_apply_change_sign_up_and_flat_stay_positive(sign):
    assert _apply_change_sign(Decimal(400), sign) == Decimal(400)
    assert _apply_change_sign(Decimal(-400), sign) == Decimal(400)   # 크기로 정규화 후 방향


@pytest.mark.parametrize("sign", ["4", "5"])
def test_apply_change_sign_down_codes_go_negative(sign):
    assert _apply_change_sign(Decimal(400), sign) == Decimal(-400)
    assert _apply_change_sign(Decimal(-400), sign) == Decimal(-400)


def test_apply_change_sign_empty_is_no_change_positive():
    assert _apply_change_sign(Decimal(400), "") == Decimal(400)


@pytest.mark.parametrize("sign", ["0", "6", "9", "x", "12"])
def test_apply_change_sign_unknown_code_fails_closed(sign):
    with pytest.raises(KISError):
        _apply_change_sign(Decimal(400), sign)


# --- required/optional decimal ---------------------------------------------
def test_required_decimal_parses_and_strips():
    assert required_decimal("70000", "px") == Decimal(70000)
    assert required_decimal("  -1.23 ", "px") == Decimal("-1.23")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_decimal_rejects_empty(value):
    with pytest.raises(KISError):
        required_decimal(value, "px")


@pytest.mark.parametrize("value", ["abc", "1,000", "NaN", "Infinity", "-inf"])
def test_required_decimal_rejects_malformed_or_nonfinite(value):
    with pytest.raises(KISError):
        required_decimal(value, "px")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_optional_decimal_blank_is_none(value):
    assert optional_decimal(value, "px") is None


@pytest.mark.parametrize("value", ["abc", "NaN", "Infinity"])
def test_optional_decimal_rejects_nonfinite_or_malformed(value):
    with pytest.raises(KISError):
        optional_decimal(value, "px")


# --- required/optional int -------------------------------------------------
def test_required_int_accepts_integer_valued_decimal_string():
    assert required_int("1234", "vol") == 1234
    assert required_int("1234.0", "vol") == 1234       # KIS 가 정수를 "1234.0" 로 줄 때
    assert required_int("1E3", "vol") == 1000


@pytest.mark.parametrize("value", ["1.5", "0.1", "1234.001"])
def test_required_int_rejects_fractional(value):
    with pytest.raises(KISError):
        required_int(value, "vol")


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "abc"])
def test_required_int_rejects_empty_or_nonfinite(value):
    with pytest.raises(KISError):
        required_int(value, "vol")


def test_optional_int_blank_is_none_and_rejects_fractional():
    assert optional_int("", "vol") is None
    assert optional_int(None, "vol") is None
    assert optional_int("42", "vol") == 42
    with pytest.raises(KISError):
        optional_int("1.5", "vol")


# --- format_wire_decimal (지문/와이어 정본) --------------------------------
@pytest.mark.parametrize(("value", "expected"), [
    (Decimal("1E+3"), "1000"),        # 지수표기를 펼친다
    (Decimal(10), "10"),
    (Decimal("70000.00"), "70000.00"),  # 반올림/정규화 없이 고정소수점 그대로
    (Decimal("-1.23"), "-1.23"),
])
def test_format_wire_decimal_is_fixed_point(value, expected):
    assert format_wire_decimal(value) == expected


# --- decimal_or_zero (재조회 경로) ----------------------------------
@pytest.mark.parametrize("value", [None, ""])
def test_decimal_or_zero_blank_is_zero(value):
    assert decimal_or_zero(value) == Decimal(0)


def test_decimal_or_zero_parses_value():
    assert decimal_or_zero("71500") == Decimal(71500)


@pytest.mark.parametrize("value", ["abc", "NaN", "Infinity", "-Infinity"])
def test_decimal_or_zero_rejects_malformed_or_nonfinite(value):
    with pytest.raises(KISError):
        decimal_or_zero(value)

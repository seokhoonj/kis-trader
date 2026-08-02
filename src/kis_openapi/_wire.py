"""KIS 와이어 값(문자열) -> 타입 수치 강제변환 -- **fail-closed**.

KIS 응답의 수치는 전부 문자열이다("70000", "", "  ", "-1.23"). 이 모듈은 그것을
:class:`~decimal.Decimal` / :class:`int` 로 바꾸되, **값이 있는데 파싱에 실패하면 조용히
0으로 만들지 않고** :class:`~kis_openapi.errors.KisError` 를 올린다. 시세는 신뢰 못 할
숫자를 조작하면 그대로 오판(잘못된 가격/거래량)이 되므로, 파싱 실패는 항상 예외다.

빈 필드("" / 공백 / None)의 처리는 required 냐 optional 이냐로 갈린다: required 는 예외,
optional 은 ``None`` 이다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import KisError


def required_decimal(value: object, field_name: str) -> Decimal:
    """반드시 있어야 하는 수치. 빈 값/파싱 실패는 :class:`KisError`."""
    text = _strip(value)
    if not text:
        raise KisError(f"필수 수치 필드 {field_name!r} 가 비어 있다: {value!r}")
    return _to_decimal(text, field_name)


def optional_decimal(value: object, field_name: str) -> Decimal | None:
    """있으면 Decimal, 비어 있으면 ``None``. 값이 있는데 파싱 실패면 :class:`KisError`."""
    text = _strip(value)
    if not text:
        return None
    return _to_decimal(text, field_name)


def required_int(value: object, field_name: str) -> int:
    """반드시 있어야 하는 정수(거래량 등). 빈 값/파싱 실패는 :class:`KisError`.

    KIS가 정수도 소수점 문자열로 줄 때가 있어(예: "1234.0") Decimal 을 거쳐 정수화한다.
    소수부가 있으면(진짜 정수가 아니면) 조작하지 않고 예외로 fail-closed 한다.
    """
    number = required_decimal(value, field_name)
    if number != number.to_integral_value():
        raise KisError(f"정수 필드 {field_name!r} 에 소수부가 있다: {value!r}")
    return int(number)


def optional_int(value: object, field_name: str) -> int | None:
    """있으면 int, 비어 있으면 ``None``. 값이 있는데 정수가 아니면 :class:`KisError`."""
    text = _strip(value)
    if not text:
        return None
    number = _to_decimal(text, field_name)
    if number != number.to_integral_value():
        raise KisError(f"정수 필드 {field_name!r} 에 소수부가 있다: {value!r}")
    return int(number)


def _strip(value: object) -> str:
    """None/숫자/문자열을 공백 제거한 문자열로. None 은 빈 문자열."""
    if value is None:
        return ""
    return str(value).strip()


def _to_decimal(text: str, field_name: str) -> Decimal:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KisError(f"KIS 수치 필드 {field_name!r} 파싱 실패: {text!r}") from err

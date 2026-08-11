"""KIS 와이어 값(문자열) -> 타입 수치 강제변환 -- **fail-closed**.

KIS 응답의 수치는 전부 문자열이다("70000", "", "  ", "-1.23"). 이 모듈은 그것을
:class:`~decimal.Decimal` / :class:`int` 로 바꾸되, **값이 있는데 파싱에 실패하면 조용히
0으로 만들지 않고** :class:`~kis_openapi.errors.KISError` 를 올린다. 시세는 신뢰 못 할
숫자를 조작하면 그대로 오판(잘못된 가격/거래량)이 되므로, 파싱 실패는 항상 예외다.

빈 필드("" / 공백 / None)의 처리는 required 냐 optional 이냐로 갈린다: required 는 예외,
optional 은 ``None`` 이다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import KISError


def required_decimal(value: object, field_name: str) -> Decimal:
    """반드시 있어야 하는 수치. 빈 값/파싱 실패는 :class:`KISError`."""
    text = _strip(value)
    if not text:
        raise KISError(f"필수 수치 필드 {field_name!r} 가 비어 있다: {value!r}")
    return _to_decimal(text, field_name)


def optional_decimal(value: object, field_name: str) -> Decimal | None:
    """있으면 Decimal, 비어 있으면 ``None``. 값이 있는데 파싱 실패면 :class:`KISError`."""
    text = _strip(value)
    if not text:
        return None
    return _to_decimal(text, field_name)


def required_int(value: object, field_name: str) -> int:
    """반드시 있어야 하는 정수(거래량 등). 빈 값/파싱 실패는 :class:`KISError`.

    KIS가 정수도 소수점 문자열로 줄 때가 있어(예: "1234.0") Decimal 을 거쳐 정수화한다.
    소수부가 있으면(진짜 정수가 아니면) 조작하지 않고 예외로 fail-closed 한다.
    """
    number = required_decimal(value, field_name)
    if number != number.to_integral_value():
        raise KISError(f"정수 필드 {field_name!r} 에 소수부가 있다: {value!r}")
    return int(number)


def optional_int(value: object, field_name: str) -> int | None:
    """있으면 int, 비어 있으면 ``None``. 값이 있는데 정수가 아니면 :class:`KISError`."""
    text = _strip(value)
    if not text:
        return None
    number = _to_decimal(text, field_name)
    if number != number.to_integral_value():
        raise KISError(f"정수 필드 {field_name!r} 에 소수부가 있다: {value!r}")
    return int(number)


def format_wire_decimal(value: Decimal) -> str:
    """Decimal 을 KIS 와이어 정본 문자열로: 지수표기·컨텍스트 반올림 없이 고정소수점.

    ``format(x, "f")`` 는 ``normalize()`` 와 달리 정밀도로 반올림하지 않고 지수표기만 펼친다.
    KIS 와이어에 실리는 모든 수치(주문 단가/수량, 사전점검 단가, 요청 지문)가 **같은** 정본을
    쓰도록 여기 한 곳에 둔다 -- 정본이 갈리면 와이어가 동일한 값이 서로 다른 문자열이 되어
    주문 멱등 판정(지문 비교)이 깨진다.
    """
    return format(value, "f")


#: KIS 대비기호(prdy_vrss_sign) 중 하락을 뜻하는 코드(4=하한, 5=하락).
_DOWN_SIGNS = frozenset(("4", "5"))


def _apply_change_sign(magnitude: Decimal, sign_code: str) -> Decimal:
    """전일대비 값에 방향 부호를 입힌다(하락 코드면 음수).

    KIS가 크기만 주든(부호 없는) 이미 부호를 실어 주든 상관없이 옳도록, 크기를 ``abs`` 로
    정규화한 뒤 부호코드로만 방향을 정한다(부호가 이중 적용돼 뒤집히는 일 방지).
    """
    size = abs(magnitude)
    return -size if sign_code in _DOWN_SIGNS else size


def _strip(value: object) -> str:
    """None/숫자/문자열을 공백 제거한 문자열로. None 은 빈 문자열."""
    if value is None:
        return ""
    return str(value).strip()


def _to_decimal(text: str, field_name: str) -> Decimal:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISError(f"KIS 수치 필드 {field_name!r} 파싱 실패: {text!r}") from err

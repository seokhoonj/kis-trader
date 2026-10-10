"""실시간 파서 공용 수치 변환 -- :func:`_decimal` (가격) 과 :func:`_int` (수량).

6개 실시간 파서 모듈(domestic_stock/overseas/derivatives/index/bond/elw)이 공유한다.
``_internal._wire.field_decimal_or_zero`` 와 같은 fail-closed 정책: 빈 값만 0 으로 보고,
비어있지 않은데 파싱 불가하거나 유한하지 않으면(NaN/Infinity) 조작된 0 을 지어내지 않고
닫는다. 여기서 KISError 를 올리면 ``RealtimeConnection._decode`` 가 그 프레임을 드롭한다
(스트림은 중단되지 않음) -- 신뢰 못 할 수치를 frozen 엔티티로 흘려 오판하는 것을 막는다.

가격은 :func:`_decimal` (Decimal), 시장데이터 수량(호가 잔량·거래량·누적량·종목 수·투자자
플로 수량 등)은 :func:`_int` (int) 를 쓴다 -- REST 정본이 호가 잔량/거래량을 int 로,
가격을 Decimal 로 파싱하는 것과 같은 개념별 타입이다. 분수주식(소수점)은 내 주문/보유 수량에만
생기는 브로커 레벨 현상이고 거래소가 relay 하는 집계 호가/거래량엔 없으므로, 수량 필드에
소수부가 오면 신뢰 못 할 프레임으로 보고 닫는다. (주문/체결 수량은 REST ``Order.quantity`` 와
같이 Decimal 로 두어 미국 미니스탁 분수 체결을 담는다 -- 그 필드는 :func:`_decimal` 을 쓴다.)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ...errors import KISError


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값은 0(스트림 연속성). 비어있지 않은데 파싱 불가하거나
    유한하지 않으면(NaN/Infinity) KISError 로 닫아 그 프레임을 드롭시킨다."""
    if not value:
        return Decimal(0)
    try:
        number = Decimal(value)
    except InvalidOperation as err:
        raise KISError(f"realtime 수치 필드 파싱 실패: {value!r}") from err
    if not number.is_finite():
        raise KISError(f"realtime 수치가 유한하지 않다(NaN/Infinity 금지): {value!r}")
    return number


def _int(value: str) -> int:
    """실시간 정수 필드(호가 잔량·거래량·종목 수 등) -> int. 빈 값은 0(스트림 연속성).
    :func:`_decimal` 을 거쳐 파싱·유한성을 확인하고, 소수부가 있으면(진짜 정수가 아니면)
    조작하지 않고 KISError 로 닫아 그 프레임을 드롭시킨다(REST ``required_int`` 와 같은
    fail-closed). KIS 가 정수를 "1234.0" 으로 줄 때도 정수화한다."""
    number = _decimal(value)
    if number != number.to_integral_value():
        raise KISError(f"realtime 정수 필드에 소수부가 있다: {value!r}")
    return int(number)

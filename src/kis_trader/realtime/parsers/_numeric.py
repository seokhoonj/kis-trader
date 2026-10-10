"""실시간 파서 공용 수치 변환 -- :func:`_decimal`.

6개 실시간 파서 모듈(domestic_stock/overseas/derivatives/index/bond/elw)이 공유한다.
``_internal._wire.field_decimal_or_zero`` 와 같은 fail-closed 정책: 빈 값만 0 으로 보고,
비어있지 않은데 파싱 불가하거나 유한하지 않으면(NaN/Infinity) 조작된 0 을 지어내지 않고
닫는다. 여기서 KISError 를 올리면 ``RealtimeConnection._decode`` 가 그 프레임을 드롭한다
(스트림은 중단되지 않음) -- 신뢰 못 할 수치를 frozen 엔티티로 흘려 오판하는 것을 막는다.
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

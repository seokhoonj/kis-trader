"""국내주식 미체결(정정·취소 가능) 주문 한 건(DATA) -- :class:`OpenOrder`.

브로커(한국투자증권)에 남아 있는 접수 주문 중 아직 정정·취소할 수 있는 건들이다. 해외의
:class:`~kis_openapi.overseas_items.OverseasOpenOrder` 와 대칭인 국내판으로, 브로커 측 뷰라
우리 ``client_order_id`` 는 없고 KIS 주문번호(``order_id``)로 식별한다.

정정·취소 전에 ``cancelable_quantity`` (KIS ``psbl_qty``, 정정/취소 가능수량)를 확인하라 --
미체결 잔량(``unfilled_quantity``)과 대개 같지만 일부 주문구분에선 다를 수 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class OpenOrder:
    """국내주식 미체결 주문 한 건(불변).

    ``order_id`` KIS 주문번호(odno), ``original_order_id`` 는 이 건이 정정/취소 주문이면 그
    원주문번호(아니면 빈 문자열), ``branch_number`` 주문 채번 지점번호. ``quantity`` 주문수량,
    ``filled_quantity`` 체결수량, ``unfilled_quantity`` 미체결 잔량(주문-체결), ``cancelable_quantity``
    정정/취소 가능수량. ``price`` 주문단가(KRW), ``order_time`` 주문시각(HH:MM:SS, 없으면 None).
    """

    symbol: str
    name: str
    order_id: str                     # 주문번호(odno)
    original_order_id: str            # 원주문번호(orgn_odno) -- 정정/취소 주문이 아니면 ""
    branch_number: str                # 주문채번지점번호(ord_gno_brno)
    side: str                         # buy / sell
    order_type: str                   # 주문구분명(ord_dvsn_name), 예: "지정가"
    quantity: Decimal                 # 주문수량
    filled_quantity: Decimal          # 총체결수량
    unfilled_quantity: Decimal        # 미체결 잔량(주문-체결)
    cancelable_quantity: Decimal      # 정정/취소 가능수량(psbl_qty)
    price: Decimal                    # 주문단가(KRW)
    order_time: time | None           # 주문시각
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

"""예약주문(DATA) -- :class:`ReservedOrder`.

예약주문은 정규장이 열리지 않는 시간에 미리 걸어두고 다음 영업일(또는 지정 기간) 아침 동시호가에
집행되는 주문이다. 즉시체결 주문(:class:`~kis_trader.report.ExecutionReport`)과 라이프사이클이
달라(예약순번 ``sequence`` 로 식별, 집행 전엔 체결번호 없음) 별도 타입으로 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .order import Side


@dataclass(frozen=True, slots=True)
class ReservedOrder:
    """예약주문 한 건(불변).

    ``sequence`` 예약주문순번(정정·취소 시 이 값으로 지목), ``order_date`` 예약집행 예정일,
    ``status`` 처리결과(예: "미처리"/"처리"), ``executed_order_id`` 집행됐다면 그 주문번호(미집행이면 ""),
    ``reservation_end_date`` 예약 유효 종료일. 금액/수량은 KRW·주(株) Decimal.
    """

    sequence: str                     # 예약주문순번(rsvn_ord_seq)
    order_date: date | None           # 예약주문주문일자(rsvn_ord_ord_dt) -- 집행 예정일
    received_date: date | None        # 예약주문접수일자(rsvn_ord_rcit_dt)
    symbol: str
    name: str
    side: Side                        # buy / sell
    order_type_name: str              # 주문구분명(ord_dvsn_name), 예: "현금매수"
    reserved_quantity: Decimal        # 주문예약수량(ord_rsvn_qty)
    filled_quantity: Decimal          # 총체결수량(tot_ccld_qty)
    order_price: Decimal              # 주문예약단가(ord_rsvn_unpr)
    status: str                       # 처리결과(prcs_rslt)
    reject_reason: str                # 거부사유(rjct_rson2)
    executed_order_id: str            # 집행 주문번호(odno), 미집행이면 ""
    reservation_end_date: date | None  # 예약종료일자(rsvn_end_dt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

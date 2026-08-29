"""국내주식 일별 주문·체결 내역 DATA -- 개별 주문·체결 행(:class:`StockFill`)과 기간 합계
요약(:class:`StockFillHistory`).

``kis.account.domestic.fills(start=, end=)`` 가 돌려주는 조회 전용 스냅샷이다. 한 행은 한 주문의
주문·체결 상태를 함께 담는다(주문수량/단가, 체결수량/평균가/금액, 잔여·거부수량, 취소여부).
금액·수량은 KRW Decimal. 실전·모의(``TTTC0081R``/``VTTC0081R``) 모두 지원한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload
from ...order import Side


@dataclass(frozen=True, slots=True)
class StockFill:
    """국내주식 일별 주문·체결 내역 한 건(불변).

    ``order_quantity`` / ``order_price`` 는 주문, ``filled_quantity`` / ``average_price`` /
    ``filled_amount`` 는 체결, ``unfilled_quantity`` 는 잔여(미체결)수량, ``rejected_quantity`` 는
    거부수량이다. ``cancelled`` 는 취소여부. ``side`` 는 매수/매도.
    """

    order_date: date | None           # 주문일자(ord_dt)
    order_id: str                     # 주문번호(odno)
    original_order_id: str            # 원주문번호(orgn_odno)
    order_type: str                   # 주문구분명(ord_dvsn_name)
    side: Side                        # 매도매수구분(sll_buy_dvsn_cd)
    symbol: str                       # 상품번호/종목코드(pdno)
    name: str                         # 상품명(prdt_name)
    order_quantity: Decimal           # 주문수량(ord_qty)
    order_price: Decimal              # 주문단가(ord_unpr)
    order_time: time | None           # 주문시각(ord_tmd)
    filled_quantity: Decimal          # 총체결수량(tot_ccld_qty)
    average_price: Decimal            # 체결평균가(avg_prvs)
    filled_amount: Decimal            # 총체결금액(tot_ccld_amt)
    unfilled_quantity: Decimal        # 잔여수량(rmn_qty)
    rejected_quantity: Decimal        # 거부수량(rjct_qty)
    cancelled: bool                   # 취소여부(cncl_yn)
    branch_number: str                # 주문채번지점번호(ord_gno_brno)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class StockFillHistory:
    """국내주식 일별 주문·체결 내역과 기간 합계 요약(불변).

    ``fills`` 는 개별 주문·체결 행(:class:`StockFill`)이고, ``total_*`` 은 조회 기간의 합계다.
    ``average_purchase_price`` 는 기간 매입평균가격, ``estimated_expenses`` 는 추정제비용합계.
    금액·수량은 KRW Decimal.
    """

    fills: tuple[StockFill, ...]      # 개별 주문·체결 내역(output1)
    total_order_quantity: Decimal     # 총주문수량(tot_ord_qty)
    total_filled_quantity: Decimal    # 총체결수량(tot_ccld_qty)
    total_filled_amount: Decimal      # 총체결금액(tot_ccld_amt)
    average_purchase_price: Decimal   # 매입평균가격(pchs_avg_pric)
    estimated_expenses: Decimal       # 추정제비용합계(prsm_tlex_smtl)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "fills", tuple(self.fills))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

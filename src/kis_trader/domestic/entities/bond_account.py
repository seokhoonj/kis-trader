"""장내채권 계좌 잔고/체결(DATA) -- :class:`BondPosition`, :class:`BondBuyable`,
:class:`BondOpenOrder`, :class:`BondFill`, :class:`BondFillHistory`.

``kis.account.domestic.bonds`` 뷰가 돌려주는 장내채권 계좌 조회 결과다. 채권은 위탁(01) 계좌를
주식과 함께 쓰되 ``domestic-bond`` 전용 엔드포인트로 조회한다. 금액·수량은 KRW Decimal, 날짜는
``date``(공백/형식오류면 ``None``). 시세 DATA(:mod:`~kis_trader.domestic.entities.bond`)와 달리
여기는 보유·매수가능·미체결·체결 같은 계좌 관점의 값이다. 타입화하지 않은 벤더 필드는 ``_raw``.
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
class BondPosition:
    """한 장내채권 보유 lot 의 잔고 현황(불변).

    채권 잔고는 종목이 아니라 매수 단위(``buy_date`` + ``buy_sequence``)로 쪼개져 오므로 같은
    종목이 여러 lot 으로 나뉠 수 있다. ``comprehensive_tax_quantity`` 종합과세수량,
    ``separate_tax_quantity`` 분리과세수량은 세제 구분 수량이다.
    """

    symbol: str                       # 상품번호(pdno)
    name: str                         # 상품명(prdt_name)
    buy_date: date | None             # 매수일자(buy_dt)
    buy_sequence: str                 # 매수순번(buy_sqno)
    quantity: Decimal                 # 잔고수량(cblc_qty)
    comprehensive_tax_quantity: Decimal  # 종합과세수량(agrx_qty)
    separate_tax_quantity: Decimal    # 분리과세수량(sprx_qty)
    maturity_date: date | None        # 만기일자(exdt)
    buy_yield: Decimal                # 매수수익율(buy_erng_rt)
    buy_price: Decimal                # 매수단가(buy_unpr)
    buy_amount: Decimal               # 매수금액(buy_amt)
    orderable_quantity: Decimal       # 주문가능수량(ord_psbl_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BondBuyable:
    """한 장내채권을 지정 단가로 살 때의 매수가능 여력(불변).

    ``orderable_cash`` 주문가능현금, ``orderable_substitute`` 주문가능대용, ``reusable_amount``
    재사용가능금액, ``buyable_amount`` / ``buyable_quantity`` 매수가능금액/수량, ``cma_value``
    CMA평가금액. 금액은 KRW Decimal.
    """

    symbol: str                       # 조회한 표준코드(요청 PDNO)
    orderable_cash: Decimal           # 주문가능현금(ord_psbl_cash)
    orderable_substitute: Decimal     # 주문가능대용(ord_psbl_sbst)
    reusable_amount: Decimal          # 재사용가능금액(ruse_psbl_amt)
    buyable_amount: Decimal           # 매수가능금액(buy_psbl_amt)
    buyable_quantity: Decimal         # 매수가능수량(buy_psbl_qty)
    cma_value: Decimal                # CMA평가금액(cma_evlu_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BondOpenOrder:
    """정정·취소 가능한 장내채권 미체결 주문 한 건(불변).

    브로커 측 뷰라 우리 ``client_order_id`` 는 없다 -- 정정/취소는 ``order_id`` 로 지목한다.
    ``revise_cancel_type`` 정정취소구분명, ``cancelable_quantity`` 정정·취소 가능 수량,
    ``original_order_id`` 원주문번호(정정·취소 주문이면 원주문). ``side`` 는 매수/매도.
    """

    order_id: str                     # 주문번호(odno)
    symbol: str                       # 상품번호(pdno)
    name: str                         # 상품약어명(prdt_abrv_name)
    revise_cancel_type: str           # 정정취소구분명(rvse_cncl_dvsn_name)
    order_quantity: Decimal           # 주문수량(ord_qty)
    order_price: Decimal              # 채권주문단가(bond_ord_unpr)
    order_time: time | None           # 주문시각(ord_tmd)
    filled_quantity: Decimal          # 총체결수량(tot_ccld_qty)
    filled_amount: Decimal            # 총체결금액(tot_ccld_amt)
    cancelable_quantity: Decimal      # 주문가능수량(ord_psbl_qty)
    original_order_id: str            # 원주문번호(orgn_odno)
    side: Side                        # 매도매수구분(sll_buy_dvsn_cd)
    order_division: str               # 주문구분코드(ord_dvsn_cd)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

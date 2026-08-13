"""퇴직연금(IRP/DC) 계좌 조회 DATA -- 예수금·매수가능·잔고·미체결.

일반 위탁계좌(:mod:`~kis_openapi.balance`)와 별개인 퇴직연금 전용 조회 결과다. KIS가 퇴직연금
계좌를 별도 엔드포인트(`/trading/pension/...`)로 두므로 반환 타입도 나눈다. 금액은 KRW Decimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .balance import Position


@dataclass(frozen=True, slots=True)
class PensionDeposit:
    """퇴직연금 예수금 요약(불변).

    ``total_deposit`` 예수금총액, ``next_day_estimated_settlement_amount`` 익일정산액, ``next_day_settlement_amount``
    익일결제금액, ``second_day_settlement_amount`` 2익일결제금액.
    """

    total_deposit: Decimal                         # 예수금총액(dnca_tota)
    next_day_estimated_settlement_amount: Decimal  # 익일정산액(nxdy_excc_amt)
    next_day_settlement_amount: Decimal            # 익일결제금액(nxdy_sttl_amt)
    second_day_settlement_amount: Decimal          # 2익일결제금액(nx2_day_sttl_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class PensionBalance:
    """퇴직연금 잔고(불변) -- 보유종목과 예수금 기준 계좌 요약을 한 스냅샷으로.

    ``positions`` 보유종목(:class:`~kis_openapi.balance.Position` 재사용), ``total_deposit`` 예수금총액,
    ``next_day_estimated_settlement_amount`` 익일정산액, ``prior_settlement`` 가수도정산금액, ``securities_evaluation``
    유가평가금액, ``total_evaluation`` 총평가금액, ``today_buy_amount``/``today_sell_amount`` 당일 매수/매도금액.
    """

    positions: tuple[Position, ...]
    total_deposit: Decimal                         # 예수금총액(dnca_tot_amt)
    next_day_estimated_settlement_amount: Decimal  # 익일정산액(nxdy_excc_amt)
    prior_settlement: Decimal                      # 가수도정산금액(prvs_rcdl_excc_amt)
    securities_evaluation: Decimal                 # 유가평가금액(scts_evlu_amt)
    total_evaluation: Decimal                      # 총평가금액(tot_evlu_amt)
    today_buy_amount: Decimal                      # 당일매수금액(thdt_buy_amt)
    today_sell_amount: Decimal                     # 당일매도금액(thdt_sll_amt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class PensionPresentBalance:
    """퇴직연금 체결기준잔고(불변) -- 체결기준 보유종목과 손익 요약.

    ``positions`` 보유종목, ``total_purchase_amount`` 매입금액합계, ``total_evaluation_amount``
    평가금액합계, ``total_evaluation_pnl`` 평가손익합계, ``total_trade_pnl`` 매매손익합계,
    ``today_total_pnl`` 당일총손익, ``return_percent`` 수익률(%).
    """

    positions: tuple[Position, ...]
    total_purchase_amount: Decimal    # 매입금액합계(pchs_amt_smtl_amt)
    total_evaluation_amount: Decimal  # 평가금액합계(evlu_amt_smtl_amt)
    total_evaluation_pnl: Decimal     # 평가손익합계(evlu_pfls_smtl_amt)
    total_trade_pnl: Decimal          # 매매손익합계(trad_pfls_smtl)
    today_total_pnl: Decimal          # 당일총손익(thdt_tot_pfls_amt)
    return_percent: Decimal           # 수익률(pftrt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class PensionOrder:
    """퇴직연금 주문 한 건(불변) -- 당일 체결/미체결 내역. ``unfilled_quantity`` 미체결 잔량.

    ``order_id`` 주문번호, ``original_order_id`` 원주문번호(정정/취소면), ``branch_number`` 채번 지점,
    ``order_type`` 주문구분명, ``average_purchase_price`` 매입평균가격, ``order_time`` 주문시각.
    """

    order_id: str                     # 주문번호(odno)
    original_order_id: str            # 원주문번호(orgn_odno)
    branch_number: str                # 주문채번지점번호(ord_gno_brno)
    side: str                         # buy / sell
    order_type: str                   # 주문구분명(ord_dvsn_name)
    symbol: str
    name: str
    quantity: Decimal                 # 주문수량(ord_qty)
    filled_quantity: Decimal          # 총체결수량(tot_ccld_qty)
    unfilled_quantity: Decimal        # 미체결수량(nccs_qty)
    price: Decimal                    # 주문단가(ord_unpr)
    average_purchase_price: Decimal   # 매입평균가격(pchs_avg_pric)
    order_time: time | None           # 주문시각(ord_tmd)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class PensionBuyableAmount:
    """퇴직연금 매수가능 여력(불변).

    ``orderable_cash`` 주문가능현금, ``reusable_cash`` 재사용가능금액, ``calc_unit_price``
    가능수량계산단가, ``max_buyable_amount``/``max_buyable_quantity`` 최대 매수금액/수량.
    """

    symbol: str
    orderable_cash: Decimal           # 주문가능현금(ord_psbl_cash)
    reusable_cash: Decimal            # 재사용가능금액(ruse_psbl_amt)
    calc_unit_price: Decimal          # 가능수량계산단가(psbl_qty_calc_unpr)
    max_buyable_amount: Decimal       # 최대매수금액(max_buy_amt)
    max_buyable_quantity: Decimal     # 최대매수수량(max_buy_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

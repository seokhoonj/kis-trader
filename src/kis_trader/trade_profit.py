"""기간별 매매손익(DATA) -- :class:`TradeProfit`/:class:`TradeProfitHistory`(종목별),
:class:`DailyProfit`/:class:`DailyProfitHistory`(일별합산).

체결된 매매의 **실현손익** 원장이다. 미실현 평가손익(:class:`~kis_trader.balance.Position`)과
달리 매도로 확정된 손익을 기간별로 본다. 종목별(:class:`TradeProfit`)은 종목/매매 한 줄, 일별
(:class:`DailyProfit`)은 하루 합산 한 줄이다. 각 History 컨테이너가 그 목록과 기간 총계를 담는다.
금액은 KRW Decimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class TradeProfit:
    """한 종목/매매의 실현손익 한 줄(불변).

    ``purchase_price`` 매입단가, ``buy_amount``/``sell_amount`` 매수/매도 거래금액, ``realized_pnl``
    실현손익(매도로 확정), ``return_percent`` 손익률(%), ``fee`` 수수료, ``tax`` 제세금,
    ``loan_interest`` 대출이자.
    """

    trade_date: date | None           # 매매일자(trad_dt)
    symbol: str
    name: str
    trade_type: str                   # 매매구분명(trad_dvsn_name), 예: "현금"
    holding_quantity: Decimal         # 보유수량(hldg_qty)
    purchase_price: Decimal           # 매입단가(pchs_unpr)
    buy_quantity: Decimal             # 매수수량(buy_qty)
    buy_amount: Decimal               # 매수금액(buy_amt)
    sell_price: Decimal               # 매도가격(sll_pric)
    sell_quantity: Decimal            # 매도수량(sll_qty)
    sell_amount: Decimal              # 매도금액(sll_amt)
    realized_pnl: Decimal             # 실현손익(rlzt_pfls)
    return_percent: Decimal           # 손익률(pfls_rt)
    fee: Decimal                      # 수수료(fee)
    tax: Decimal                      # 제세금(tl_tax)
    loan_interest: Decimal            # 대출이자(loan_int)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class TradeProfitHistory:
    """기간 매매손익 목록과 총계(불변). ``trades`` 종목/매매별 실현손익, ``total_*`` 기간 합계."""

    trades: tuple[TradeProfit, ...]
    total_realized_pnl: Decimal       # 총실현손익(tot_rlzt_pfls)
    total_return_percent: Decimal     # 총수익률(tot_pftrt)
    total_buy_amount: Decimal         # 매수거래금액합계(buy_tr_amt_smtl)
    total_sell_amount: Decimal        # 매도거래금액합계(sll_tr_amt_smtl)
    total_fee: Decimal                # 총수수료(tot_fee)
    total_tax: Decimal                # 총제세금(tot_tltx)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "trades", tuple(self.trades))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DailyProfit:
    """하루치 매매손익 합산 한 줄(불변). 종목 구분 없이 그 날의 매수/매도금액·실현손익·수익률.

    ``return_percent`` 그 날의 손익률(%). 종목별 세부는 :class:`TradeProfit` 을 쓴다.
    """

    trade_date: date | None           # 매매일자(trad_dt)
    buy_amount: Decimal               # 매수금액(buy_amt)
    sell_amount: Decimal              # 매도금액(sll_amt)
    realized_pnl: Decimal             # 실현손익(rlzt_pfls)
    return_percent: Decimal           # 손익률(pfls_rt)
    fee: Decimal                      # 수수료(fee)
    tax: Decimal                      # 제세금(tl_tax)
    loan_interest: Decimal            # 대출이자(loan_int)
    buy_quantity: Decimal             # 매수수량(buy_qty1)
    sell_quantity: Decimal            # 매도수량(sll_qty1)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DailyProfitHistory:
    """기간 일별 매매손익 목록과 총계(불변). ``days`` 일별 실현손익, ``total_*`` 기간 합계.

    일별합산 응답에는 총수익률(tot_pftrt)이 없어 기간 총수익률 필드는 두지 않는다 -- 필요하면
    종목별 :class:`TradeProfitHistory`(``total_return_percent``)를 쓴다.
    """

    days: tuple[DailyProfit, ...]
    total_realized_pnl: Decimal       # 총실현손익(tot_rlzt_pfls)
    total_buy_amount: Decimal         # 매수거래금액합계(buy_tr_amt_smtl)
    total_sell_amount: Decimal        # 매도거래금액합계(sll_tr_amt_smtl)
    total_fee: Decimal                # 총수수료(tot_fee)
    total_tax: Decimal                # 총제세금(tot_tltx)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "days", tuple(self.days))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

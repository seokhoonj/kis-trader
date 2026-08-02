"""국내주식 주문가능 여력(DATA) -- :class:`BuyableAmount`, :class:`SellableQuantity`, 파서.

주문을 넣기 **전에** 확인하는 사전점검값이다. :class:`BuyableAmount` 는 한 종목을 얼마나 살 수
있는지(현금 기준과 미수 포함 최대), :class:`SellableQuantity` 는 얼마나 팔 수 있는지(보유수량 중
주문가능수량)를 담는다.

금액은 원화(KRW) :class:`~decimal.Decimal`. 다중통화(해외)가 필요해지면 ``Money`` 로 승격한다.

종목/단가 없이(금액만) 매수가능을 조회하거나 보유하지 않은 종목의 매도가능을 조회하면, 종목별
수량 필드는 자연히 0이다 -- KIS가 빈 값으로 주더라도 0으로 읽는다(값이 **있는데** 파싱 실패면
여전히 예외로 fail-closed). 계좌 현금(주문가능현금/재사용가능)은 항상 있는 값이라 필수로 읽는다.

KIS URL/TR-id:
- 매수가능조회: ``GET /uapi/domestic-stock/v1/trading/inquire-psbl-order``
  실전 ``TTTC8908R`` / 모의 ``VTTC8908R`` (모의 지원). 응답은 단일 ``output``.
- 매도가능수량조회: ``GET /uapi/domestic-stock/v1/trading/inquire-psbl-sell``
  ``TTTC8408R`` (**모의 미지원**). 응답은 단일 ``output1``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._wire import optional_decimal, required_decimal


@dataclass(frozen=True, slots=True)
class BuyableAmount:
    """한 종목의 매수가능 여력(불변). 금액은 KRW Decimal.

    ``cash_buyable_amount`` / ``cash_buyable_quantity`` 는 미수(외상)를 쓰지 않는 현금 기준,
    ``max_buyable_amount`` / ``max_buyable_quantity`` 는 미수를 포함한 최대치다. 종목/단가 없이
    조회하면(금액만) 종목별 수량·금액 필드는 0이 된다.
    """

    symbol: str                       # 조회한 종목(금액만 조회면 "")
    currency: str                     # 국내는 항상 KRW
    orderable_cash: Decimal           # ord_psbl_cash(주문가능현금)
    reusable_cash: Decimal            # ruse_psbl_amt(재사용가능금액)
    cash_buyable_amount: Decimal      # nrcvb_buy_amt(미수없는 매수가능금액)
    cash_buyable_quantity: Decimal    # nrcvb_buy_qty(미수없는 매수가능수량)
    max_buyable_amount: Decimal       # max_buy_amt(미수포함 최대매수금액)
    max_buyable_quantity: Decimal     # max_buy_qty(미수포함 최대매수수량)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


@dataclass(frozen=True, slots=True)
class SellableQuantity:
    """한 종목의 매도가능 수량(불변).

    ``quantity`` 는 보유 잔고수량, ``sellable_quantity`` 는 그중 실제 주문(매도) 가능한 수량이다
    (미결제·담보 등으로 둘이 다를 수 있다). 보유하지 않은 종목이면 둘 다 0이다.
    """

    symbol: str                       # pdno
    security_name: str                # prdt_name(종목명)
    quantity: Decimal                 # cblc_qty(잔고수량)
    sellable_quantity: Decimal        # ord_psbl_qty(주문가능=매도가능 수량)
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


def parse_buyable_amount(output: Mapping[str, Any], *, symbol: str) -> BuyableAmount:
    """매수가능조회 ``output`` 을 :class:`BuyableAmount` 로(순수).

    계좌 현금(주문가능현금/재사용가능)은 필수로 읽고, 종목별 매수 금액·수량은 빈 값을 0으로
    읽는다(종목/단가 없는 금액만 조회에서 자연히 빔). 값이 **있는데** 파싱 실패면 :class:`KisError`.
    """
    return BuyableAmount(
        symbol=symbol,
        currency="KRW",
        orderable_cash=required_decimal(output.get("ord_psbl_cash"), "ord_psbl_cash"),
        reusable_cash=required_decimal(output.get("ruse_psbl_amt"), "ruse_psbl_amt"),
        cash_buyable_amount=_amount_or_zero(output.get("nrcvb_buy_amt"), "nrcvb_buy_amt"),
        cash_buyable_quantity=_amount_or_zero(output.get("nrcvb_buy_qty"), "nrcvb_buy_qty"),
        max_buyable_amount=_amount_or_zero(output.get("max_buy_amt"), "max_buy_amt"),
        max_buyable_quantity=_amount_or_zero(output.get("max_buy_qty"), "max_buy_qty"),
        raw=output,
    )


def parse_sellable_quantity(output1: Mapping[str, Any], *, symbol: str) -> SellableQuantity:
    """매도가능수량조회 ``output1`` 을 :class:`SellableQuantity` 로(순수).

    수량은 빈 값을 0으로 읽는다(미보유 종목은 자연히 빔). 값이 있는데 파싱 실패면 :class:`KisError`.
    """
    return SellableQuantity(
        symbol=symbol,
        security_name=str(output1.get("prdt_name", "")).strip(),
        quantity=_amount_or_zero(output1.get("cblc_qty"), "cblc_qty"),
        sellable_quantity=_amount_or_zero(output1.get("ord_psbl_qty"), "ord_psbl_qty"),
        raw=output1,
    )


def _amount_or_zero(value: object, field_name: str) -> Decimal:
    """빈 값은 0, 값이 있으면 Decimal(파싱 실패면 :class:`KisError`). 의미상 '없음=0'인 필드용."""
    return optional_decimal(value, field_name) or Decimal(0)

"""해외 계좌 DATA -- :class:`OverseasPosition`, :class:`OverseasBalance`.

해외 잔고가 돌려주는 보유 종목(:meth:`~kis_openapi.client.KISClient.overseas_positions`)과 계좌
손익 요약(:meth:`~kis_openapi.client.KISClient.overseas_balance`). 금액은 종목/조회 통화
(USD/HKD/JPY/...)라 :class:`~kis_openapi.money.Money` 로 통화를 함께 담는다 -- 국내
:class:`~kis_openapi.balance.Position`(KRW Decimal)와 달리 다통화이기 때문이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .money import Money


@dataclass(frozen=True, slots=True)
class OverseasPosition:
    """해외 보유 종목 한 건(불변). 금액은 종목 통화의 :class:`Money`.

    ``quantity`` 는 보유 수량, ``sellable_quantity`` 는 매도가능 수량. ``unrealized_pnl`` 은 외화
    평가손익, ``pnl_percent`` 는 평가손익률(%).
    """

    symbol: str
    name: str
    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    quantity: int                     # 보유 수량
    sellable_quantity: int            # 매도가능 수량
    average_price: Money              # 매입 평균가
    current_price: Money              # 현재가
    purchase_amount: Money            # 외화 매입금액
    market_value: Money               # 평가금액
    unrealized_pnl: Money             # 외화 평가손익
    pnl_percent: Decimal              # 평가손익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasOpenOrder:
    """해외 미체결 주문 한 건(불변). 브로커 측 미체결 목록이라 우리 ``client_order_id`` 는 없고
    거래소 주문번호(``order_id``)로 식별한다. ``unfilled_quantity`` 는 아직 체결 안 된 잔량."""

    symbol: str
    name: str
    exchange: str                     # 해외거래소코드
    order_id: str                     # 거래소 주문번호(odno)
    side: str                         # buy / sell
    quantity: int                     # 주문수량
    filled_quantity: int              # 체결수량
    unfilled_quantity: int            # 미체결 잔량
    price: Money                      # 주문단가(종목 통화)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasBalance:
    """해외 계좌 손익 요약(불변). 조회한 거래소 그룹+통화 기준. 금액은 :class:`Money`.

    ``purchase_amount`` 보유분 매입금액, ``unrealized_pnl`` 평가손익, ``realized_pnl`` 실현손익,
    ``total_pnl`` 총손익(실현+평가), ``return_percent`` 총수익률(%). 예수금(현금)은 별도 조회다.
    """

    exchange: str                     # 조회한 해외거래소코드(OVRS_EXCG_CD)
    purchase_amount: Money            # 외화 매입금액
    unrealized_pnl: Money             # 평가손익
    realized_pnl: Money               # 실현손익
    total_pnl: Money                  # 총손익(실현+평가)
    return_percent: Decimal           # 총수익률(%)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class OverseasSettlementDate:
    """해외 시장별 현지·국내 결제일자 한 건(불변)."""

    market_type_code: str
    country_code: str
    country_name: str
    country_abbr: str
    market_code: str
    market_name: str
    local_settlement_date: date | None
    domestic_settlement_date: date | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

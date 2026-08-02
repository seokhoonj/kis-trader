"""국내주식 호가창(DATA) -- :class:`OrderBook`, :class:`PriceLevel`, :func:`parse_order_book`.

한 종목의 10단계 매수/매도 호가와 각 단계 잔량을 :class:`OrderBook` 으로 표현한다(Level 2 /
market depth). :attr:`OrderBook.bids` / :attr:`OrderBook.asks` 는 각각 최우선 호가가 index 0
(top of book)이고, 실제 주문이 있는 단계만 담는다(빈 단계는 제외).

KIS 응답에는 예상체결(``output2``, 예상체결가/예상거래량 등)도 함께 오지만, 이 값은 장 시작
전/마감 전 단일가 경매 시간대에만 의미가 있어 이 스냅샷에서는 다루지 않는다(별도 타입/슬라이스).
``.raw`` 는 이 객체가 파싱된 호가창 블록(``output1``)만 담는다 -- ``output2`` 는 담지 않는다.
실시간 호가 갱신은 WebSocket 레이어의 몫이다.

KIS URL/TR-id:
- 주식현재가 호가/예상체결: ``GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn``
  실전/모의 공통 ``FHKST01010200`` (모의투자 지원). 호가창은 ``output1`` 블록.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._wire import optional_decimal, optional_int, required_int
from ...errors import KisError
from .quote import Market

#: 호가 단계 수(KRX 10단계).
_DEPTH = 10


@dataclass(frozen=True, slots=True)
class PriceLevel:
    """호가창 한 단계 -- 가격(KRW)과 그 가격의 잔량(대기 주문 수량, 주식 수)."""

    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderBook:
    """한 종목의 호가창 스냅샷(불변).

    ``bids`` 는 매수호가(가격 높은 순, index 0 = 최우선 매수), ``asks`` 는 매도호가(가격 낮은
    순, index 0 = 최우선 매도)로, **가격이 있는 단계만** 담는다(빈/0 가격 단계 제외). ``as_of``
    는 조회 시각(KST)이다 -- KIS 호가접수시각(``aspr_acpt_hour``)은 ``raw`` 에 있다.
    """

    symbol: str
    market: Market
    bids: tuple[PriceLevel, ...]      # 최우선 매수가 index 0
    asks: tuple[PriceLevel, ...]      # 최우선 매도가 index 0
    total_bid_quantity: int
    total_ask_quantity: int
    as_of: datetime                   # 데이터 유효 시각 = 조회 시각(KST-aware)
    # raw 는 원본 와이어 뷰(출처 보존). 동등성/해시/repr 제외 -- 값 동일성은 호가/잔량으로 정하고,
    # dict 는 unhashable 이라 포함하면 frozen 인데도 hash(book) 가 TypeError 를 낸다.
    raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


def parse_order_book(
    output1: Mapping[str, Any], *, symbol: str, market: Market, as_of: datetime
) -> OrderBook:
    """KIS 호가 응답의 ``output1`` 블록을 :class:`OrderBook` 으로(순수).

    수치 파싱은 fail-closed -- 값이 있는데 파싱 실패면 :class:`~kis_openapi.errors.KisError`.
    빈/0 가격 단계는 주문 없는 단계로 보아 건너뛴다(잔량은 그 단계가 실재할 때만 필수).
    총잔량은 비어 있으면 0으로 본다(호가창이 빈 정지/동시호가 상태와 대칭 -- 단계가 다 비면
    총잔량도 0). 값이 있는데 파싱 실패면 여전히 예외다.
    """
    return OrderBook(
        symbol=symbol,
        market=market,
        bids=_price_levels(output1, "bidp", "bidp_rsqn"),
        asks=_price_levels(output1, "askp", "askp_rsqn"),
        total_bid_quantity=optional_int(output1.get("total_bidp_rsqn"), "total_bidp_rsqn") or 0,
        total_ask_quantity=optional_int(output1.get("total_askp_rsqn"), "total_askp_rsqn") or 0,
        as_of=as_of,
        raw=output1,
    )


def _price_levels(
    output1: Mapping[str, Any], price_key: str, quantity_key: str
) -> tuple[PriceLevel, ...]:
    """``{price_key}1..10`` / ``{quantity_key}1..10`` 를 실재 단계만 :class:`PriceLevel` 로.

    KIS는 최우선 호가를 1번으로 채우므로 1->10 순서가 곧 최우선->차선 순서다. 가격이 비거나
    0인 단계(주문 없음)는 건너뛴다. 가격이 음수면 손상 데이터이므로 조용히 넘기지 않고 예외로
    fail-closed 한다(주가는 음수일 수 없다).
    """
    levels: list[PriceLevel] = []
    for step in range(1, _DEPTH + 1):
        price = optional_decimal(output1.get(f"{price_key}{step}"), f"{price_key}{step}")
        if price is None or price == 0:  # 주문 없는 단계 -- 건너뜀
            continue
        if price < 0:
            raise KisError(f"호가 단계 {price_key}{step} 의 가격이 음수다: {price}")
        quantity = required_int(output1.get(f"{quantity_key}{step}"), f"{quantity_key}{step}")
        levels.append(PriceLevel(price=price, quantity=quantity))
    return tuple(levels)

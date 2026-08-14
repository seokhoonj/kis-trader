"""KIS 호가창 심도(depth) 파서 -- 시장 중립.

응답 output 블록의 단계별 가격/잔량 키(예: ``bidp1``/``bidp_rsqn1`` ..)를 최우선->차선
순서의 :class:`PriceLevel` 사다리로 만든다. 종목 10단계, 채권/파생 5단계처럼 시장마다 단계
수가 달라도 빈/0 가격 단계를 건너뛰어 실재 단계만 담고, 음수 가격은 손상이라 fail-closed.
시장별 엔진이 공통으로 import 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._internal._wire import optional_decimal, required_int
from .errors import KISError
from .order_book import PriceLevel

#: 호가 사다리를 훑을 최대 단계(종목 10단계; 채권/파생 5단계는 빈 단계로 건너뛴다).
_MAX_DEPTH_STEPS = 10


def _price_levels(
    output: Mapping[str, Any], *, price_key: str, quantity_key: str
) -> tuple[PriceLevel, ...]:
    """실재 단계만 최우선->차선 순서로. 빈/0 가격은 건너뛰고, 음수 가격은 손상이라 fail-closed."""
    levels: list[PriceLevel] = []
    for step in range(1, _MAX_DEPTH_STEPS + 1):
        price = optional_decimal(output.get(f"{price_key}{step}"), f"{price_key}{step}")
        if price is None or price == 0:
            continue
        if price < 0:
            raise KISError(f"호가 단계 {price_key}{step} 의 가격이 음수다: {price}")
        quantity = required_int(output.get(f"{quantity_key}{step}"), f"{quantity_key}{step}")
        levels.append(PriceLevel(price=price, quantity=quantity))
    return tuple(levels)

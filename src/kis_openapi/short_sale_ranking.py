"""공매도 순위 항목(DATA) -- :class:`ShortSaleRanking`.

:meth:`~kis_openapi.ranking.RankingQueries.by_short_sale` 이 돌려주는 낱개 항목이다. 시세(현재가·
전일대비·거래량)에 더해 공매도 지표(체결수량·거래량 비중·거래대금·거래대금 비중·평균가)를 담는다.
KIS 공매도 상위 엔드포인트는 순위 필드를 주지 않으므로 ``rank`` 는 응답 순서(1부터)로 매긴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ShortSaleRanking:
    """공매도 순위 한 항목(불변). ``rank`` 는 응답 순서(KIS가 공매도 규모순으로 내려준다).

    ``change`` / ``change_percent`` 는 하락이면 음수. 비중(``*_ratio``)은 % 단위.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal                    # 현재가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    short_volume: int                 # 공매도 체결 수량
    short_volume_ratio: Decimal       # 공매도 거래량 비중(%)
    short_value: Decimal              # 공매도 거래대금
    short_value_ratio: Decimal        # 공매도 거래대금 비중(%)
    average_price: Decimal            # 공매도 평균가격
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

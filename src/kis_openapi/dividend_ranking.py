"""배당률 순위 항목(DATA) -- :class:`DividendRanking`.

:meth:`~kis_openapi.ranking.RankingQueries.by_dividend` 이 돌려주는 낱개 항목이다.
:class:`~kis_openapi.ranked_stock.RankedStock` 과 달리 가격/등락/거래량이 아니라 배당 정보
(기준일·주당배당금·배당률·배당종류)만 담는다 -- KIS 배당률 상위 엔드포인트가 시세를 주지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DividendRanking:
    """배당률 순위 한 항목(불변).

    ``dividend_rate`` 는 주당배당금/액면가(%)인 **배당률**이다 -- 시장가 기준 배당수익률(dividend
    yield)이 아니다. ``dividend_kind`` 는 현금/주식 배당 구분, ``record_date`` 는 배당 기준일.
    """

    rank: int
    symbol: str
    name: str
    record_date: date                 # 배당 기준일
    dividend_per_share: Decimal       # 주당배당금(현금 또는 주식)
    dividend_rate: Decimal            # 배당률(%), 액면가 기준
    dividend_kind: str                # 배당종류(현금/주식)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

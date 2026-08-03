"""순위 항목(DATA) -- :class:`RankedStock`.

시장 전체 순위 조회(:class:`~kis_openapi.ranking.RankingQueries`)가 돌려주는 낱개 항목이다.
어떤 순위(등락률/거래량/시가총액 등)든 공통으로 쓰는 필드만 담고, 그 순위 고유의 지표
(예: 시가총액)는 ``_raw`` 로 접근한다 -- 22종 순위를 하나의 타입으로 통일하기 위함이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class RankedStock:
    """순위 한 항목(불변). ``rank`` 는 1부터의 순위, 나머지는 그 종목의 현재가·전일대비·거래량.

    ``change`` / ``change_percent`` 는 하락이면 음수. 순위별 고유 지표(시가총액 등)는 ``_raw``.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

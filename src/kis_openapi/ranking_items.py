"""시장 전체 순위 항목(DATA) -- 한 행(row)을 나타내는 불변 데이터 타입 모음.

:class:`~kis_openapi.ranking.RankingQueries` (``kis.ranking.*``)가 돌려주는 순위 결과의 낱개
항목들이다. 대부분의 순위는 공통 코어(순위·종목·시세·거래량)를 공유하므로 :class:`RankedStock`
하나로 통일하고, 그 순위 고유의 지표는 ``_raw`` 로 접근한다. 코어가 맞지 않는 순위(시세가 없는
배당률, 공매도 지표가 본질인 공매도)는 전용 타입을 둔다.

네이밍 규칙(새 순위 항목을 추가할 때 따를 것): 여러 순위가 재사용하는 **범용** 행은 담고 있는
payload 로 이름 짓는다(:class:`RankedStock` = 순위가 매겨진 종목 시세). 특정 endpoint **전용**
행은 그 순위 이름을 따 ``<도메인>Ranking`` 으로 짓는다(:class:`DividendRanking`,
:class:`ShortSaleRanking`). 범용 재사용성과 전용성을 이름에서 구분하기 위함이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


def _empty_raw() -> Mapping[str, Any]:
    return MappingProxyType({})


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
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class DividendRanking:
    """배당률 순위 한 항목(불변).

    ``dividend_rate`` 는 주당배당금/액면가(%)인 **배당률**이다 -- 시장가 기준 배당수익률(dividend
    yield)이 아니다. ``dividend_kind`` 는 현금/주식 배당 구분, ``record_date`` 는 배당 기준일.
    배당률 상위 엔드포인트는 시세를 주지 않아 :class:`RankedStock` 의 가격/등락 코어를 쓰지 못한다.
    """

    rank: int
    symbol: str
    name: str
    record_date: date                 # 배당 기준일
    dividend_per_share: Decimal       # 주당배당금(현금 또는 주식)
    dividend_rate: Decimal            # 배당률(%), 액면가 기준
    dividend_kind: str                # 배당종류(현금/주식)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ShortSaleRanking:
    """공매도 순위 한 항목(불변). ``rank`` 는 응답 순서(KIS가 공매도 규모순으로 내려준다).

    시세(현재가·전일대비·거래량)에 더해 공매도 지표(체결수량·거래량 비중·거래대금·거래대금 비중·
    평균가)를 담는다. ``change`` / ``change_percent`` 는 하락이면 음수, 비중(``*_ratio``)은 % 단위.
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
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

"""해외주식 순위 항목(DATA) -- :class:`OverseasRankedStock` + endpoint 전용 타입.

해외주식 시장 순위(거래량/등락률/시가총액 등)의 한 행이다. 거래소(``exchange``)와 통화 없는 원가격을 담는
공통 코어가 범용 :class:`OverseasRankedStock` 이고(국내 :class:`~kis_trader.domestic.entities.ranking.
DomesticRankedStock` 의 해외 판; 해외 시세는 거래소별 통화라 금액은 거래소 통화 기준), 고유 지표가 있는 순위는
전용 타입(:class:`TurnoverRanking` 등)으로 그 값을 필드로 노출한다. :class:`~kis_trader.overseas.ranking.
OverseasRankingQueries` (``kis.overseas.ranking``)가 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class OverseasRankedStock:
    """해외주식 순위의 한 행(불변).

    ``rank`` 는 KIS 가 준 순위, ``exchange`` 는 거래소코드(NAS/NYS/HKS/...). ``current_price`` 는 현재가,
    ``change`` / ``change_percent`` 는 전일대비(하락이면 음수), ``volume`` / ``trading_amount`` 는 거래량/
    거래대금(거래소 통화). 순위 종류마다 다른 지표는 ``_raw`` 에 있다.
    """

    rank: int
    exchange: str                     # 거래소코드(excd)
    symbol: str                       # 종목코드(symb)
    name: str                         # 종목명(name)
    english_name: str                 # 영문 종목명(ename)
    current_price: Decimal            # 현재가(last)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 거래량(tvol)
    trading_amount: Decimal | None    # 거래대금(tamt); 일부 행은 비어 올 수 있어 None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class TurnoverRanking:
    """해외주식 거래 회전율 순위의 한 행(불변). ``rank`` 는 KIS 가 준 순위.

    :class:`OverseasRankedStock` 의 공통 시세 필드(거래소/코드/현재가/전일대비/거래량)에 더해 회전율
    지표(``turnover_ratio`` 거래회전율 %, ``shares_outstanding`` 상장주식수)를 담는다. 이 순위 응답에는
    거래대금(``tamt``)이 없어 ``trading_amount`` 는 항상 None. ``change`` / ``change_percent`` 는 하락이면
    음수. :class:`~kis_trader.overseas.ranking.OverseasRankingQueries` (``kis.overseas.ranking``)가 돌려준다.
    """

    rank: int
    exchange: str                     # 거래소코드(excd)
    symbol: str                       # 종목코드(symb)
    name: str                         # 종목명(name)
    english_name: str                 # 영문 종목명(ename)
    current_price: Decimal            # 현재가(last)
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 거래량(tvol)
    trading_amount: Decimal | None    # 거래대금(tamt); 이 순위엔 없어 항상 None
    turnover_ratio: Decimal           # 거래회전율 %(tover)
    shares_outstanding: int           # 상장주식수(shar)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

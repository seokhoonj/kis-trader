"""해외주식 순위 항목(DATA) -- :class:`RankedOverseasStock`.

해외주식 시장 순위(거래량/등락률/시가총액 등)의 한 행이다. 국내 :class:`~kis_trader.ranking_items.
RankedStock` 의 해외 판으로, 거래소(``exchange``)와 통화 없는 원가격을 담는다(해외 시세는 거래소별
통화라 금액은 거래소 통화 기준). :class:`~kis_trader.overseas_ranking.OverseasRankingQueries`
(``kis.overseas.ranking``)가 돌려준다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class RankedOverseasStock:
    """해외주식 순위의 한 행(불변).

    ``rank`` 는 KIS 가 준 순위, ``exchange`` 는 거래소코드(NAS/NYS/HKS/...). ``current_price`` 는 현재가,
    ``change`` / ``change_percent`` 는 전일대비(하락이면 음수), ``volume`` / ``amount`` 는 거래량/
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
    amount: Decimal                   # 거래대금(tamt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

"""뉴스 헤드라인 -- :class:`NewsHeadline`.

국내 시장 뉴스(:meth:`~kis_trader.domestic.market.MarketQueries.news`)가 돌려주는 결과
타입이다. 해외 속보는 자체 :class:`~kis_trader.overseas.entities.news.OverseasNewsHeadline`
를 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class NewsHeadline:
    """한 건의 시황/공시 뉴스(불변).

    ``title`` 은 제목, ``source`` 는 출처 매체, ``category`` 는 분류코드, ``symbols`` 는 그 뉴스에
    연관된 종목코드들(없으면 빈 튜플)이다. 본문은 제공하지 않는다(제목 피드). ``timestamp`` 는
    게시 시각(KST-aware).
    """

    serial: str                       # 일련번호(cntt_usiq_srno)
    timestamp: datetime               # 게시 시각(KST-aware)
    title: str                        # 제목(hts_pbnt_titl_cntt)
    source: str                       # 출처 매체(dorg)
    category: str                     # 분류코드(news_lrdv_code)
    symbols: tuple[str, ...]          # 연관 종목코드(iscd1~10 중 비어있지 않은 것)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

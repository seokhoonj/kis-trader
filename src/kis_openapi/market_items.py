"""시장 전체 분석 DATA -- :class:`MarketInvestorFlow`.

종목이 아니라 **시장(코스피/코스닥) 전체**를 대상으로 한 분석 결과다. :class:`~kis_openapi.market.
MarketQueries`(``kis.market``)가 돌려준다. 종목 단위 투자자매매동향은 종목 핸들
(:meth:`~kis_openapi.ticker.Ticker.investor_flows`)에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketInvestorFlow:
    """하루의 시장 전체 투자자 순매수(불변).

    한 시장(코스피/코스닥)의 그날 지수와 주체별 순매수를 담는다. ``foreign_net`` /
    ``individual_net`` / ``institutional_net`` 은 외국인/개인/기관계 순매수 수량(pre-signed;
    음수면 순매도)이다. 증권/투신/사모/은행/보험/종금/기금/기타 세부 주체는 ``_raw`` 에 있다.
    ``index_value`` 는 그날 업종(시장)지수, ``timestamp`` 는 영업일(KST-aware).
    """

    market: str                       # "KOSPI" / "KOSDAQ"
    timestamp: datetime               # 영업일(KST-aware)
    index_value: Decimal              # 시장(업종)지수(bstp_nmix_prpr)
    index_change: Decimal             # 지수 전일대비(부호 포함)
    index_change_percent: Decimal     # 지수 전일대비율(부호 포함)
    foreign_net: int                  # 외국인 순매수 수량(frgn_ntby_qty)
    individual_net: int               # 개인 순매수 수량(prsn_ntby_qty)
    institutional_net: int            # 기관계 순매수 수량(orgn_ntby_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ProgramTradeSummary:
    """하루의 시장 전체 프로그램매매 종합(불변).

    프로그램매매를 차익(arbitrage; 현물-선물 가격차 노린 바스켓)과 비차익(non-arbitrage; 단순
    바스켓)으로 나눠, 각각의 순매수 수량/금액을 담는다. 순매수는 pre-signed(음수면 순매도).
    ``total_net_volume`` 은 둘의 합. 위탁/자기 세부와 매수/매도 원자료는 ``_raw`` 에 있다.
    ``timestamp`` 는 영업일(KST-aware).
    """

    market: str                       # "KOSPI" / "KOSDAQ"
    timestamp: datetime               # 영업일(KST-aware)
    arbitrage_net_volume: int         # 차익 합계 순매수 수량(arbt_smtm_ntby_qty; KIS 필드 오탈자 smtm)
    arbitrage_net_amount: Decimal     # 차익 합계 순매수 금액(arbt_smtn_ntby_tr_pbmn)
    nonarb_net_volume: int            # 비차익 합계 순매수 수량(nabt_smtn_ntby_qty)
    nonarb_net_amount: Decimal        # 비차익 합계 순매수 금액(nabt_smtn_ntby_tr_pbmn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

    @property
    def total_net_volume(self) -> int:
        """전체 프로그램 순매수 수량(차익 + 비차익)."""
        return self.arbitrage_net_volume + self.nonarb_net_volume

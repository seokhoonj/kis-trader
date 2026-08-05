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
from typing import Any, Literal

#: 시장 -- 시장 전체 분석이 대상으로 삼는 시장(코스피/코스닥).
Market = Literal["KOSPI", "KOSDAQ"]


@dataclass(frozen=True, slots=True)
class MarketInvestorFlow:
    """하루의 시장 전체 투자자 순매수(불변).

    한 시장(코스피/코스닥)의 그날 지수와 주체별 순매수를 담는다. ``foreign_net`` /
    ``individual_net`` / ``institutional_net`` 은 외국인/개인/기관계 순매수 수량(pre-signed;
    음수면 순매도)이다. 증권/투신/사모/은행/보험/종금/기금/기타 세부 주체는 ``_raw`` 에 있다.
    ``index_value`` 는 그날 업종(시장)지수, ``timestamp`` 는 영업일(KST-aware).
    """

    market: Market                    # 코스피/코스닥
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

    market: Market                    # 코스피/코스닥
    timestamp: datetime               # 영업일(KST-aware)
    arbitrage_net_volume: int         # 차익 합계 순매수 수량(arbt_smtn_ntby_qty)
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


@dataclass(frozen=True, slots=True)
class VIEvent:
    """한 종목의 VI(변동성완화장치) 발동 이벤트(불변).

    VI 는 단기 급변동 시 2분간 단일가로 전환해 과열을 식히는 장치다. ``triggered_at`` 은 발동 시각,
    ``released_at`` 은 해제 시각(아직 해제 전이면 ``None``). ``trigger_price`` 는 발동가, ``base_price``
    는 기준가, ``disparity_percent`` 는 기준가 대비 괴리율(%), ``count`` 는 그날 그 종목의 누적 발동
    횟수. ``vi_class`` 는 정적/동적 구분코드(vi_cls_code), ``vi_kind`` 는 발동 종류코드(vi_kind_code).
    시각들은 KST-aware. 시장 전체를 대상으로 하므로 ``kis.market.vi_events`` 가 돌려준다.
    """

    symbol: str
    name: str
    triggered_at: datetime            # 발동 시각(KST-aware)
    released_at: datetime | None      # 해제 시각(미해제면 None; KST-aware)
    vi_class: str                     # 정적/동적 구분(vi_cls_code)
    vi_kind: str                      # 발동 종류(vi_kind_code)
    trigger_price: Decimal            # 발동가(vi_prc)
    base_price: Decimal | None        # 기준가(vi_stnd_prc)
    disparity_percent: Decimal | None  # 기준가 대비 괴리율 %(vi_dprt)
    count: int                        # 당일 누적 발동 횟수(vi_count)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class LimitStock:
    """상한가/하한가에 도달한 한 종목(불변).

    ``price`` 가 ``upper_limit`` 와 같으면 상한가, ``lower_limit`` 와 같으면 하한가에 걸린 것이다.
    ``total_ask_quantity`` / ``total_bid_quantity`` 는 총 매도/매수 호가잔량(상한가면 매수잔량이,
    하한가면 매도잔량이 크게 쌓인다). 시장 전체 스냅샷이라 ``kis.market.limit_stocks`` 가 돌려준다.
    """

    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    upper_limit: Decimal              # 상한가(stck_mxpr)
    lower_limit: Decimal              # 하한가(stck_llam)
    total_ask_quantity: int           # 총 매도호가잔량(total_askp_rsqn)
    total_bid_quantity: int           # 총 매수호가잔량(total_bidp_rsqn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

    @property
    def at_upper_limit(self) -> bool:
        """상한가에 걸렸는지(현재가 == 상한가)."""
        return self.price == self.upper_limit


@dataclass(frozen=True, slots=True)
class ProgramFlowPoint:
    """당일 한 시각의 프로그램매매 순매수 대금(불변).

    :class:`ProgramTradeSummary`(일별 종합)의 당일 시간판이다. ``arbitrage_net_amount`` /
    ``nonarb_net_amount`` 는 차익/비차익 순매수 금액, ``total_net_amount`` 는 전체 순매수 금액
    (모두 pre-signed; 음수면 순매도). 매수/매도 원자료·비율은 ``_raw``. ``timestamp`` 는 조회일
    날짜를 붙인 시각(KST-aware).
    """

    market: str
    timestamp: datetime               # 시각(조회일 날짜; KST)
    arbitrage_net_amount: Decimal     # 차익 순매수 대금(arbt_smtn_ntby_tr_pbmn)
    nonarb_net_amount: Decimal        # 비차익 순매수 대금(nabt_smtn_ntby_tr_pbmn)
    total_net_amount: Decimal         # 전체 순매수 대금(whol_smtn_ntby_tr_pbmn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

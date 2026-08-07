"""시장 전체 분석 DATA -- :class:`MarketInvestorFlow`.

종목이 아니라 **시장(코스피/코스닥) 전체**를 대상으로 한 분석 결과다. :class:`~kis_openapi.market.
MarketQueries`(``kis.market``)가 돌려준다. 종목 단위 투자자매매동향은 종목 핸들
(:meth:`~kis_openapi.ticker.Ticker.investor_flows`)에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from .investor import InvestorActivity, InvestorNetActivity
from .program import ProgramTradeActivity

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
class MarketInvestorSnapshot:
    """한 시장·업종의 조회 시점 세부 투자자 매매 총량(불변).

    ``participants`` 는 외국인·개인·기관계 및 기관 세부 주체를 매수·매도·순매수
    수량과 대금으로 매핑한다. 대금 필드는 KIS 원장 단위인 백만원이다.
    """

    market_code: str
    industry_code: str
    participants: Mapping[str, InvestorActivity]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "participants", MappingProxyType(dict(self.participants)))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class InvestorNetBuyStock:
    """기관·외국인 등 투자자 순매수 기준으로 집계된 종목."""

    symbol: str
    name: str
    net_buy_quantity: int
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    participants: Mapping[str, InvestorNetActivity]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "participants", MappingProxyType(dict(self.participants)))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ProgramInvestorTrade:
    """당일 한 투자자 구분의 전체·차익·비차익 프로그램매매."""

    investor_code: str
    investor_name: str
    total: ProgramTradeActivity
    arbitrage: ProgramTradeActivity
    nonarbitrage: ProgramTradeActivity
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


@dataclass(frozen=True, slots=True)
class TradingDay:
    """거래 캘린더의 하루(불변).

    ``date`` 기준으로 그날이 영업일/거래일/개장일/결제일인지 알려준다. ``is_open`` 이 거래소 개장
    여부(휴장일이면 False), ``is_settlement_day`` 는 결제일 여부다. :meth:`~kis_openapi.market.
    MarketQueries.trading_calendar` 가 기준일에서 앞으로 한 페이지를 돌려준다. ``date`` 는 KST-aware.
    """

    date: datetime                    # 기준일자(bass_dt; KST-aware)
    weekday: str                      # 요일구분코드(wday_dvsn_cd)
    is_business_day: bool             # 영업일 여부(bzdy_yn)
    is_trading_day: bool              # 거래일 여부(tr_day_yn)
    is_open: bool                     # 개장일 여부(opnd_yn; 휴장이면 False)
    is_settlement_day: bool           # 결제일 여부(sttl_day_yn)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class FuturesMarketSchedule:
    """국내선물의 기준 영업일과 당일 장 운영 시각(불변)."""

    business_days: tuple[date, ...]
    today: date
    current_time: datetime
    opens_at: datetime
    closes_at: datetime
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "business_days", tuple(self.business_days))
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class NewsItem:
    """한 건의 시황/공시 뉴스(불변).

    ``title`` 은 제목, ``source`` 는 출처 매체, ``category`` 는 분류코드, ``symbols`` 는 그 뉴스에
    연관된 종목코드들(없으면 빈 튜플)이다. 본문은 제공하지 않는다(제목 피드). :meth:`~kis_openapi.
    market.MarketQueries.news` 가 돌려준다. ``timestamp`` 는 게시 시각(KST-aware).
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
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ForeignBrokerFlow:
    """외국계 창구 매매 가집계의 한 종목(불변).

    외국계 증권사 창구를 통한 그날 추정 매매다(확정 아닌 가집계). ``estimated_net`` 은 추정 순매수
    수량(매수-매도; pre-signed, 음수면 순매도), ``estimated_buy`` / ``estimated_sell`` 은 추정 매수/
    매도 수량. ``rank`` 는 응답 순서 기반이다. 시장 전체 집계라 ``kis.market.foreign_broker_trades``
    가 돌려준다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int
    estimated_net: int                # 외국계 추정 순매수(glob_ntsl_qty; 매수-매도)
    estimated_buy: int                # 외국계 추정 매수(glob_total_shnu_qty)
    estimated_sell: int               # 외국계 추정 매도(glob_total_seln_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class InterestRateQuote:
    """국내·해외 금리 지표 또는 채권지수의 최근 값(불변).

    ``value`` 는 항목에 따라 금리(%) 또는 지수 수준이고, ``change`` / ``change_percent`` 는
    전일대비 부호를 반영한다. ``region`` 은 ``"domestic"`` 또는 ``"overseas"``.
    """

    code: str
    name: str
    region: Literal["domestic", "overseas"]
    value: Decimal
    change: Decimal
    change_percent: Decimal
    date: date
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class LendableStock:
    """회사 대주가 가능한 한 종목의 한도·사용·가능수량(불변)."""

    symbol: str
    name: str
    par_value: Decimal
    previous_close: Decimal
    substitute_value: Decimal
    trading_status: str
    availability: str
    limit_quantity: int
    used_quantity: int
    available_quantity: int
    rights_type: str
    base_date: date
    is_lendable: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class CreditEligibleStock:
    """회사 신용주문 가능 여부와 신용비율을 가진 한 종목(불변)."""

    symbol: str
    name: str
    credit_rate: Decimal
    is_eligible: bool
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class BrokerOpinion:
    """한 증권사가 한 종목에 낸 투자의견과 목표가격(불변)."""

    date: date
    symbol: str
    name: str
    broker: str
    opinion: str
    opinion_code: str
    previous_opinion: str
    previous_opinion_code: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    target_price: Decimal | None
    previous_close: Decimal
    disparity_percent: Decimal | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class MarketFunds:
    """하루의 증시자금 종합 현황(불변).

    코스피 지수와 시가총액, 고객예탁금·신용융자잔고 및 유형별 펀드 잔고를 담는다.
    금액 필드는 원장의 단위를 그대로 유지하며, 제공되지 않은 값은 ``None`` 이다.
    ``index_change`` 와 ``index_change_percent`` 는 전일대비 부호를 반영한 값이다.
    """

    date: date | None
    index_value: Decimal
    index_change: Decimal
    index_change_percent: Decimal
    market_cap: Decimal | None
    customer_deposits: Decimal | None
    customer_deposits_change: Decimal | None
    turnover_rate: Decimal | None
    receivables: Decimal | None
    credit_loan_balance: Decimal | None
    futures_deposits: Decimal | None
    equity_fund: Decimal | None
    mixed_fund: Decimal | None
    bond_fund: Decimal | None
    mmf: Decimal | None
    collateral_loan_balance: Decimal | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

"""per-ticker 시세분석(DATA) -- :class:`CreditBalancePoint` / :class:`ShortSalePoint` 등.

한 종목의 시세분석 결과 한 행이다: 일별 추이(신용잔고/공매도/대차/체결량)나 체결금액대별 매매비중 같은
스냅샷. :class:`~kis_trader.stock.DomesticStock` 의 대응 메서드가 리스트로 돌려준다(추이는 최근->과거).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class CreditBalancePoint:
    """하루의 신용잔고 스냅샷(불변).

    ``margin_loan_*`` 은 융자(신용매수) 잔고, ``stock_loan_*`` 은 대주(신용매도) 잔고다. ``*_shares``
    는 잔고 주수, ``*_amount`` 는 잔고 금액, ``*_ratio`` 는 잔고 비율(%). ``price`` / ``change`` 는
    그날 종목 시세. ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    margin_loan_shares: int           # 융자 잔고 주수(whol_loan_rmnd_stcn)
    margin_loan_amount: Decimal       # 융자 잔고 금액(whol_loan_rmnd_amt)
    margin_loan_ratio: Decimal | None  # 융자 잔고 비율 %(whol_loan_rmnd_rate)
    stock_loan_shares: int            # 대주 잔고 주수(whol_stln_rmnd_stcn)
    stock_loan_amount: Decimal        # 대주 잔고 금액(whol_stln_rmnd_amt)
    stock_loan_ratio: Decimal | None  # 대주 잔고 비율 %(whol_stln_rmnd_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class LoanPoint:
    """하루의 대차거래(주식 대여) 스냅샷(불변).

    ``new_shares`` 는 그날 신규 대차 체결 주수, ``redeemed_shares`` 는 상환 주수, ``balance_shares``
    / ``balance_amount`` 는 대차잔고 주수/금액, ``balance_change`` 는 잔고 전일대비 주수다. 대차잔고는
    공매도 공급 여력의 대리지표로 본다. ``price`` / ``change`` 는 그날 종목 시세, ``timestamp`` 는
    영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    new_shares: int                   # 신규 대차 체결 주수(new_stcn)
    redeemed_shares: int              # 상환 주수(rdmp_stcn)
    balance_shares: int               # 대차잔고 주수(rmnd_stcn)
    balance_amount: Decimal           # 대차잔고 금액(rmnd_amt)
    balance_change: int               # 잔고 전일대비 주수(prdy_rmnd_vrss)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ShortSalePoint:
    """하루의 공매도 스냅샷(불변).

    ``short_volume`` 은 그날 공매도 체결량, ``short_volume_ratio`` 는 거래량 대비 공매도 비중(%),
    ``short_amount`` 는 공매도 대금, ``short_avg_price`` 는 공매도 평균가. ``close`` / ``change`` 는
    그날 종목 종가. ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    close: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int                       # 종목 거래량
    short_volume: int                 # 공매도 체결량(ssts_cntg_qty)
    short_volume_ratio: Decimal | None  # 공매도 비중 %(ssts_vol_rlim)
    short_amount: Decimal             # 공매도 대금(ssts_tr_pbmn)
    short_average_price: Decimal | None  # 공매도 평균가(avrg_prc)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AnalystOpinion:
    """한 시점의 애널리스트 투자의견(불변).

    ``opinion`` 은 투자의견(매수/중립/매도 등 텍스트), ``previous_opinion`` 은 직전 의견,
    ``target_price`` 는 HTS 목표주가, ``disparity_percent`` 는 목표가 대비 괴리율(%)이다.
    :meth:`~kis_trader.stock.DomesticStock.analyst_opinions` 가 기간 시계열로 돌려준다.
    ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    opinion: str                      # 투자의견(invt_opnn)
    previous_opinion: str             # 직전 투자의견(rgbf_invt_opnn)
    target_price: Decimal | None      # HTS 목표주가(hts_goal_prc)
    previous_close: Decimal | None    # 전일 종가(stck_prdy_clpr)
    disparity_percent: Decimal | None  # 목표가 대비 괴리율 %(dprt)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class EarningsEstimate:
    """한 종목의 월간 추정손익·투자지표 스냅샷(불변).

    ``periods`` 와 각 metric tuple은 같은 위치로 대응한다. ``income_statement`` 는 매출·영업이익·
    순이익과 증감률, ``indicators`` 는 EBITDA·EPS·PER·ROE 등 추정 투자지표다.
    """

    symbol: str
    security_name: str
    analyst_name: str
    estimate_date: date
    recommendation: str
    capital: Decimal | None
    foreign_limit_ratio: Decimal | None
    periods: tuple[str, ...]
    income_statement: Mapping[str, tuple[Decimal | None, ...]] = field(hash=False)
    indicators: Mapping[str, tuple[Decimal | None, ...]] = field(hash=False)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "periods", tuple(self.periods))
        object.__setattr__(
            self, "income_statement", MappingProxyType(dict(self.income_statement))
        )
        object.__setattr__(self, "indicators", MappingProxyType(dict(self.indicators)))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class RecentPricePoint:
    """최근 일·주·월 주가와 수급 보조지표 한 점(불변)."""

    symbol: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    volume_ratio: Decimal
    change: Decimal
    change_percent: Decimal
    foreign_exhaustion_ratio: Decimal
    foreign_net_quantity: int
    ex_rights_code: str
    cumulative_split_ratio: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IntradayExecutionSummary:
    """당일 시간대별 체결 조회의 현재 종목 요약(불변)."""

    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    previous_volume: int
    market_name: str
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IntradayExecutionPoint:
    """당일 한 시각의 체결·최우선호가·체결강도(불변)."""

    timestamp: datetime
    price: Decimal
    change: Decimal
    change_percent: Decimal
    ask_price: Decimal
    bid_price: Decimal
    strength: Decimal
    cumulative_volume: int
    quantity: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class IntradayExecutions:
    """현재 종목 요약과 기준시각 이전의 당일 체결 목록(불변)."""

    symbol: str
    summary: IntradayExecutionSummary
    points: tuple[IntradayExecutionPoint, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class TradeAmountBand:
    """한 체결금액대의 매매비중(불변).

    당일 체결을 체결금액대(``band_label`` 예: "3백 이하")로 묶어 매수/매도/순매수를 나눈다.
    ``*_ratio`` 는 전체 대비 거래량 비율(%), ``*_count`` 는 체결 건수, ``net_buy_*`` 는 순매수(음수 가능).
    :meth:`~kis_trader.stock.DomesticStock.trade_amount_bands` 가 금액대 리스트로 돌려준다.
    """

    symbol: str
    band_label: str                   # 가격(금액)대명(prpr_name)
    average_price: Decimal            # 금액대 평균가격(smtn_avrg_prpr)
    volume: int                       # 금액대 합계 거래량(acml_vol)
    net_buy_ratio: Decimal | None     # 합계 순매수비율 %(whol_ntby_qty_rate; 음수 가능)
    net_buy_count: int                # 합계 순매수건수(ntby_cntg_csnu; 음수 가능)
    sell_volume: int                  # 매도 거래량(seln_cnqn_smtn)
    sell_volume_ratio: Decimal | None  # 매도 거래량비율 %(whol_seln_vol_rate)
    sell_count: int                   # 매도 건수(seln_cntg_csnu)
    buy_volume: int                   # 매수 거래량(shnu_cnqn_smtn)
    buy_volume_ratio: Decimal | None  # 매수 거래량비율 %(whol_shun_vol_rate; KIS 명세 오타 shun)
    buy_count: int                    # 매수 건수(shnu_cntg_csnu)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedPricePoint:
    """한 시점의 예상 체결가(불변).

    장 시작 전/마감 동시호가 구간에 형성되는 예상 체결가 시계열의 한 점이다. ``expected_price`` 는
    그 시각의 예상 체결가, ``change`` / ``change_percent`` 는 전일 종가 대비(하락이면 음수), ``volume``
    은 누적 예상 거래량이다. :meth:`~kis_trader.stock.DomesticStock.expected_price_trend` 가 시각 리스트
    (최근->과거)로 돌려준다. ``timestamp`` 는 체결시각(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 체결시각(KST-aware, 일자+시각)
    expected_price: Decimal           # 예상 체결가(stck_prpr)
    change: Decimal                   # 전일 대비(부호 포함)
    change_percent: Decimal           # 전일 대비율(부호 포함)
    volume: int                       # 누적 거래량(acml_vol)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DailyTradeVolumePoint:
    """하루의 매수/매도 체결량 합계(불변).

    ``buy_volume`` 은 그날 총 매수 체결량, ``sell_volume`` 은 총 매도 체결량이다.
    :meth:`~kis_trader.stock.DomesticStock.daily_trade_volume` 이 일자 시계열(최근->과거)로 돌려준다.
    ``timestamp`` 는 영업일(KST-aware).
    """

    symbol: str
    timestamp: datetime               # 영업일(KST-aware)
    buy_volume: int                   # 총 매수 수량(total_shnu_qty)
    sell_volume: int                  # 총 매도 수량(total_seln_qty)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ForeignNetBuyPoint:
    """한 시점의 장중 외국계(외국인 회원사) 순매수 스냅샷(불변).

    ``foreign_net_buy`` 는 누적 외국계 순매수 수량, ``foreign_net_buy_change`` 는 해당 시간대의
    순매수 증감이다. ``timestamp`` 는 당일 체결시각(KST-aware)이며 응답 순서대로 제공된다.
    """

    symbol: str
    timestamp: datetime
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    foreign_sell_volume: int
    foreign_buy_volume: int
    foreign_net_buy: int
    foreign_net_buy_change: int
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class VolumeAtPrice:
    """한 가격대의 체결량과 전체 누적거래량 대비 비중(불변)."""

    rank: int
    price: Decimal
    volume: int
    volume_share_percent: Decimal
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """종목의 가격대별 거래량 분포와 현재 시세 요약(불변).

    ``bands`` 는 KIS가 제공한 순서를 유지하며, 각 가격대의 체결량과 누적거래량 대비 비중(%)을
    담는다. ``weighted_average_price`` 는 가중평균가, ``listed_shares`` 는 상장주수다.
    """

    symbol: str
    market: str
    name: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    volume: int
    weighted_average_price: Decimal
    listed_shares: int
    bands: tuple[VolumeAtPrice, ...]
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "bands", tuple(self.bands))
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

"""시장 전체 순위 항목(DATA) -- 한 행(row)을 나타내는 불변 데이터 타입 모음.

:class:`~kis_trader.domestic.ranking.DomesticRankingQueries` (``kis.domestic.ranking.*``)가 돌려주는 순위 결과의 낱개
항목들이다. 대부분의 순위는 그 endpoint 고유의 지표(거래량 계열·시가총액·이격도·체결강도 등)를 전용 타입의
필드로 노출한다(:class:`TradingActivityRanking` · :class:`MarketCapRanking` · :class:`DisparityRanking` 등).
순위 고유 지표가 없고 코어(순위·종목·시세·거래량)가 곧 payload 인 등락률 순위만 범용 :class:`DomesticRankedStock`
를 돌려준다. 어느 타입이든 명명하지 않은 원본 값은 ``_raw`` 로 접근한다.

네이밍 규칙(새 순위 항목을 추가할 때 따를 것): 여러 순위가 재사용하는 **범용** 행은 담고 있는
payload 로 이름 짓는다(:class:`DomesticRankedStock` = 순위가 매겨진 종목 시세). 특정 endpoint **전용**
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

from ..._internal._freeze import freeze_vendor_payload
from ...instrument import DomesticMarket


def _empty_raw() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class DomesticRankedStock:
    """순위 한 항목(불변). ``rank`` 는 1부터의 순위, 나머지는 그 종목의 현재가·전일대비·거래량.

    ``change`` / ``change_percent`` 는 하락이면 음수. 순위별 고유 지표(시가총액 등)는 ``_raw``.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    #: 누적 거래대금(원) -- 거래량 순위 응답에만 있어(그 외 순위는 None). 거래대금 기준 정렬의 근거값.
    cumulative_trading_amount: Decimal | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DividendRanking:
    """배당률 순위 한 항목(불변).

    ``dividend_rate`` 는 주당배당금/액면가(%)인 **배당률**이다 -- 시장가 기준 배당수익률(dividend
    yield)이 아니다. ``dividend_kind`` 는 현금/주식 배당 구분, ``record_date`` 는 배당 기준일.
    배당률 상위 엔드포인트는 시세를 주지 않아 :class:`DomesticRankedStock` 의 가격/등락 코어를 쓰지 못한다.
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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class CreditBalanceRanking:
    """신용잔고 순위 한 항목(불변). ``rank`` 는 응답 순서.

    ``margin_loan_*`` 은 그 종목을 **신용융자(빚내서 매수)**로 보유 중인 잔고, ``stock_loan_*`` 은
    **대주(주식 빌려 매도)** 잔고이다(각각 주수/금액/비율). 비율(``*_ratio``)은 % 단위. ``change`` /
    ``change_percent`` 는 하락이면 음수. N일 대비 증가율 등은 ``_raw``.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    margin_loan_shares: int           # 융자 잔고 주수
    margin_loan_amount: Decimal       # 융자 잔고 금액
    margin_loan_ratio: Decimal        # 융자 잔고 비율(%)
    stock_loan_shares: int            # 대주 잔고 주수
    stock_loan_amount: Decimal        # 대주 잔고 금액
    stock_loan_ratio: Decimal         # 대주 잔고 비율(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class NearHighLowRanking:
    """신고/신저 근접 순위 한 항목(불변). ``rank`` 는 응답 순서.

    ``new_high`` / ``new_low`` 는 신 최고가 / 최저가, ``high_near_rate`` / ``low_near_rate`` 는 그
    가격에 얼마나 근접했는지의 비율(%). ``change`` / ``change_percent`` 는 하락이면 음수.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    new_high: Decimal                 # 신 최고가
    high_near_rate: Decimal           # 고가 근접 비율(%)
    new_low: Decimal                  # 신 최저가
    low_near_rate: Decimal            # 저가 근접 비율(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class TradingActivityRanking:
    """거래량 계열 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    평균거래량/증가율/회전율 등 거래활동 지표를 담는다. ``*_percent`` 는 변화율(%),
    ``*_ratio`` 는 회전율(회전 배수). ``change`` / ``change_percent`` 는 하락이면 음수.
    ``cumulative_trading_amount`` 는 누적 거래대금(원)이며 응답에 없을 수 있다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    average_volume: int               # 평균거래량(주식 수)
    volume_growth_percent: Decimal    # 거래량증가율(%)
    volume_turnover_ratio: Decimal    # 거래량회전율(회전 배수)
    n_day_volume_turnover_ratio: Decimal      # N일 거래량회전율
    average_trading_amount: Decimal   # 평균거래대금(원)
    trading_amount_turnover_ratio: Decimal    # 거래대금회전율
    n_day_trading_amount_turnover_ratio: Decimal  # N일 거래대금회전율
    #: 누적 거래대금(원) -- 거래대금 기준 정렬의 근거값(응답에 없으면 None).
    cumulative_trading_amount: Decimal | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DomesticExecutionStrengthRanking:
    """국내 체결강도 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``execution_strength`` 는 당일 체결강도(매수체결량/매도체결량 x100, 100 초과면 매수 우위),
    ``buy_execution_volume`` / ``sell_execution_volume`` 는 각각 누적 매수/매도 체결량(주식 수).
    ``change`` / ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    execution_strength: Decimal       # 당일 체결강도
    buy_execution_volume: int         # 매수 체결량 합(주식 수)
    sell_execution_volume: int        # 매도 체결량 합(주식 수)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class WatchlistRegistrationRanking:
    """관심종목 등록상위 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``registration_count`` 는 사용자들이 그 종목을 관심종목으로 등록한 건수(많이 등록될수록 상위).
    ``change`` / ``change_percent`` 는 하락이면 음수. ``cumulative_trading_amount`` 는 누적 거래대금(원).
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    registration_count: int           # 관심종목 등록 건수
    #: 누적 거래대금(원) -- 응답에 없으면 None.
    cumulative_trading_amount: Decimal | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class BulkTradeCountRanking:
    """대량체결건수 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``buy_trade_count`` / ``sell_trade_count`` 는 대량 매수/매도 체결 **건수**이고,
    ``net_trade_volume`` 은 순매수 체결 **수량**(주식 수)이다 -- 와이어 키 ``ntby_cnqn`` 의
    ``cnqn``(체결량)은 건수(``csnu``)가 아니라 수량이라 ``_volume`` 으로 이름 짓는다.
    ``change`` / ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    buy_trade_count: int              # 매수 체결 건수
    sell_trade_count: int             # 매도 체결 건수
    net_trade_volume: int             # 순매수 체결 수량(주식 수)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class HouseTradeVolumeRanking:
    """당사매매종목 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``buy_volume`` / ``sell_volume`` / ``net_volume`` 은 각각 당사(증권사 자기) 매수/매도/순매수
    체결 수량(주식 수)이다. ``change`` / ``change_percent`` 는 하락이면 음수.
    ``cumulative_trading_amount`` 는 누적 거래대금(원).
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    buy_volume: int                   # 당사 매수 체결 수량(주식 수)
    sell_volume: int                  # 당사 매도 체결 수량(주식 수)
    net_volume: int                   # 당사 순매수 체결 수량(주식 수)
    #: 누적 거래대금(원) -- 응답에 없으면 None.
    cumulative_trading_amount: Decimal | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class MarketCapRanking:
    """시가총액 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``market_cap`` 는 시가총액(KIS 단위 억원), ``listed_shares`` 는 상장주식수(주식 수),
    ``market_cap_weight_ratio`` 는 전체 시가총액 중 그 종목의 비중(%)이다. ``change`` /
    ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    market_cap: Decimal               # 시가총액(억원)
    listed_shares: int                # 상장주식수(주식 수)
    market_cap_weight_ratio: Decimal  # 전체 시가총액 중 비중(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class DisparityRanking:
    """이격도 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``disparity_5`` / ``_10`` / ``_20`` / ``_60`` / ``_120`` 은 각각 5/10/20/60/120일 이동평균
    대비 이격도(%)이다(현재가/이동평균 x100, 100 초과면 이동평균 위). 정렬 기준 일수와 상관없이
    5개 이격도를 모두 담는다. ``change`` / ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    disparity_5: Decimal              # 5일 이격도(%)
    disparity_10: Decimal             # 10일 이격도(%)
    disparity_20: Decimal             # 20일 이격도(%)
    disparity_60: Decimal             # 60일 이격도(%)
    disparity_120: Decimal            # 120일 이격도(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class QuoteBalanceRanking:
    """호가잔량 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    ``total_ask_residual`` / ``total_bid_residual`` 는 총매도/총매수 호가잔량(주식 수),
    ``sell_residual_ratio`` / ``buy_residual_ratio`` 는 각각 매도/매수 잔량 비율(%)이다.
    ``change`` / ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    total_ask_residual: int           # 총매도 호가잔량(주식 수)
    total_bid_residual: int           # 총매수 호가잔량(주식 수)
    sell_residual_ratio: Decimal      # 매도잔량 비율(%)
    buy_residual_ratio: Decimal       # 매수잔량 비율(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class PreferredShareDisparityRanking:
    """본주/우선주 괴리 순위 한 항목(불변). ``rank`` 는 1부터의 순위.

    코어(``symbol`` / ``price`` / ``change`` / ... )는 **본주** 기준이고, ``preferred_*`` 는 짝이 되는
    **우선주** 쪽 값(코드·이름·현재가·전일대비·거래량)이다. ``disparity_rate`` 는 본주/우선주
    괴리율(%), ``price_difference`` 는 본주-우선주 가격차. ``change`` / ``change_percent`` 및
    ``preferred_change`` / ``preferred_change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str                       # 본주 종목코드
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량(주식 수)
    disparity_rate: Decimal           # 본주/우선주 괴리율(%)
    price_difference: Decimal         # 본주-우선주 가격차
    preferred_symbol: str             # 우선주 종목코드
    preferred_name: str               # 우선주명
    preferred_price: Decimal          # 우선주 현재가
    preferred_change: Decimal         # 우선주 전일대비(부호 포함)
    preferred_change_percent: Decimal  # 우선주 전일대비율(부호 포함)
    preferred_volume: int             # 우선주 누적 거래량(주식 수)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedCloseRanking:
    """장마감 예상체결 순위 한 항목(불변). ``rank`` 는 응답 순서(KIS가 순위 필드를 주지 않아 응답순으로 매긴다).

    ``price`` 는 예상체결가, ``expected_volume`` 은 예상체결량(주식 수)이다. ``base_price_vs_expected`` 는
    기준가(전일 종가 등) 대비 예상체결가의 차이, ``base_price_vs_expected_percent`` 는 그 비율(%)이다.
    ``change`` / ``change_percent`` 는 하락이면 음수. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal                    # 예상체결가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    expected_volume: int              # 예상체결량(주식 수)
    base_price_vs_expected: Decimal   # 기준가 대비 예상체결가
    base_price_vs_expected_percent: Decimal  # 기준가 대비 예상체결가 비율(%)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ExpectedExecutionChangeRanking:
    """장 시작 전 예상체결 등락 순위 한 항목(불변). ``rank`` 는 응답 순서.

    ``price`` 는 예상체결가, ``expected_volume`` 은 예상체결량(주식 수), ``expected_trading_amount`` 는 예상
    거래대금(원), ``base_price`` 는 기준가(전일 종가 등)이다. ``change`` / ``change_percent`` 는 하락이면
    음수. 매도/매수 호가잔량 등 라벨이 모호한 값은 ``_raw`` 에 남겨둔다. 이 순위엔 누적 거래대금이 없다.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal                    # 예상체결가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    expected_volume: int              # 예상체결량(주식 수)
    expected_trading_amount: Decimal  # 예상거래대금(원)
    base_price: Decimal               # 기준가(전일 종가 등)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AfterHoursRanking:
    """시간외 단일가 순위의 한 행(불변).

    정규장이 아니라 **시간외 단일가** 세션 기준이라 ``after_hours_price`` / ``after_hours_change`` /
    ``after_hours_volume`` 이 모두 시간외 값이다(정규장 현재가·누적거래량 등은 ``_raw`` 에 있다). 시간외
    등락률/거래량/예상체결 순위가 공유한다. 순위는 응답 순서 기반이다.
    """

    rank: int
    symbol: str
    name: str
    after_hours_price: Decimal           # 시간외 단일가
    after_hours_change: Decimal          # 시간외 전일대비(부호 포함)
    after_hours_change_percent: Decimal  # 시간외 전일대비율(부호 포함)
    after_hours_volume: int              # 시간외 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AfterHoursBalanceRanking:
    """시간외 잔량 순위의 한 행(불변).

    시간외 매도/매수 총잔량(``after_hours_ask_residual`` / ``after_hours_bid_residual``)과 장전/장후 시간외
    체결량(``pre_market_volume`` / ``post_market_volume``)을 담는다. 이 순위엔 정규장 누적거래량이
    없다(그래서 :class:`DomesticRankedStock` 이 아니라 전용 타입). ``price`` / ``change`` 는 정규장 종가 기준.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    after_hours_ask_residual: int     # 시간외 총 매도잔량
    after_hours_bid_residual: int     # 시간외 총 매수잔량
    pre_market_volume: int            # 장전 시간외 체결량(mkob_otcp_vol)
    post_market_volume: int           # 장후 시간외 체결량(mkfa_otcp_vol)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class TopViewedStock:
    """HTS 조회 상위 종목 한 행(불변).

    사용자들이 많이 조회한(관심을 받은) 종목 순위로, 코드와 시장구분(``market`` J:KOSPI/Q:KOSDAQ)만
    준다 -- 시세는 없으니 관심 종목은 종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)로 따로 조회한다.
    순위는 응답 순서 기반이다.
    """

    rank: int
    symbol: str
    market: DomesticMarket                    # 시장구분(J->KOSPI / Q->KOSDAQ)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

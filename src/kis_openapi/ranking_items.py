"""시장 전체 순위 항목(DATA) -- 한 행(row)을 나타내는 불변 데이터 타입 모음.

:class:`~kis_openapi.ranking.RankingQueries` (``kis.domestic.ranking.*``)가 돌려주는 순위 결과의 낱개
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

from ._freeze import freeze_vendor_payload


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
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


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
class OvertimeRanking:
    """시간외 단일가 순위의 한 행(불변).

    정규장이 아니라 **시간외 단일가** 세션 기준이라 ``overtime_price`` / ``overtime_change`` /
    ``overtime_volume`` 이 모두 시간외 값이다(정규장 현재가·누적거래량 등은 ``_raw`` 에 있다). 시간외
    등락률/거래량/예상체결 순위가 공유한다. 순위는 응답 순서 기반이다.
    """

    rank: int
    symbol: str
    name: str
    overtime_price: Decimal           # 시간외 단일가
    overtime_change: Decimal          # 시간외 전일대비(부호 포함)
    overtime_change_percent: Decimal  # 시간외 전일대비율(부호 포함)
    overtime_volume: int              # 시간외 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class AfterHoursBalanceRanking:
    """시간외 잔량 순위의 한 행(불변).

    시간외 매도/매수 총잔량(``overtime_ask_residual`` / ``overtime_bid_residual``)과 장전/장후 시간외
    체결량(``pre_market_volume`` / ``post_market_volume``)을 담는다. 이 순위엔 정규장 누적거래량이
    없다(그래서 :class:`RankedStock` 이 아니라 전용 타입). ``price`` / ``change`` 는 정규장 종가 기준.
    """

    rank: int
    symbol: str
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    overtime_ask_residual: int        # 시간외 총 매도잔량
    overtime_bid_residual: int        # 시간외 총 매수잔량
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
    준다 -- 시세는 없으니 관심 종목은 종목 핸들(:class:`~kis_openapi.stock.DomesticStock`)로 따로 조회한다.
    순위는 응답 순서 기반이다.
    """

    rank: int
    symbol: str
    market: str                       # 시장구분(J:KOSPI / Q:KOSDAQ)
    _raw: Mapping[str, Any] = field(
        default_factory=_empty_raw, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

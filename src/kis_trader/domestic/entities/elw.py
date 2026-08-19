"""ELW(주식워런트증권) 시세 DATA 타입들.

ELW 는 증권 형태로 상장된 옵션이다(기초자산에 대한 콜/풋 권리를 담은 워런트). 기본 시세
(현재가/호가/체결)는 종목 핸들(:class:`~kis_trader.domestic.stock.DomesticStock`)로 조회하고, 여기 타입들은
ELW 고유의 **옵션 분석 지표** -- 민감도(그릭스), 변동성, 투자지표의 시계열 -- 를 담는다.

:class:`~kis_trader.elw.ELW` 핸들(``kis.domestic.elw(code)``)의 조회 메서드가 돌려준다. 타입이 여럿이라
한 파일에 모은다(ranking_items/index_items 선례).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from ..._internal._freeze import freeze_vendor_payload


@dataclass(frozen=True, slots=True)
class ELWQuote:
    """ELW 현재가 스냅샷(불변) -- ELW-aware 시세.

    종목 :class:`~kis_trader.quote.Quote` 와 달리, ELW 가 옵션인 만큼 **기초자산 가격**(``underlying_
    price``)과 **내재변동성**(``implied_volatility``)·**이론가**(``theoretical_price``)·**괴리율**
    (``disparity_rate`` = 이론가 대비 시장가 괴리)·**행사가**(``strike``)·**머니니스**(``moneyness`` =
    ATM/ITM/OTM)를 함께 담는다. ``change`` / ``change_percent`` 는 전일대비(이 응답엔 ELW 부호
    필드가 따로 없어 값 자체의 부호를 쓴다). 피벗/자본지지점 등 부가 지표는 ``_raw`` 에 있다.
    """

    code: str
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    change: Decimal                   # 전일대비(값 자체 부호)
    change_percent: Decimal           # 전일대비율
    volume: int
    bid: Decimal | None               # 매수호가
    ask: Decimal | None               # 매도호가
    theoretical_price: Decimal | None  # HTS 이론가(모형가)
    disparity_rate: Decimal | None           # 괴리율(dprt)
    implied_volatility: Decimal | None  # HTS 내재변동성(%)
    strike: Decimal | None            # 행사가(acpr)
    moneyness: str                    # ATM/ITM/OTM(atm_cls_name)
    underlying_name: str              # 기초자산명(unas_isnm)
    underlying_price: Decimal         # 기초자산 현재가(unas_prpr)
    as_of: datetime                   # KST-aware
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ELWSensitivityPoint:
    """한 시점의 ELW 민감도(그릭스) 스냅샷(불변).

    옵션 민감도로, ``delta`` 는 기초자산 1 변동당 ELW 이론가 변동, ``gamma`` 는 델타의 변화율,
    ``theta`` 는 시간가치 감소(하루당), ``vega`` 는 변동성 1%p 당 가치 변화, ``rho`` 는 금리
    민감도다. ``theoretical_price`` 는 HTS 이론가(모형가). ``timestamp`` 는 일별 조회면 영업일자,
    체결별 조회면 조회일 날짜를 붙인 체결시각(둘 다 KST-aware).
    """

    code: str
    timestamp: datetime               # 일별=영업일자 / 체결별=체결시각(조회일 날짜); KST-aware
    price: Decimal                    # ELW 현재가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    theoretical_price: Decimal | None  # HTS 이론가(모형가)
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    rho: Decimal | None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ELWVolatilityPoint:
    """한 시점의 ELW 변동성 스냅샷(불변).

    ``implied_volatility`` 는 HTS 내재변동성(%) -- 옵션 가격에 내포된 시장의 변동성 기대치다.
    ``change`` / ``change_percent`` 는 전일대비(일별/체결별에만 있고 분별/틱은 없어 ``None``).
    시간축(체결/일별/분별/틱)마다 벤더가 주는 부가 필드가 달라(일별=역사변동성 곡선 d10~d90 및
    OHLCV, 체결=매수/매도호가, 분별=OHLC) 공통 축만 타입으로 담고 나머지는 ``_raw`` 에 둔다.
    ``timestamp`` 는 KST-aware(일별=영업일자, 그 외=날짜+체결시각 또는 조회일+체결시각).
    """

    code: str
    timestamp: datetime               # KST-aware
    price: Decimal                    # ELW 현재가
    implied_volatility: Decimal | None  # HTS 내재변동성(%)
    change: Decimal | None            # 전일대비(부호 포함; 분별/틱은 None)
    change_percent: Decimal | None    # 전일대비율(부호 포함; 분별/틱은 None)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ELWIndicatorPoint:
    """한 시점의 ELW 투자지표 스냅샷(불변).

    ``leverage`` 는 실효 레버리지(기초자산 1% 변동당 ELW 몇 % 변동), ``gearing`` 은 명목 레버리지
    (기초자산가 / (ELW가 x 전환비율)), ``intrinsic_value`` 는 내재가치(지금 행사 시 가치),
    ``parity`` 는 패리티(기초자산가 / 행사가 x 100; 100 = 등가격). ``change`` / ``change_percent``
    는 전일대비(일별/체결별에만 있고 분별은 없어 ``None``). 시간가치/프리미엄/자본지지점 근접률 등
    축마다 다른 부가 지표는 ``_raw`` 에 둔다. ``timestamp`` 는 KST-aware.
    """

    code: str
    timestamp: datetime               # KST-aware
    price: Decimal                    # ELW 현재가
    leverage: Decimal | None          # 실효 레버리지(lvrg_val)
    gearing: Decimal | None           # 명목 레버리지/기어링(gear)
    intrinsic_value: Decimal | None   # 내재가치(invl_val)
    parity: Decimal | None            # 패리티(prit)
    change: Decimal | None            # 전일대비(부호 포함; 분별은 None)
    change_percent: Decimal | None    # 전일대비율(부호 포함; 분별은 None)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ELWLPFlow:
    """하루의 LP(유동성공급자) 매매 흐름(불변).

    ELW 는 거래가 얇아 발행 증권사의 LP 가 양방향 호가를 대므로, LP 의 매수/매도 물량이 시세를
    좌우한다. ``lp_buy_quantity`` / ``lp_sell_quantity`` 는 그날 LP 의 매수/매도 수량, ``*_avg_price``
    는 각 평균단가, ``lp_holding_quantity`` / ``lp_holding_rate`` 는 LP 의 보유수량/보유비율(%)이다.
    :attr:`net_quantity`(매수-매도)가 양수면 LP 가 순매수(회수), 음수면 순매도(공급)한 날이다.
    ``timestamp`` 는 영업일자(KST-aware).
    """

    code: str
    timestamp: datetime               # 영업일자(KST-aware)
    price: Decimal                    # ELW 현재가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    lp_buy_quantity: int              # LP 매수수량(lp_shnu_qty)
    lp_buy_avg_price: Decimal | None  # LP 매수평균단가(lp_shnu_avrg_unpr)
    lp_sell_quantity: int             # LP 매도수량(lp_seln_qty)
    lp_sell_avg_price: Decimal | None  # LP 매도평균단가(lp_seln_avrg_unpr)
    lp_holding_quantity: int          # LP 보유수량(lp_hvol)
    lp_holding_rate: Decimal | None   # LP 보유비율 %(lp_hldn_rate)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

    @property
    def net_quantity(self) -> int:
        """LP 순매수 수량(매수-매도). 양수면 LP 회수, 음수면 LP 공급."""
        return self.lp_buy_quantity - self.lp_sell_quantity


@dataclass(frozen=True, slots=True)
class ELWUnderlying:
    """ELW 가 상장돼 있는 기초자산 한 종목(불변).

    :meth:`~kis_trader.elw_screener.ELWScreenerQueries.underlyings` 가 돌려주는, ELW 발행의 바탕이
    되는 기초자산(개별주식/지수) 목록의 낱개 행이다. ``symbol`` 은 기초자산 코드(지수면 2001 등,
    주식이면 6자리), ``price`` 는 기초자산 현재가.
    """

    symbol: str                       # 기초자산 코드(unas_shrn_iscd)
    name: str                         # 기초자산명(unas_isnm)
    price: Decimal                    # 기초자산 현재가
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class ELWListing:
    """스크리닝/목록 조회에 잡힌 ELW 한 종목(불변).

    기초자산별 시세·신규상장·만기예정·조건검색·비교종목 등 여러 목록 조회가 공유하는 행이다. 조회
    종류마다 벤더가 주는 필드가 달라(신규상장/비교종목은 시세 없음, 기초자산별/조건검색은 시세 있음)
    ``symbol``/``name`` 외에는 모두 optional 이며, 부가 필드(전환비율·잔존일수·LP 보유 등)는 ``_raw``
    에 둔다. ``strike`` 는 행사가, ``listing_date`` / ``last_trade_date`` 는 상장일/최종거래일.
    """

    symbol: str                       # ELW 코드(elw_shrn_iscd / bond_shrn_iscd)
    name: str                         # ELW 명(elw_kor_isnm / hts_kor_isnm)
    underlying_name: str | None       # 기초자산명(unas_isnm)
    price: Decimal | None             # ELW 현재가(elw_prpr)
    change: Decimal | None            # 전일대비(부호 포함)
    change_percent: Decimal | None    # 전일대비율(부호 포함)
    volume: int | None                # 누적 거래량(acml_vol)
    strike: Decimal | None            # 행사가(acpr)
    listing_date: datetime | None     # 상장일(KST-aware)
    last_trade_date: datetime | None  # 최종거래일(KST-aware)
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))


@dataclass(frozen=True, slots=True)
class RankedELW:
    """시장 전체 ELW 순위의 한 행(불변).

    :class:`~kis_trader.domestic.entities.ranking.RankedStock` 과 대칭인 ELW 판으로, 어떤 기준으로 줄 세운
    ELW 목록의 낱개 행이다. 공통 축(순위/코드/이름/가격/전일대비/거래량)만 담고, 순위 종류마다
    다른 고유 지표(그릭스·레버리지·회전율·호가잔량 등)는 ``_raw`` 에 있다. ``rank`` 는 응답 순서
    기반 1-베이스 순위다(KIS 가 별도 순위 필드를 주지 않음).
    """

    rank: int
    symbol: str                       # ELW 표준코드(6자리)
    name: str
    price: Decimal
    change: Decimal                   # 전일대비(부호 포함)
    change_percent: Decimal           # 전일대비율(부호 포함)
    volume: int                       # 누적 거래량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_raw", freeze_vendor_payload(self._raw))

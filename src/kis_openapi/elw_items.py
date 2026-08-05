"""ELW(주식워런트증권) 시세 DATA 타입들.

ELW 는 증권 형태로 상장된 옵션이다(기초자산에 대한 콜/풋 권리를 담은 워런트). 기본 시세
(현재가/호가/체결)는 종목 핸들(:class:`~kis_openapi.ticker.Ticker`)로 조회하고, 여기 타입들은
ELW 고유의 **옵션 분석 지표** -- 민감도(그릭스), 변동성, 투자지표의 시계열 -- 를 담는다.

:class:`~kis_openapi.elw.Elw` 핸들(``kis.elw(code)``)의 조회 메서드가 돌려준다. 타입이 여럿이라
한 파일에 모은다(ranking_items/index_items 선례).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ElwSensitivityPoint:
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
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ElwVolatilityPoint:
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
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))


@dataclass(frozen=True, slots=True)
class ElwIndicatorPoint:
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
        object.__setattr__(self, "_raw", MappingProxyType(dict(self._raw)))

"""ELW 실시간 파서 -- 호가/체결가/예상체결.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 세 TR 은 레이아웃이
서로 달라 각자 필드 튜플과 파서를 갖는다:

* ``H0EWASP0`` (실시간호가, 73필드) -> :class:`OrderBook` -- 10호가 사다리 + LP 잔량 + 예상체결.
* ``H0EWCNT0`` (실시간체결가, 63필드) -> :class:`ExecutionTick` -- 체결 + ELW 지표(그릭/내재변동성 등).
* ``H0EWANC0`` (실시간예상체결, 59필드) -> :class:`ExpectedConclusion` -- 예상체결 + ELW 지표.

가격/수량/그릭 등 의미상 숫자인 헤드라인 필드는 :func:`_decimal` 로 ``Decimal`` 화하고, 코드/시각/
부호/Y·N 플래그는 원문 문자열로 둔다. 전체 필드 원문은 각 엔티티의 ``_raw`` 에 Element 이름으로
보존된다. (원장 ELW 시트는 모든 필드 타입을 문자열로 표기하지만, 여기서는 의미 기반으로 타입화한다.)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register

# --------------------------------------------------------------------------------------
# 필드 레이아웃(원장 Response Body 순서). 인덱스 = ^ 위치.
# --------------------------------------------------------------------------------------

# H0EWASP0 -- ELW 실시간호가(73필드).
_ORDER_BOOK_FIELDS = (
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE", "ASKP1", "ASKP2",
    "ASKP3", "ASKP4", "ASKP5", "ASKP6", "ASKP7",
    "ASKP8", "ASKP9", "ASKP10", "BIDP1", "BIDP2",
    "BIDP3", "BIDP4", "BIDP5", "BIDP6", "BIDP7",
    "BIDP8", "BIDP9", "BIDP10", "ASKP_RSQN1", "ASKP_RSQN2",
    "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5", "ASKP_RSQN6", "ASKP_RSQN7",
    "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10", "BIDP_RSQN1", "BIDP_RSQN2",
    "BIDP_RSQN3", "BIDP_RSQN4", "BIDP_RSQN5", "BIDP_RSQN6", "BIDP_RSQN7",
    "BIDP_RSQN8", "BIDP_RSQN9", "BIDP_RSQN10", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "ANTC_CNPR", "ANTC_CNQN", "ANTC_CNTG_VRSS_SIGN", "ANTC_CNTG_VRSS", "ANTC_CNTG_PRDY_CTRT",
    "LP_ASKP_RSQN1", "LP_ASKP_RSQN2", "LP_ASKP_RSQN3", "LP_BIDP_RSQN4", "LP_ASKP_RSQN4",
    "LP_BIDP_RSQN5", "LP_ASKP_RSQN5", "LP_BIDP_RSQN6", "LP_ASKP_RSQN6", "LP_BIDP_RSQN7",
    "LP_ASKP_RSQN7", "LP_ASKP_RSQN8", "LP_BIDP_RSQN8", "LP_ASKP_RSQN9", "LP_BIDP_RSQN9",
    "LP_ASKP_RSQN10", "LP_BIDP_RSQN10", "LP_BIDP_RSQN1", "LP_TOTAL_ASKP_RSQN", "LP_BIDP_RSQN2",
    "LP_TOTAL_BIDP_RSQN", "LP_BIDP_RSQN3", "ANTC_VOL",
)

# H0EWCNT0 -- ELW 실시간체결가(63필드).
_EXECUTION_TICK_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CNTG_CLS_CODE", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "TMVL_VAL", "PRIT", "PRMM_VAL", "GEAR", "PRLS_QRYR_RATE",
    "INVL_VAL", "PRMM_RATE", "CFP", "LVRG_VAL", "DELTA",
    "GAMA", "VEGA", "THETA", "RHO", "HTS_INTS_VLTL",
    "HTS_THPR", "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE", "APPRCH_RATE",
    "LP_HVOL", "LP_HLDN_RATE", "LP_NTBY_QTY",
)

# H0EWANC0 -- ELW 실시간예상체결(59필드). 체결가와 유사하나 전일동시간/접근도/LP순매도량이 없다.
_EXPECTED_CONCLUSION_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CNTG_CLS_CODE", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "TMVL_VAL", "PRIT", "PRMM_VAL", "GEAR", "PRLS_QRYR_RATE",
    "INVL_VAL", "PRMM_RATE", "CFP", "LVRG_VAL", "DELTA",
    "GAMA", "VEGA", "THETA", "RHO", "HTS_INTS_VLTL",
    "HTS_THPR", "VOL_TNRT", "LP_HVOL", "LP_HLDN_RATE",
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _ladder(raw: Mapping[str, str], prefix: str, levels: int = 10) -> tuple[Decimal, ...]:
    """``prefix1..prefixN`` 사다리를 Decimal 튜플로. (호가/잔량 10단계 헤드라인용)."""
    return tuple(_decimal(raw[f"{prefix}{i}"]) for i in range(1, levels + 1))


# --------------------------------------------------------------------------------------
# 엔티티(frozen dataclass) -- 헤드라인 타입 필드 + 전체 원문 _raw.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderBook:
    """ELW 실시간호가(H0EWASP0). 매도/매수 10호가 사다리와 잔량, LP 총잔량, 예상체결.

    ``ask_prices``/``bid_prices`` 와 ``ask_volumes``/``bid_volumes`` 는 1~10호가를 순서대로 담은
    길이 10 튜플이다. 전체 73개 필드(개별 LP 잔량 포함)는 ``_raw`` 에 Element 이름으로 있다.
    """

    symbol: str
    time: str  # HHMMSS (영업시간)
    hour_class: str  # 시간구분코드
    ask_prices: tuple[Decimal, ...]  # ASKP1..10
    bid_prices: tuple[Decimal, ...]  # BIDP1..10
    ask_volumes: tuple[Decimal, ...]  # ASKP_RSQN1..10
    bid_volumes: tuple[Decimal, ...]  # BIDP_RSQN1..10
    total_ask_volume: Decimal
    total_bid_volume: Decimal
    expected_price: Decimal  # 예상체결가
    expected_volume: Decimal  # 예상체결량
    expected_change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    expected_change: Decimal
    expected_change_percent: Decimal
    lp_total_ask_volume: Decimal  # LP 총매도호가잔량
    lp_total_bid_volume: Decimal  # LP 총매수호가잔량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class ExecutionTick:
    """ELW 실시간체결가(H0EWCNT0). 체결 현재가/등락/거래량과 ELW 고유 지표(그릭/내재변동성 등).

    자주 쓰는 헤드라인만 타입화하고, 전체 63개 필드는 ``_raw`` 에 Element 이름으로 있다.
    """

    symbol: str
    time: str  # HHMMSS (주식체결시간)
    current_price: Decimal
    change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    best_ask: Decimal
    best_bid: Decimal
    trade_volume: Decimal  # 이번 체결 수량
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    conclusion_strength: Decimal  # 체결강도
    trade_sign: str  # 체결구분코드
    business_date: str  # YYYYMMDD
    trading_halted: bool
    # -- ELW 지표 --
    time_value: Decimal  # 시간가치
    parity: Decimal  # 패리티
    premium: Decimal  # 프리미엄값
    premium_percent: Decimal  # 프리미엄비율
    gearing: Decimal  # 기어링
    leverage: Decimal  # 레버리지
    breakeven_percent: Decimal  # 손익분기비율
    intrinsic_value: Decimal  # 내재가치
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    theta: Decimal
    rho: Decimal
    implied_volatility: Decimal  # HTS 내재변동성
    theoretical_price: Decimal  # HTS 이론가
    lp_holding: Decimal  # LP 보유량
    lp_holding_percent: Decimal  # LP 보유비율
    lp_net_sell_volume: Decimal  # LP 순매도량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class ExpectedConclusion:
    """ELW 실시간예상체결(H0EWANC0). 예상 체결가/등락/거래량과 ELW 고유 지표(그릭 등).

    체결가(:class:`ExecutionTick`)와 유사하나 전일동시간누적/접근도/LP순매도량 필드가 없다. 전체
    59개 필드는 ``_raw`` 에 Element 이름으로 있다.
    """

    symbol: str
    time: str  # HHMMSS (주식체결시간)
    expected_price: Decimal  # 예상체결가
    change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    best_ask: Decimal
    best_bid: Decimal
    expected_volume: Decimal  # 예상체결량
    accumulated_volume: Decimal
    accumulated_value: Decimal  # 누적 거래대금
    conclusion_strength: Decimal  # 체결강도
    trade_sign: str  # 체결구분코드
    business_date: str  # YYYYMMDD
    trading_halted: bool
    # -- ELW 지표 --
    time_value: Decimal  # 시간가치
    parity: Decimal  # 패리티
    premium: Decimal  # 프리미엄값
    premium_percent: Decimal  # 프리미엄비율
    gearing: Decimal  # 기어링
    leverage: Decimal  # 레버리지
    breakeven_percent: Decimal  # 손익분기비율
    intrinsic_value: Decimal  # 내재가치
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    theta: Decimal
    rho: Decimal
    implied_volatility: Decimal  # HTS 내재변동성
    theoretical_price: Decimal  # HTS 이론가
    lp_holding: Decimal  # LP 보유량
    lp_holding_percent: Decimal  # LP 보유비율
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


# --------------------------------------------------------------------------------------
# 파서(순수 함수) -- list[str] -> 엔티티.
# --------------------------------------------------------------------------------------


def parse_order_book(fields: list[str]) -> OrderBook:
    """H0EWASP0 한 레코드(73필드) -> :class:`OrderBook`."""
    raw = MappingProxyType(dict(zip(_ORDER_BOOK_FIELDS, fields, strict=False)))
    return OrderBook(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["BSOP_HOUR"],
        hour_class=raw["HOUR_CLS_CODE"],
        ask_prices=_ladder(raw, "ASKP"),
        bid_prices=_ladder(raw, "BIDP"),
        ask_volumes=_ladder(raw, "ASKP_RSQN"),
        bid_volumes=_ladder(raw, "BIDP_RSQN"),
        total_ask_volume=_decimal(raw["TOTAL_ASKP_RSQN"]),
        total_bid_volume=_decimal(raw["TOTAL_BIDP_RSQN"]),
        expected_price=_decimal(raw["ANTC_CNPR"]),
        expected_volume=_decimal(raw["ANTC_CNQN"]),
        expected_change_sign=raw["ANTC_CNTG_VRSS_SIGN"],
        expected_change=_decimal(raw["ANTC_CNTG_VRSS"]),
        expected_change_percent=_decimal(raw["ANTC_CNTG_PRDY_CTRT"]),
        lp_total_ask_volume=_decimal(raw["LP_TOTAL_ASKP_RSQN"]),
        lp_total_bid_volume=_decimal(raw["LP_TOTAL_BIDP_RSQN"]),
        _raw=raw,
    )


def parse_execution_tick(fields: list[str]) -> ExecutionTick:
    """H0EWCNT0 한 레코드(63필드) -> :class:`ExecutionTick`."""
    raw = MappingProxyType(dict(zip(_EXECUTION_TICK_FIELDS, fields, strict=False)))
    return ExecutionTick(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        current_price=_decimal(raw["STCK_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        best_ask=_decimal(raw["ASKP1"]),
        best_bid=_decimal(raw["BIDP1"]),
        trade_volume=_decimal(raw["CNTG_VOL"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        conclusion_strength=_decimal(raw["CTTR"]),
        trade_sign=raw["CNTG_CLS_CODE"],
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        time_value=_decimal(raw["TMVL_VAL"]),
        parity=_decimal(raw["PRIT"]),
        premium=_decimal(raw["PRMM_VAL"]),
        premium_percent=_decimal(raw["PRMM_RATE"]),
        gearing=_decimal(raw["GEAR"]),
        leverage=_decimal(raw["LVRG_VAL"]),
        breakeven_percent=_decimal(raw["PRLS_QRYR_RATE"]),
        intrinsic_value=_decimal(raw["INVL_VAL"]),
        delta=_decimal(raw["DELTA"]),
        gamma=_decimal(raw["GAMA"]),
        vega=_decimal(raw["VEGA"]),
        theta=_decimal(raw["THETA"]),
        rho=_decimal(raw["RHO"]),
        implied_volatility=_decimal(raw["HTS_INTS_VLTL"]),
        theoretical_price=_decimal(raw["HTS_THPR"]),
        lp_holding=_decimal(raw["LP_HVOL"]),
        lp_holding_percent=_decimal(raw["LP_HLDN_RATE"]),
        lp_net_sell_volume=_decimal(raw["LP_NTBY_QTY"]),
        _raw=raw,
    )


def parse_expected_conclusion(fields: list[str]) -> ExpectedConclusion:
    """H0EWANC0 한 레코드(59필드) -> :class:`ExpectedConclusion`."""
    raw = MappingProxyType(dict(zip(_EXPECTED_CONCLUSION_FIELDS, fields, strict=False)))
    return ExpectedConclusion(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        expected_price=_decimal(raw["STCK_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        best_ask=_decimal(raw["ASKP1"]),
        best_bid=_decimal(raw["BIDP1"]),
        expected_volume=_decimal(raw["CNTG_VOL"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        conclusion_strength=_decimal(raw["CTTR"]),
        trade_sign=raw["CNTG_CLS_CODE"],
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        time_value=_decimal(raw["TMVL_VAL"]),
        parity=_decimal(raw["PRIT"]),
        premium=_decimal(raw["PRMM_VAL"]),
        premium_percent=_decimal(raw["PRMM_RATE"]),
        gearing=_decimal(raw["GEAR"]),
        leverage=_decimal(raw["LVRG_VAL"]),
        breakeven_percent=_decimal(raw["PRLS_QRYR_RATE"]),
        intrinsic_value=_decimal(raw["INVL_VAL"]),
        delta=_decimal(raw["DELTA"]),
        gamma=_decimal(raw["GAMA"]),
        vega=_decimal(raw["VEGA"]),
        theta=_decimal(raw["THETA"]),
        rho=_decimal(raw["RHO"]),
        implied_volatility=_decimal(raw["HTS_INTS_VLTL"]),
        theoretical_price=_decimal(raw["HTS_THPR"]),
        lp_holding=_decimal(raw["LP_HVOL"]),
        lp_holding_percent=_decimal(raw["LP_HLDN_RATE"]),
        _raw=raw,
    )


register(TRSpec("H0EWASP0", field_count=len(_ORDER_BOOK_FIELDS), parser=parse_order_book))
register(TRSpec("H0EWCNT0", field_count=len(_EXECUTION_TICK_FIELDS), parser=parse_execution_tick))
register(
    TRSpec(
        "H0EWANC0",
        field_count=len(_EXPECTED_CONCLUSION_FIELDS),
        parser=parse_expected_conclusion,
    )
)

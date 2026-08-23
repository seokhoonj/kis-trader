"""국내주식 실시간 파서 -- 체결가/호가/예상체결/프로그램매매/회원사/체결통보.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 같은 필드
레이아웃을 쓰는 TR 은 파서를 공유한다(예: 체결가 KRX/NXT/통합 H0STCNT0/H0NXCNT0/H0UNCNT0
는 동일 46필드). 헤드라인 필드만 타입화하고 전체 원장 필드는 ``_raw`` (Element 이름->원문)
에 담는다. 결과 엔티티는 REST 결과 타입과 같은 관례(공개 식별자 산업표준 영어, 한국어
docstring, frozen dataclass, 숫자 Decimal)로 이 모듈 안에 정의한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register
from ..messages import TradeTick

# H0STCNT0 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_TRADE_TICK_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE", "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)

# NXT/통합 체결가는 index 21 의 Element 이름만 다르다(KRX=CCLD_DVSN, NXT/통합=CNTG_CLS_CODE).
# 위치·의미(체결구분)는 동일. _raw 를 각 시트의 원장 키로 정직하게 노출하려고 튜플만 분리한다.
_TRADE_TICK_FIELDS_NXT = (
    _TRADE_TICK_FIELDS[:21] + ("CNTG_CLS_CODE",) + _TRADE_TICK_FIELDS[22:]
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def parse_trade_tick(
    fields: list[str], field_names: tuple[str, ...] = _TRADE_TICK_FIELDS
) -> TradeTick:
    """체결가 한 레코드(46필드) -> :class:`TradeTick`.

    ``field_names`` 로 KRX(H0STCNT0)와 NXT/통합(H0NXCNT0/H0UNCNT0)의 index 21 이름 차이를
    흡수한다(기본 = KRX 레이아웃).
    """
    raw = MappingProxyType(dict(zip(field_names, fields, strict=False)))
    return TradeTick(
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
        trade_sign=raw.get("CCLD_DVSN") or raw.get("CNTG_CLS_CODE", ""),
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        static_vi_reference_price=_decimal(raw["VI_STND_PRC"]),
        _raw=raw,
    )


# KRX 는 CCLD_DVSN, NXT/통합은 CNTG_CLS_CODE 레이아웃(index 21만 다름, 둘 다 46필드).
register(TRSpec("H0STCNT0", field_count=len(_TRADE_TICK_FIELDS), parser=parse_trade_tick))
for _tr_id in ("H0NXCNT0", "H0UNCNT0"):
    register(
        TRSpec(
            _tr_id,
            field_count=len(_TRADE_TICK_FIELDS_NXT),
            parser=lambda fields: parse_trade_tick(fields, _TRADE_TICK_FIELDS_NXT),
        )
    )


# ---------------------------------------------------------------------------
# 호가 (StockOrderBook) -- KRX / NXT / 통합 / 시간외
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockOrderBook:
    """국내주식 실시간 호가. 매도/매수 각 호가단계 잔량과 총잔량, 예상체결가/량.

    KRX(H0STASP0, 10단계), NXT(H0NXASP0, 10단계), 통합(H0UNASP0, 10단계 + KRX/NXT
    중간가), 시간외(H0STOAA0, 9단계)가 각기 다른 필드 집합을 쓰지만 상단 호가/총잔량/
    예상체결은 공통이라 하나의 엔티티로 표현한다. 전체 단계별 잔량은 ``_raw`` 에 있다.
    """

    symbol: str
    time: str  # BSOP_HOUR, HHMMSS
    hour_class: str  # HOUR_CLS_CODE 시간구분(0 장중, 등)
    best_ask: Decimal  # ASKP1
    best_bid: Decimal  # BIDP1
    best_ask_quantity: Decimal  # ASKP_RSQN1
    best_bid_quantity: Decimal  # BIDP_RSQN1
    total_ask_quantity: Decimal  # TOTAL_ASKP_RSQN
    total_bid_quantity: Decimal  # TOTAL_BIDP_RSQN
    expected_price: Decimal  # ANTC_CNPR 예상체결가
    expected_qty: Decimal  # ANTC_CNQN 예상체결량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


def _order_book(raw: Mapping[str, str]) -> StockOrderBook:
    """호가 원장 매핑 -> :class:`StockOrderBook` (레이아웃별 공통 헤드라인)."""
    return StockOrderBook(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["BSOP_HOUR"],
        hour_class=raw["HOUR_CLS_CODE"],
        best_ask=_decimal(raw["ASKP1"]),
        best_bid=_decimal(raw["BIDP1"]),
        best_ask_quantity=_decimal(raw["ASKP_RSQN1"]),
        best_bid_quantity=_decimal(raw["BIDP_RSQN1"]),
        total_ask_quantity=_decimal(raw["TOTAL_ASKP_RSQN"]),
        total_bid_quantity=_decimal(raw["TOTAL_BIDP_RSQN"]),
        expected_price=_decimal(raw["ANTC_CNPR"]),
        expected_qty=_decimal(raw["ANTC_CNQN"]),
        _raw=MappingProxyType(dict(raw)),
    )


# 매도/매수 10단계 호가 + 잔량 + 예상체결 공통 프리픽스(KRX/NXT/통합 동일 순서).
_ORDER_BOOK_10_PREFIX = (
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE",
    "ASKP1", "ASKP2", "ASKP3", "ASKP4", "ASKP5",
    "ASKP6", "ASKP7", "ASKP8", "ASKP9", "ASKP10",
    "BIDP1", "BIDP2", "BIDP3", "BIDP4", "BIDP5",
    "BIDP6", "BIDP7", "BIDP8", "BIDP9", "BIDP10",
    "ASKP_RSQN1", "ASKP_RSQN2", "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5",
    "ASKP_RSQN6", "ASKP_RSQN7", "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10",
    "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3", "BIDP_RSQN4", "BIDP_RSQN5",
    "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9", "BIDP_RSQN10",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN", "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC", "STCK_DEAL_CLS_CODE",
)

# KRX 호가(H0STASP0): 공통 10단계 + 단일 중간가.
_ORDER_BOOK_KRX_FIELDS = _ORDER_BOOK_10_PREFIX + ("MID_PRC", "MIDP_TOTAL_RSQN", "MIDP_CLS_CODE")

# NXT 호가(H0NXASP0): 공통 10단계 + NXT 중간가.
_ORDER_BOOK_NXT_FIELDS = _ORDER_BOOK_10_PREFIX + ("NMID_PRC", "NMID_TOTAL_RSQN", "NMID_CLS_CODE")

# 통합 호가(H0UNASP0): 공통 10단계 + KRX 중간가 + NXT 중간가.
_ORDER_BOOK_UNIFIED_FIELDS = _ORDER_BOOK_10_PREFIX + (
    "KMID_PRC", "KMID_TOTAL_RSQN", "KMID_CLS_CODE",
    "NMID_PRC", "NMID_TOTAL_RSQN", "NMID_CLS_CODE",
)

# 시간외 호가(H0STOAA0): 매도/매수 9단계(중간가 없음).
_ORDER_BOOK_AFTER_HOURS_FIELDS = (
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE",
    "ASKP1", "ASKP2", "ASKP3", "ASKP4", "ASKP5", "ASKP6", "ASKP7", "ASKP8", "ASKP9",
    "BIDP1", "BIDP2", "BIDP3", "BIDP4", "BIDP5", "BIDP6", "BIDP7", "BIDP8", "BIDP9",
    "ASKP_RSQN1", "ASKP_RSQN2", "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5",
    "ASKP_RSQN6", "ASKP_RSQN7", "ASKP_RSQN8", "ASKP_RSQN9",
    "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3", "BIDP_RSQN4", "BIDP_RSQN5",
    "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN", "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC",
)


def parse_order_book_krx(fields: list[str]) -> StockOrderBook:
    """H0STASP0 한 레코드(62필드) -> :class:`StockOrderBook`."""
    return _order_book(dict(zip(_ORDER_BOOK_KRX_FIELDS, fields, strict=False)))


def parse_order_book_nxt(fields: list[str]) -> StockOrderBook:
    """H0NXASP0 한 레코드(62필드) -> :class:`StockOrderBook`."""
    return _order_book(dict(zip(_ORDER_BOOK_NXT_FIELDS, fields, strict=False)))


def parse_order_book_unified(fields: list[str]) -> StockOrderBook:
    """H0UNASP0 한 레코드(65필드) -> :class:`StockOrderBook`."""
    return _order_book(dict(zip(_ORDER_BOOK_UNIFIED_FIELDS, fields, strict=False)))


def parse_order_book_after_hours(fields: list[str]) -> StockOrderBook:
    """H0STOAA0 한 레코드(54필드, 시간외 9단계) -> :class:`StockOrderBook`."""
    return _order_book(dict(zip(_ORDER_BOOK_AFTER_HOURS_FIELDS, fields, strict=False)))


register(TRSpec("H0STASP0", field_count=len(_ORDER_BOOK_KRX_FIELDS), parser=parse_order_book_krx))
register(TRSpec("H0NXASP0", field_count=len(_ORDER_BOOK_NXT_FIELDS), parser=parse_order_book_nxt))
register(TRSpec("H0UNASP0", field_count=len(_ORDER_BOOK_UNIFIED_FIELDS), parser=parse_order_book_unified))
register(TRSpec("H0STOAA0", field_count=len(_ORDER_BOOK_AFTER_HOURS_FIELDS), parser=parse_order_book_after_hours))


# ---------------------------------------------------------------------------
# 예상체결 (StockExpectedConclusion) -- KRX / NXT / 통합
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockExpectedConclusion:
    """국내주식 실시간 예상체결. 장 시작/종료 동시호가 등의 예상 체결가/량.

    KRX(H0STANC0, 45필드)와 NXT/통합(H0NXANC0/H0UNANC0, 46필드 -- 끝에 VI 기준가 추가)
    이 앞 43필드를 공유한다. 전체 필드는 ``_raw`` 에 있다.
    """

    symbol: str
    time: str  # STCK_CNTG_HOUR
    expected_price: Decimal  # STCK_PRPR 예상체결가
    change_sign: str  # PRDY_VRSS_SIGN
    change: Decimal  # PRDY_VRSS
    change_percent: Decimal  # PRDY_CTRT
    open: Decimal  # STCK_OPRC
    high: Decimal  # STCK_HGPR
    low: Decimal  # STCK_LWPR
    expected_volume: Decimal  # CNTG_VOL 예상체결량
    accumulated_volume: Decimal  # ACML_VOL
    business_date: str  # BSOP_DATE
    trading_halted: bool  # TRHT_YN
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


def _expected_conclusion(raw: Mapping[str, str]) -> StockExpectedConclusion:
    """예상체결 원장 매핑 -> :class:`StockExpectedConclusion` (레이아웃별 공통 헤드라인)."""
    return StockExpectedConclusion(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        expected_price=_decimal(raw["STCK_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        expected_volume=_decimal(raw["CNTG_VOL"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        _raw=MappingProxyType(dict(raw)),
    )


# 예상체결 앞 43필드(KRX/NXT/통합 공통).
_EXPECTED_CONCLUSION_PREFIX = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CNTG_CLS_CODE", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
)

# KRX 예상체결(H0STANC0): 공통 43 + 시간구분 + 임의종료구분.
_EXPECTED_CONCLUSION_KRX_FIELDS = _EXPECTED_CONCLUSION_PREFIX + (
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE",
)

# NXT/통합 예상체결(H0NXANC0/H0UNANC0): KRX 필드 + VI 기준가.
_EXPECTED_CONCLUSION_EXT_FIELDS = _EXPECTED_CONCLUSION_KRX_FIELDS + ("VI_STND_PRC",)


def parse_expected_conclusion_krx(fields: list[str]) -> StockExpectedConclusion:
    """H0STANC0 한 레코드(45필드) -> :class:`StockExpectedConclusion`."""
    return _expected_conclusion(dict(zip(_EXPECTED_CONCLUSION_KRX_FIELDS, fields, strict=False)))


def parse_expected_conclusion_ext(fields: list[str]) -> StockExpectedConclusion:
    """H0NXANC0/H0UNANC0 한 레코드(46필드) -> :class:`StockExpectedConclusion`."""
    return _expected_conclusion(dict(zip(_EXPECTED_CONCLUSION_EXT_FIELDS, fields, strict=False)))


register(TRSpec(
    "H0STANC0", field_count=len(_EXPECTED_CONCLUSION_KRX_FIELDS), parser=parse_expected_conclusion_krx
))
for _tr_id in ("H0NXANC0", "H0UNANC0"):
    register(TRSpec(
        _tr_id, field_count=len(_EXPECTED_CONCLUSION_EXT_FIELDS), parser=parse_expected_conclusion_ext
    ))


# ---------------------------------------------------------------------------
# 시간외 체결/예상체결 (AfterHoursTick) -- KRX
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AfterHoursTick:
    """국내주식 시간외 실시간 체결/예상체결. 시간외 단일가/종가 세션의 현재가/등락/거래량.

    시간외 예상체결(H0STOAC0)과 시간외 체결가(H0STOUP0)가 동일 43필드 레이아웃을 공유한다.
    전체 필드는 ``_raw`` 에 있다.
    """

    symbol: str
    time: str  # STCK_CNTG_HOUR
    current_price: Decimal  # STCK_PRPR
    change_sign: str  # PRDY_VRSS_SIGN
    change: Decimal  # PRDY_VRSS
    change_percent: Decimal  # PRDY_CTRT
    open: Decimal  # STCK_OPRC
    high: Decimal  # STCK_HGPR
    low: Decimal  # STCK_LWPR
    trade_volume: Decimal  # CNTG_VOL
    accumulated_volume: Decimal  # ACML_VOL
    accumulated_value: Decimal  # ACML_TR_PBMN
    business_date: str  # BSOP_DATE
    trading_halted: bool  # TRHT_YN
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


# 시간외 체결/예상체결(H0STOAC0/H0STOUP0) 43필드.
_AFTER_HOURS_TICK_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CNTG_CLS_CODE", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
)


def parse_after_hours_tick(fields: list[str]) -> AfterHoursTick:
    """H0STOAC0/H0STOUP0 한 레코드(43필드) -> :class:`AfterHoursTick`."""
    raw = MappingProxyType(dict(zip(_AFTER_HOURS_TICK_FIELDS, fields, strict=False)))
    return AfterHoursTick(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        current_price=_decimal(raw["STCK_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        trade_volume=_decimal(raw["CNTG_VOL"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        _raw=raw,
    )


for _tr_id in ("H0STOAC0", "H0STOUP0"):
    register(TRSpec(_tr_id, field_count=len(_AFTER_HOURS_TICK_FIELDS), parser=parse_after_hours_tick))


# ---------------------------------------------------------------------------
# 프로그램매매 (StockProgramTrade) -- KRX / NXT / 통합
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockProgramTrade:
    """국내주식 실시간 프로그램매매. 프로그램 매도/매수 체결량과 거래대금, 순매수.

    KRX(H0STPGM0)/NXT(H0NXPGM0)/통합(H0UNPGM0)이 동일 11필드 레이아웃을 공유한다.
    """

    symbol: str
    time: str  # STCK_CNTG_HOUR
    sell_volume: Decimal  # SELN_CNQN 매도 체결량
    sell_value: Decimal  # SELN_TR_PBMN 매도 거래대금
    buy_volume: Decimal  # SHNU_CNQN 매수 체결량
    buy_value: Decimal  # SHNU_TR_PBMN 매수 거래대금
    net_buy_volume: Decimal  # NTBY_CNQN 순매수 체결량
    net_buy_value: Decimal  # NTBY_TR_PBMN 순매수 거래대금
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


_PROGRAM_TRADE_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "SELN_CNQN", "SELN_TR_PBMN", "SHNU_CNQN",
    "SHNU_TR_PBMN", "NTBY_CNQN", "NTBY_TR_PBMN", "SELN_RSQN", "SHNU_RSQN",
    "WHOL_NTBY_QTY",
)


def parse_program_trade(fields: list[str]) -> StockProgramTrade:
    """H0STPGM0/H0NXPGM0/H0UNPGM0 한 레코드(11필드) -> :class:`StockProgramTrade`."""
    raw = MappingProxyType(dict(zip(_PROGRAM_TRADE_FIELDS, fields, strict=False)))
    return StockProgramTrade(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        sell_volume=_decimal(raw["SELN_CNQN"]),
        sell_value=_decimal(raw["SELN_TR_PBMN"]),
        buy_volume=_decimal(raw["SHNU_CNQN"]),
        buy_value=_decimal(raw["SHNU_TR_PBMN"]),
        net_buy_volume=_decimal(raw["NTBY_CNQN"]),
        net_buy_value=_decimal(raw["NTBY_TR_PBMN"]),
        _raw=raw,
    )


for _tr_id in ("H0STPGM0", "H0NXPGM0", "H0UNPGM0"):
    register(TRSpec(_tr_id, field_count=len(_PROGRAM_TRADE_FIELDS), parser=parse_program_trade))


# ---------------------------------------------------------------------------
# 회원사 매매동향 (MemberActivity) -- KRX / NXT / 통합
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberActivity:
    """국내주식 실시간 회원사(거래원) 매매동향. 상위 매도/매수 회원사와 외국계 순매수.

    KRX(H0STMBC0)/NXT(H0NXMBC0)/통합(H0UNMBC0)이 동일 78필드 레이아웃을 공유한다. 5개
    회원사명/코드/비중/증감과 영문명은 전부 ``_raw`` 에 있고, 아래는 상위 1위와 외국계 집계만
    타입화한다.
    """

    symbol: str
    top_seller: str  # SELN2_MBCR_NAME1 매도 1위 회원사명
    top_buyer: str  # BYOV_MBCR_NAME1 매수 1위 회원사명
    foreign_sell_volume: Decimal  # GLOB_TOTAL_SELN_QTY 외국계 총 매도 수량
    foreign_buy_volume: Decimal  # GLOB_TOTAL_SHNU_QTY 외국계 총 매수 수량
    foreign_net_buy_volume: Decimal  # GLOB_NTBY_QTY 외국계 순매수 수량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


_MEMBER_ACTIVITY_FIELDS = (
    "MKSC_SHRN_ISCD",
    "SELN2_MBCR_NAME1", "SELN2_MBCR_NAME2", "SELN2_MBCR_NAME3", "SELN2_MBCR_NAME4", "SELN2_MBCR_NAME5",
    "BYOV_MBCR_NAME1", "BYOV_MBCR_NAME2", "BYOV_MBCR_NAME3", "BYOV_MBCR_NAME4", "BYOV_MBCR_NAME5",
    "TOTAL_SELN_QTY1", "TOTAL_SELN_QTY2", "TOTAL_SELN_QTY3", "TOTAL_SELN_QTY4", "TOTAL_SELN_QTY5",
    "TOTAL_SHNU_QTY1", "TOTAL_SHNU_QTY2", "TOTAL_SHNU_QTY3", "TOTAL_SHNU_QTY4", "TOTAL_SHNU_QTY5",
    "SELN_MBCR_GLOB_YN_1", "SELN_MBCR_GLOB_YN_2", "SELN_MBCR_GLOB_YN_3", "SELN_MBCR_GLOB_YN_4", "SELN_MBCR_GLOB_YN_5",
    "SHNU_MBCR_GLOB_YN_1", "SHNU_MBCR_GLOB_YN_2", "SHNU_MBCR_GLOB_YN_3", "SHNU_MBCR_GLOB_YN_4", "SHNU_MBCR_GLOB_YN_5",
    "SELN_MBCR_NO1", "SELN_MBCR_NO2", "SELN_MBCR_NO3", "SELN_MBCR_NO4", "SELN_MBCR_NO5",
    "SHNU_MBCR_NO1", "SHNU_MBCR_NO2", "SHNU_MBCR_NO3", "SHNU_MBCR_NO4", "SHNU_MBCR_NO5",
    "SELN_MBCR_RLIM1", "SELN_MBCR_RLIM2", "SELN_MBCR_RLIM3", "SELN_MBCR_RLIM4", "SELN_MBCR_RLIM5",
    "SHNU_MBCR_RLIM1", "SHNU_MBCR_RLIM2", "SHNU_MBCR_RLIM3", "SHNU_MBCR_RLIM4", "SHNU_MBCR_RLIM5",
    "SELN_QTY_ICDC1", "SELN_QTY_ICDC2", "SELN_QTY_ICDC3", "SELN_QTY_ICDC4", "SELN_QTY_ICDC5",
    "SHNU_QTY_ICDC1", "SHNU_QTY_ICDC2", "SHNU_QTY_ICDC3", "SHNU_QTY_ICDC4", "SHNU_QTY_ICDC5",
    "GLOB_TOTAL_SELN_QTY", "GLOB_TOTAL_SHNU_QTY", "GLOB_TOTAL_SELN_QTY_ICDC", "GLOB_TOTAL_SHNU_QTY_ICDC",
    "GLOB_NTBY_QTY", "GLOB_SELN_RLIM", "GLOB_SHNU_RLIM",
    "SELN2_MBCR_ENG_NAME1", "SELN2_MBCR_ENG_NAME2", "SELN2_MBCR_ENG_NAME3", "SELN2_MBCR_ENG_NAME4", "SELN2_MBCR_ENG_NAME5",
    "BYOV_MBCR_ENG_NAME1", "BYOV_MBCR_ENG_NAME2", "BYOV_MBCR_ENG_NAME3", "BYOV_MBCR_ENG_NAME4", "BYOV_MBCR_ENG_NAME5",
)


def parse_member_activity(fields: list[str]) -> MemberActivity:
    """H0STMBC0/H0NXMBC0/H0UNMBC0 한 레코드(78필드) -> :class:`MemberActivity`."""
    raw = MappingProxyType(dict(zip(_MEMBER_ACTIVITY_FIELDS, fields, strict=False)))
    return MemberActivity(
        symbol=raw["MKSC_SHRN_ISCD"],
        top_seller=raw["SELN2_MBCR_NAME1"],
        top_buyer=raw["BYOV_MBCR_NAME1"],
        foreign_sell_volume=_decimal(raw["GLOB_TOTAL_SELN_QTY"]),
        foreign_buy_volume=_decimal(raw["GLOB_TOTAL_SHNU_QTY"]),
        foreign_net_buy_volume=_decimal(raw["GLOB_NTBY_QTY"]),
        _raw=raw,
    )


for _tr_id in ("H0STMBC0", "H0NXMBC0", "H0UNMBC0"):
    register(TRSpec(_tr_id, field_count=len(_MEMBER_ACTIVITY_FIELDS), parser=parse_member_activity))


# ---------------------------------------------------------------------------
# 체결통보 (StockExecutionNotice) -- 암호화 (통보 프레임은 연결 계층이 복호화 후 전달)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockExecutionNotice:
    """국내주식 실시간 체결/주문 통보. 내 주문의 접수/체결/거부 상태와 체결 수량/단가.

    통보 프레임(H0STCNI0)은 암호화되어 오지만 연결 계층이 파서 호출 전에 복호화하므로, 파서는
    평문 필드를 매핑한다. 전체 26필드는 ``_raw`` 에 있다.
    """

    customer_id: str  # CUST_ID
    account_no: str  # ACNT_NO
    order_no: str  # ODER_NO
    original_order_no: str  # OODER_NO
    sell_buy_class: str  # SELN_BYOV_CLS 01 매도 02 매수
    symbol: str  # STCK_SHRN_ISCD
    executed_qty: Decimal  # CNTG_QTY
    executed_price: Decimal  # CNTG_UNPR 체결단가
    time: str  # STCK_CNTG_HOUR
    refused: bool  # RFUS_YN
    conclusion_flag: str  # CNTG_YN 1 접수(주문/정정/취소/거부) 2 체결
    accepted_flag: str  # ACPT_YN
    order_qty: Decimal  # ODER_QTY
    order_price: Decimal  # ODER_PRC 주문가격
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


_EXECUTION_NOTICE_FIELDS = (
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS",
    "RCTF_CLS", "ODER_KIND", "ODER_COND", "STCK_SHRN_ISCD", "CNTG_QTY",
    "CNTG_UNPR", "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN", "ACPT_YN",
    "BRNC_NO", "ODER_QTY", "ACNT_NAME", "ORD_COND_PRC", "ORD_EXG_GB",
    "POPUP_YN", "FILLER", "CRDT_CLS", "CRDT_LOAN_DATE", "CNTG_ISNM40",
    "ODER_PRC",
)


def parse_execution_notice(fields: list[str]) -> StockExecutionNotice:
    """H0STCNI0 한 레코드(26필드, 복호화된 평문) -> :class:`StockExecutionNotice`."""
    raw = MappingProxyType(dict(zip(_EXECUTION_NOTICE_FIELDS, fields, strict=False)))
    return StockExecutionNotice(
        customer_id=raw["CUST_ID"],
        account_no=raw["ACNT_NO"],
        order_no=raw["ODER_NO"],
        original_order_no=raw["OODER_NO"],
        sell_buy_class=raw["SELN_BYOV_CLS"],
        symbol=raw["STCK_SHRN_ISCD"],
        executed_qty=_decimal(raw["CNTG_QTY"]),
        executed_price=_decimal(raw["CNTG_UNPR"]),
        time=raw["STCK_CNTG_HOUR"],
        refused=raw["RFUS_YN"] == "1",  # RFUS_YN 0:승인 1:거부 (Y/N 아님)
        conclusion_flag=raw["CNTG_YN"],
        accepted_flag=raw["ACPT_YN"],
        order_qty=_decimal(raw["ODER_QTY"]),
        order_price=_decimal(raw["ODER_PRC"]),
        _raw=raw,
    )


register(TRSpec(
    "H0STCNI0", field_count=len(_EXECUTION_NOTICE_FIELDS), parser=parse_execution_notice, encrypted=True
))


# ---------------------------------------------------------------------------
# ETF NAV 추이 (ETFNav) -- 국내 ETF
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ETFNav:
    """국내 ETF 실시간 NAV(순자산가치) 추이. 현재 NAV 와 전일대비/시가/고가/저가 NAV.

    NAV(순자산가치)는 ETF 1주가 담는 기초자산의 실질 가치로, 시장 체결가와는 별개로 산출된다.
    NAV추이(H0STNAV0)가 이 엔티티로 매핑된다. 전체 8필드는 ``_raw`` 에 있다.
    """

    symbol: str  # MKSC_SHRN_ISCD
    nav: Decimal  # NAV 현재 순자산가치
    nav_change_sign: str  # NAV_PRDY_VRSS_SIGN 전일대비 부호
    nav_change: Decimal  # NAV_PRDY_VRSS 전일대비
    nav_change_percent: Decimal  # NAV_PRDY_CTRT 전일대비율
    nav_open: Decimal  # OPRC_NAV 시가 NAV
    nav_high: Decimal  # HPRC_NAV 고가 NAV
    nav_low: Decimal  # LPRC_NAV 저가 NAV
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


_ETF_NAV_FIELDS = (
    "MKSC_SHRN_ISCD", "NAV", "NAV_PRDY_VRSS_SIGN", "NAV_PRDY_VRSS", "NAV_PRDY_CTRT",
    "OPRC_NAV", "HPRC_NAV", "LPRC_NAV",
)


def parse_etf_nav(fields: list[str]) -> ETFNav:
    """H0STNAV0 한 레코드(8필드) -> :class:`ETFNav`."""
    raw = MappingProxyType(dict(zip(_ETF_NAV_FIELDS, fields, strict=False)))
    return ETFNav(
        symbol=raw["MKSC_SHRN_ISCD"],
        nav=_decimal(raw["NAV"]),
        nav_change_sign=raw["NAV_PRDY_VRSS_SIGN"],
        nav_change=_decimal(raw["NAV_PRDY_VRSS"]),
        nav_change_percent=_decimal(raw["NAV_PRDY_CTRT"]),
        nav_open=_decimal(raw["OPRC_NAV"]),
        nav_high=_decimal(raw["HPRC_NAV"]),
        nav_low=_decimal(raw["LPRC_NAV"]),
        _raw=raw,
    )


register(TRSpec("H0STNAV0", field_count=len(_ETF_NAV_FIELDS), parser=parse_etf_nav))


# ---------------------------------------------------------------------------
# 장운영정보 (MarketOperation) -- KRX / NXT / 통합
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketOperation:
    """국내주식 실시간 장운영정보. 매매정지 여부/사유와 장운영/VI/거래소 구분 코드.

    KRX(H0STMKO0)/NXT(H0NXMKO0)는 종목코드를 포함한 11필드 동일 레이아웃을 공유하고, 통합
    (H0UNMKO0)은 종목코드가 없는 10필드다(통합 피드엔 종목 식별자가 실리지 않아 ``symbol`` 이
    빈 문자열이 된다). 전체 필드는 ``_raw`` 에 있다.
    """

    symbol: str  # MKSC_SHRN_ISCD (통합 피드엔 없음 -> "")
    trading_halted: bool  # TRHT_YN 매매정지 여부
    halt_reason: str  # TR_SUSP_REAS_CNTT 매매정지 사유
    operation_code: str  # MKOP_CLS_CODE 장운영구분코드
    expected_operation_code: str  # ANTC_MKOP_CLS_CODE 예상장운영구분코드
    vi_code: str  # VI_CLS_CODE VI 적용구분코드
    exchange_code: str  # EXCH_CLS_CODE 거래소구분코드
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


def _market_operation(raw: Mapping[str, str]) -> MarketOperation:
    """장운영정보 원장 매핑 -> :class:`MarketOperation` (종목코드 유무를 흡수)."""
    return MarketOperation(
        symbol=raw.get("MKSC_SHRN_ISCD", ""),
        trading_halted=raw["TRHT_YN"] == "Y",
        halt_reason=raw["TR_SUSP_REAS_CNTT"],
        operation_code=raw["MKOP_CLS_CODE"],
        expected_operation_code=raw["ANTC_MKOP_CLS_CODE"],
        vi_code=raw["VI_CLS_CODE"],
        exchange_code=raw["EXCH_CLS_CODE"],
        _raw=MappingProxyType(dict(raw)),
    )


# KRX/NXT 장운영정보(H0STMKO0/H0NXMKO0): 종목코드 포함 11필드.
_MARKET_OPERATION_FIELDS = (
    "MKSC_SHRN_ISCD", "TRHT_YN", "TR_SUSP_REAS_CNTT", "MKOP_CLS_CODE", "ANTC_MKOP_CLS_CODE",
    "MRKT_TRTM_CLS_CODE", "DIVI_APP_CLS_CODE", "ISCD_STAT_CLS_CODE", "VI_CLS_CODE",
    "OVTM_VI_CLS_CODE", "EXCH_CLS_CODE",
)

# 통합 장운영정보(H0UNMKO0): 종목코드 없는 10필드(TRHT_YN 부터 시작).
_MARKET_OPERATION_UNIFIED_FIELDS = _MARKET_OPERATION_FIELDS[1:]


def parse_market_operation(fields: list[str]) -> MarketOperation:
    """H0STMKO0/H0NXMKO0 한 레코드(11필드) -> :class:`MarketOperation`."""
    return _market_operation(dict(zip(_MARKET_OPERATION_FIELDS, fields, strict=False)))


def parse_market_operation_unified(fields: list[str]) -> MarketOperation:
    """H0UNMKO0 한 레코드(10필드, 종목코드 없음) -> :class:`MarketOperation`. ``symbol`` 은 ""."""
    return _market_operation(dict(zip(_MARKET_OPERATION_UNIFIED_FIELDS, fields, strict=False)))


for _tr_id in ("H0STMKO0", "H0NXMKO0"):
    register(TRSpec(_tr_id, field_count=len(_MARKET_OPERATION_FIELDS), parser=parse_market_operation))
register(TRSpec(
    "H0UNMKO0", field_count=len(_MARKET_OPERATION_UNIFIED_FIELDS), parser=parse_market_operation_unified
))

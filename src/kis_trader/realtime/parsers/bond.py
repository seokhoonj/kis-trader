"""채권 실시간 파서 -- 일반채권 체결가/호가, 채권지수 체결가.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 자산군 엔티티는
:mod:`..messages` 의 스타일(frozen dataclass, 산업표준 영어 식별자, 한국어 docstring,
``_raw`` = 전체 Element->원문, 숫자는 :class:`~decimal.Decimal`)을 그대로 따른다.

일반채권 체결(H0BJCNT0)과 호가(H0BJASP0)는 레이아웃이 다르므로 별도 파서/엔티티다. 원장에서
호가 시트의 TR-id 가 체결과 동일하게(H0BJCNT0) 잘못 기재되어 있으나, 실제 KIS 실시간 호가
TR-id 는 H0BJASP0 이다(같은 TR-id 로는 체결/호가 프레임을 구분할 수 없다). 여기서는 호가를
H0BJASP0 로 등록한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register

# H0BJCNT0(일반채권 실시간체결가) 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_BOND_TRADE_TICK_FIELDS = (
    "STND_ISCD", "BOND_ISNM", "STCK_CNTG_HOUR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "STCK_PRPR", "CNTG_VOL", "STCK_OPRC", "STCK_HGPR",
    "STCK_LWPR", "STCK_PRDY_CLPR", "BOND_CNTG_ERT", "OPRC_ERT", "HGPR_ERT",
    "LWPR_ERT", "ACML_VOL", "PRDY_VOL", "CNTG_TYPE_CLS_CODE",
)

# H0BJASP0(일반채권 실시간호가) 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_BOND_ORDER_BOOK_FIELDS = (
    "STND_ISCD", "STCK_CNTG_HOUR", "ASKP_ERT1", "BIDP_ERT1", "ASKP1",
    "BIDP1", "ASKP_RSQN1", "BIDP_RSQN1", "ASKP_ERT2", "BIDP_ERT2",
    "ASKP2", "BIDP2", "ASKP_RSQN2", "BIDP_RSQN2", "ASKP_ERT3",
    "BIDP_ERT3", "ASKP3", "BIDP3", "ASKP_RSQN3", "BIDP_RSQN3",
    "ASKP_ERT4", "BIDP_ERT4", "ASKP4", "BIDP4", "ASKP_RSQN4",
    "BIDP_RSQN4", "ASKP_ERT5", "BIDP_ERT5", "ASKP5", "BIDP5",
    # ASKP_RSQN52/BIDP_RSQN53 은 레벨5 잔량인데 원장(H0BJASP0)이 RSQN5 가 아니라 RSQN52/53 으로
    # 표기해 그대로 따른다(원장 필드명을 _raw 에 보존). RSQN5 로 "고치지" 말 것 -- 원장과 어긋난다.
    "ASKP_RSQN52", "BIDP_RSQN53", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
)

# H0BICNT0(채권지수 실시간체결가) 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_BOND_INDEX_TICK_FIELDS = (
    "NMIX_ID", "STND_DATE1", "TRNM_HOUR", "TOTL_ERNN_NMIX_OPRC", "TOTL_ERNN_NMIX_HGPR",
    "TOTL_ERNN_NMIX_LWPR", "TOTL_ERNN_NMIX", "PRDY_TOTL_ERNN_NMIX", "TOTL_ERNN_NMIX_PRDY_VRSS", "TOTL_ERNN_NMIX_PRDY_VRSS_SIGN",
    "TOTL_ERNN_NMIX_PRDY_CTRT", "CLEN_PRC_NMIX", "MRKT_PRC_NMIX", "BOND_CALL_RNVS_NMIX", "BOND_ZERO_RNVS_NMIX",
    "BOND_FUTS_THPR", "BOND_AVRG_DRTN_VAL", "BOND_AVRG_CNVX_VAL", "BOND_AVRG_YTM_VAL", "BOND_AVRG_FRDL_YTM_VAL",
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class BondTradeTick:
    """일반채권 실시간 체결(H0BJCNT0). 한 체결 이벤트의 현재가/등락/수익률/거래량 등.

    전체 19개 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만
    타입화한 것이다.
    """

    symbol: str  # 표준종목코드
    name: str  # 채권종목명
    time: str  # HHMMSS
    change_sign: str  # 전일 대비 부호 1상한 2상승 3보합 4하한 5하락
    change: Decimal  # 전일 대비
    change_percent: Decimal  # 전일 대비율
    current_price: Decimal  # 현재가
    trade_volume: Decimal  # 이번 체결 거래량
    open: Decimal  # 시가
    high: Decimal  # 고가
    low: Decimal  # 저가
    previous_close: Decimal  # 전일 종가
    current_yield: Decimal  # 현재 수익률
    accumulated_volume: Decimal  # 누적 거래량
    trade_type_code: str  # 체결 유형 코드
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class BondOrderBook:
    """일반채권 실시간 호가(H0BJASP0). 5호가의 가격/수익률/잔량과 총잔량.

    전체 34개 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만
    타입화한 것이다.
    """

    symbol: str  # 표준종목코드
    time: str  # HHMMSS
    best_ask_price: Decimal  # 매도호가1
    best_bid_price: Decimal  # 매수호가1
    best_ask_yield: Decimal  # 매도호가 수익률1
    best_bid_yield: Decimal  # 매수호가 수익률1
    best_ask_volume: Decimal  # 매도호가 잔량1
    best_bid_volume: Decimal  # 매수호가 잔량1
    total_ask_volume: Decimal  # 총 매도호가 잔량
    total_bid_volume: Decimal  # 총 매수호가 잔량
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class BondIndexTick:
    """채권지수 실시간 체결(H0BICNT0). 총수익지수/순가격지수와 평균 듀레이션/컨벡서티/YTM 등.

    전체 20개 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만
    타입화한 것이다.
    """

    index_id: str  # 지수 ID
    base_date: str  # 기준일자 YYYYMMDD
    time: str  # 전송시간 HHMMSS
    total_return_index: Decimal  # 총수익지수
    previous_total_return_index: Decimal  # 전일 총수익지수
    change: Decimal  # 총수익지수 전일 대비
    change_sign: str  # 총수익지수 전일 대비 부호
    change_percent: Decimal  # 총수익지수 전일 대비율
    open: Decimal  # 총수익지수 시가
    high: Decimal  # 총수익지수 최고가
    low: Decimal  # 총수익지수 최저가
    clean_price_index: Decimal  # 순가격지수
    market_price_index: Decimal  # 시장가격지수
    average_duration: Decimal  # 평균 듀레이션
    average_convexity: Decimal  # 평균 컨벡서티
    average_ytm: Decimal  # 평균 YTM
    average_forward_ytm: Decimal  # 평균 선도 YTM
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
    )


def parse_bond_trade_tick(fields: list[str]) -> BondTradeTick:
    """H0BJCNT0 한 레코드(19필드) -> :class:`BondTradeTick`."""
    raw = MappingProxyType(dict(zip(_BOND_TRADE_TICK_FIELDS, fields, strict=False)))
    return BondTradeTick(
        symbol=raw["STND_ISCD"],
        name=raw["BOND_ISNM"],
        time=raw["STCK_CNTG_HOUR"],
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        current_price=_decimal(raw["STCK_PRPR"]),
        trade_volume=_decimal(raw["CNTG_VOL"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        previous_close=_decimal(raw["STCK_PRDY_CLPR"]),
        current_yield=_decimal(raw["BOND_CNTG_ERT"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        trade_type_code=raw["CNTG_TYPE_CLS_CODE"],
        _raw=raw,
    )


def parse_bond_order_book(fields: list[str]) -> BondOrderBook:
    """H0BJASP0 한 레코드(34필드) -> :class:`BondOrderBook`."""
    raw = MappingProxyType(dict(zip(_BOND_ORDER_BOOK_FIELDS, fields, strict=False)))
    return BondOrderBook(
        symbol=raw["STND_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        best_ask_price=_decimal(raw["ASKP1"]),
        best_bid_price=_decimal(raw["BIDP1"]),
        best_ask_yield=_decimal(raw["ASKP_ERT1"]),
        best_bid_yield=_decimal(raw["BIDP_ERT1"]),
        best_ask_volume=_decimal(raw["ASKP_RSQN1"]),
        best_bid_volume=_decimal(raw["BIDP_RSQN1"]),
        total_ask_volume=_decimal(raw["TOTAL_ASKP_RSQN"]),
        total_bid_volume=_decimal(raw["TOTAL_BIDP_RSQN"]),
        _raw=raw,
    )


def parse_bond_index_tick(fields: list[str]) -> BondIndexTick:
    """H0BICNT0 한 레코드(20필드) -> :class:`BondIndexTick`."""
    raw = MappingProxyType(dict(zip(_BOND_INDEX_TICK_FIELDS, fields, strict=False)))
    return BondIndexTick(
        index_id=raw["NMIX_ID"],
        base_date=raw["STND_DATE1"],
        time=raw["TRNM_HOUR"],
        total_return_index=_decimal(raw["TOTL_ERNN_NMIX"]),
        previous_total_return_index=_decimal(raw["PRDY_TOTL_ERNN_NMIX"]),
        change=_decimal(raw["TOTL_ERNN_NMIX_PRDY_VRSS"]),
        change_sign=raw["TOTL_ERNN_NMIX_PRDY_VRSS_SIGN"],
        change_percent=_decimal(raw["TOTL_ERNN_NMIX_PRDY_CTRT"]),
        open=_decimal(raw["TOTL_ERNN_NMIX_OPRC"]),
        high=_decimal(raw["TOTL_ERNN_NMIX_HGPR"]),
        low=_decimal(raw["TOTL_ERNN_NMIX_LWPR"]),
        clean_price_index=_decimal(raw["CLEN_PRC_NMIX"]),
        market_price_index=_decimal(raw["MRKT_PRC_NMIX"]),
        average_duration=_decimal(raw["BOND_AVRG_DRTN_VAL"]),
        average_convexity=_decimal(raw["BOND_AVRG_CNVX_VAL"]),
        average_ytm=_decimal(raw["BOND_AVRG_YTM_VAL"]),
        average_forward_ytm=_decimal(raw["BOND_AVRG_FRDL_YTM_VAL"]),
        _raw=raw,
    )


register(TRSpec("H0BJCNT0", field_count=len(_BOND_TRADE_TICK_FIELDS), parser=parse_bond_trade_tick))
register(TRSpec("H0BJASP0", field_count=len(_BOND_ORDER_BOOK_FIELDS), parser=parse_bond_order_book))
register(TRSpec("H0BICNT0", field_count=len(_BOND_INDEX_TICK_FIELDS), parser=parse_bond_index_tick))

"""파생상품(선물/옵션) 실시간 파서 -- 체결가/호가/예상체결/체결통보.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 각 레이아웃마다
Element 이름 튜플 상수를 두고, 동일 레이아웃을 공유하는 TR 은 파서를 재사용한다. 엔티티는 자주
쓰는 헤드라인 필드만 타입화하고, 원장의 모든 Element->값은 ``_raw`` 에 담는다.

레이아웃 공유:
- 선물 5호가: 지수/상품/야간선물 호가(H0IFASP0/H0CFASP0/H0MFASP0)가 38필드 동일.
- 옵션 5호가: 지수/야간옵션 호가(H0IOASP0/H0EUASP0)가 38필드 동일.
- 선물 체결가: 지수/상품선물 체결(H0IFCNT0/H0CFCNT0)이 50필드 동일.

통보류(체결통보, ``encrypted=True``)는 연결 계층이 평문으로 복호화한 뒤 파서에 넘긴다. 즉 파서는
평문 필드를 매핑하고, 프레임 암호화 여부는 :class:`TRSpec` 의 ``encrypted`` 로만 표시한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register

_RAW_FIELD = field(  # 모든 엔티티가 공유하는 원본 매핑 필드 정의.
    default_factory=lambda: MappingProxyType({}), compare=False, hash=False, repr=False
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


# --------------------------------------------------------------------------- 호가(order book)


@dataclass(frozen=True, slots=True)
class OrderBook:
    """파생상품 실시간 호가. 선물/옵션 매도·매수 최우선 호가와 총잔량.

    선물 5호가/옵션 5호가/주식옵션 10호가/주식선물 10호가가 모두 이 엔티티로 매핑된다. 전체 호가
    단계(2~10호가, 건수 등)는 ``_raw`` 에 있고, 아래는 최우선(1호가)과 총잔량만 타입화한 것이다.
    """

    symbol: str
    time: str  # HHMMSS
    best_ask: Decimal
    best_bid: Decimal
    best_ask_quantity: Decimal
    best_bid_quantity: Decimal
    total_ask_quantity: Decimal
    total_bid_quantity: Decimal
    _raw: Mapping[str, Any] = _RAW_FIELD


def _order_book(fields: list[str], layout: tuple[str, ...], *, symbol: str, ask: str, bid: str) -> OrderBook:
    """호가 레코드 -> :class:`OrderBook`. ``symbol``/``ask``/``bid`` 로 레이아웃별 키를 지정."""
    raw = MappingProxyType(dict(zip(layout, fields, strict=False)))
    return OrderBook(
        symbol=raw[symbol],
        time=raw["BSOP_HOUR"],
        best_ask=_decimal(raw[ask]),
        best_bid=_decimal(raw[bid]),
        best_ask_quantity=_decimal(raw["ASKP_RSQN1"]),
        best_bid_quantity=_decimal(raw["BIDP_RSQN1"]),
        total_ask_quantity=_decimal(raw["TOTAL_ASKP_RSQN"]),
        total_bid_quantity=_decimal(raw["TOTAL_BIDP_RSQN"]),
        _raw=raw,
    )


_FUTURES_ORDER_BOOK_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "FUTS_ASKP1", "FUTS_ASKP2", "FUTS_ASKP3", "FUTS_ASKP4",
    "FUTS_ASKP5", "FUTS_BIDP1", "FUTS_BIDP2", "FUTS_BIDP3", "FUTS_BIDP4", "FUTS_BIDP5",
    "ASKP_CSNU1", "ASKP_CSNU2", "ASKP_CSNU3", "ASKP_CSNU4", "ASKP_CSNU5", "BIDP_CSNU1",
    "BIDP_CSNU2", "BIDP_CSNU3", "BIDP_CSNU4", "BIDP_CSNU5", "ASKP_RSQN1", "ASKP_RSQN2",
    "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5", "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3",
    "BIDP_RSQN4", "BIDP_RSQN5", "TOTAL_ASKP_CSNU", "TOTAL_BIDP_CSNU", "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
)

_OPTION_ORDER_BOOK_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "OPTN_ASKP1", "OPTN_ASKP2", "OPTN_ASKP3", "OPTN_ASKP4",
    "OPTN_ASKP5", "OPTN_BIDP1", "OPTN_BIDP2", "OPTN_BIDP3", "OPTN_BIDP4", "OPTN_BIDP5",
    "ASKP_CSNU1", "ASKP_CSNU2", "ASKP_CSNU3", "ASKP_CSNU4", "ASKP_CSNU5", "BIDP_CSNU1",
    "BIDP_CSNU2", "BIDP_CSNU3", "BIDP_CSNU4", "BIDP_CSNU5", "ASKP_RSQN1", "ASKP_RSQN2",
    "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5", "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3",
    "BIDP_RSQN4", "BIDP_RSQN5", "TOTAL_ASKP_CSNU", "TOTAL_BIDP_CSNU", "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
)

_STOCK_OPTION_ORDER_BOOK_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "OPTN_ASKP1", "OPTN_ASKP2", "OPTN_ASKP3", "OPTN_ASKP4",
    "OPTN_ASKP5", "OPTN_BIDP1", "OPTN_BIDP2", "OPTN_BIDP3", "OPTN_BIDP4", "OPTN_BIDP5",
    "ASKP_CSNU1", "ASKP_CSNU2", "ASKP_CSNU3", "ASKP_CSNU4", "ASKP_CSNU5", "BIDP_CSNU1",
    "BIDP_CSNU2", "BIDP_CSNU3", "BIDP_CSNU4", "BIDP_CSNU5", "ASKP_RSQN1", "ASKP_RSQN2",
    "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5", "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3",
    "BIDP_RSQN4", "BIDP_RSQN5", "TOTAL_ASKP_CSNU", "TOTAL_BIDP_CSNU", "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC", "OPTN_ASKP6",
    "OPTN_ASKP7", "OPTN_ASKP8", "OPTN_ASKP9", "OPTN_ASKP10", "OPTN_BIDP6", "OPTN_BIDP7",
    "OPTN_BIDP8", "OPTN_BIDP9", "OPTN_BIDP10", "ASKP_CSNU6", "ASKP_CSNU7", "ASKP_CSNU8",
    "ASKP_CSNU9", "ASKP_CSNU10", "BIDP_CSNU6", "BIDP_CSNU7", "BIDP_CSNU8", "BIDP_CSNU9",
    "BIDP_CSNU10", "ASKP_RSQN6", "ASKP_RSQN7", "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10",
    "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9", "BIDP_RSQN10",
)

_STOCK_FUTURES_ORDER_BOOK_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "ASKP1", "ASKP2", "ASKP3", "ASKP4", "ASKP5", "ASKP6",
    "ASKP7", "ASKP8", "ASKP9", "ASKP10", "BIDP1", "BIDP2", "BIDP3", "BIDP4", "BIDP5", "BIDP6",
    "BIDP7", "BIDP8", "BIDP9", "BIDP10", "ASKP_CSNU1", "ASKP_CSNU2", "ASKP_CSNU3",
    "ASKP_CSNU4", "ASKP_CSNU5", "ASKP_CSNU6", "ASKP_CSNU7", "ASKP_CSNU8", "ASKP_CSNU9",
    "ASKP_CSNU10", "BIDP_CSNU1", "BIDP_CSNU2", "BIDP_CSNU3", "BIDP_CSNU4", "BIDP_CSNU5",
    "BIDP_CSNU6", "BIDP_CSNU7", "BIDP_CSNU8", "BIDP_CSNU9", "BIDP_CSNU10", "ASKP_RSQN1",
    "ASKP_RSQN2", "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5", "ASKP_RSQN6", "ASKP_RSQN7",
    "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10", "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3",
    "BIDP_RSQN4", "BIDP_RSQN5", "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9",
    "BIDP_RSQN10", "TOTAL_ASKP_CSNU", "TOTAL_BIDP_CSNU", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
)


def parse_futures_order_book(fields: list[str]) -> OrderBook:
    """선물 5호가(H0IFASP0/H0CFASP0/H0MFASP0, 38필드) -> :class:`OrderBook`."""
    return _order_book(
        fields, _FUTURES_ORDER_BOOK_FIELDS, symbol="FUTS_SHRN_ISCD", ask="FUTS_ASKP1", bid="FUTS_BIDP1"
    )


def parse_option_order_book(fields: list[str]) -> OrderBook:
    """옵션 5호가(H0IOASP0/H0EUASP0, 38필드) -> :class:`OrderBook`."""
    return _order_book(
        fields, _OPTION_ORDER_BOOK_FIELDS, symbol="OPTN_SHRN_ISCD", ask="OPTN_ASKP1", bid="OPTN_BIDP1"
    )


def parse_stock_option_order_book(fields: list[str]) -> OrderBook:
    """주식옵션 10호가(H0ZOASP0, 68필드) -> :class:`OrderBook`."""
    return _order_book(
        fields, _STOCK_OPTION_ORDER_BOOK_FIELDS, symbol="OPTN_SHRN_ISCD", ask="OPTN_ASKP1", bid="OPTN_BIDP1"
    )


def parse_stock_futures_order_book(fields: list[str]) -> OrderBook:
    """주식선물 10호가(H0ZFASP0, 68필드) -> :class:`OrderBook`."""
    return _order_book(
        fields, _STOCK_FUTURES_ORDER_BOOK_FIELDS, symbol="FUTS_SHRN_ISCD", ask="ASKP1", bid="BIDP1"
    )


# --------------------------------------------------------------------------- 선물 체결가(tick)


@dataclass(frozen=True, slots=True)
class FuturesTick:
    """선물 실시간 체결(틱). 현재가/등락/거래량/이론가/베이시스/미결제약정/최우선호가.

    지수·상품·야간·주식선물 체결이 이 엔티티로 매핑된다. 전체 필드는 ``_raw`` 에 있고, 아래는 자주
    쓰는 헤드라인만 타입화한 것이다.
    """

    symbol: str
    time: str  # HHMMSS
    current_price: Decimal
    change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    trade_volume: Decimal  # 최종(이번) 체결 수량
    accumulated_volume: Decimal
    accumulated_value: Decimal
    theoretical_price: Decimal  # HTS 이론가
    market_basis: Decimal
    conclusion_strength: Decimal  # 체결강도
    open_interest: Decimal  # 미결제약정 수량
    best_ask: Decimal
    best_bid: Decimal
    _raw: Mapping[str, Any] = _RAW_FIELD


def _futures_tick(
    fields: list[str],
    layout: tuple[str, ...],
    *,
    price: str,
    change: str,
    open_: str,
    high: str,
    low: str,
    ask: str,
    bid: str,
) -> FuturesTick:
    """선물 체결 레코드 -> :class:`FuturesTick`. 지수/상품선물과 주식선물의 키 차이를 인자로 흡수."""
    raw = MappingProxyType(dict(zip(layout, fields, strict=False)))
    return FuturesTick(
        symbol=raw["FUTS_SHRN_ISCD"],
        time=raw["BSOP_HOUR"],
        current_price=_decimal(raw[price]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw[change]),
        change_percent=_decimal(raw["FUTS_PRDY_CTRT"]),
        open=_decimal(raw[open_]),
        high=_decimal(raw[high]),
        low=_decimal(raw[low]),
        trade_volume=_decimal(raw["LAST_CNQN"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        theoretical_price=_decimal(raw["HTS_THPR"]),
        market_basis=_decimal(raw["MRKT_BASIS"]),
        conclusion_strength=_decimal(raw["CTTR"]),
        open_interest=_decimal(raw["HTS_OTST_STPL_QTY"]),
        best_ask=_decimal(raw[ask]),
        best_bid=_decimal(raw[bid]),
        _raw=raw,
    )


_FUTURES_TICK_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "FUTS_PRDY_VRSS", "PRDY_VRSS_SIGN", "FUTS_PRDY_CTRT",
    "FUTS_PRPR", "FUTS_OPRC", "FUTS_HGPR", "FUTS_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "MRKT_BASIS", "DPRT", "NMSC_FCTN_STPL_PRC",
    "FMSC_FCTN_STPL_PRC", "SPEAD_PRC", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_NMIX_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_NMIX_PRPR",
    "SHNU_RATE", "CTTR", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "FUTS_ASKP1",
    "FUTS_BIDP1", "ASKP_RSQN1", "BIDP_RSQN1", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "DSCS_BLTR_ACML_QTY", "DYNM_MXPR", "DYNM_LLAM",
    "DYNM_PRC_LIMT_YN",
)

_NIGHT_FUTURES_TICK_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "FUTS_PRDY_VRSS", "PRDY_VRSS_SIGN", "FUTS_PRDY_CTRT",
    "FUTS_PRPR", "FUTS_OPRC", "FUTS_HGPR", "FUTS_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "MRKT_BASIS", "DPRT", "NMSC_FCTN_STPL_PRC",
    "FMSC_FCTN_STPL_PRC", "SPEAD_PRC", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_NMIX_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_NMIX_PRPR",
    "SHNU_RATE", "CTTR", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "FUTS_ASKP1",
    "FUTS_BIDP1", "ASKP_RSQN1", "BIDP_RSQN1", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "DYNM_MXPR", "DYNM_LLAM", "DYNM_PRC_LIMT_YN",
)

_STOCK_FUTURES_TICK_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "FUTS_PRDY_CTRT", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "MRKT_BASIS", "DPRT", "NMSC_FCTN_STPL_PRC",
    "FMSC_FCTN_STPL_PRC", "SPEAD_PRC", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "SHNU_RATE",
    "CTTR", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "ASKP1", "BIDP1", "ASKP_RSQN1",
    "BIDP_RSQN1", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "DYNM_MXPR", "DYNM_LLAM", "DYNM_PRC_LIMT_YN",
)


def parse_futures_tick(fields: list[str]) -> FuturesTick:
    """지수/상품선물 체결가(H0IFCNT0/H0CFCNT0, 50필드) -> :class:`FuturesTick`."""
    return _futures_tick(
        fields, _FUTURES_TICK_FIELDS,
        price="FUTS_PRPR", change="FUTS_PRDY_VRSS", open_="FUTS_OPRC", high="FUTS_HGPR",
        low="FUTS_LWPR", ask="FUTS_ASKP1", bid="FUTS_BIDP1",
    )


def parse_night_futures_tick(fields: list[str]) -> FuturesTick:
    """KRX야간선물 체결가(H0MFCNT0, 49필드) -> :class:`FuturesTick`."""
    return _futures_tick(
        fields, _NIGHT_FUTURES_TICK_FIELDS,
        price="FUTS_PRPR", change="FUTS_PRDY_VRSS", open_="FUTS_OPRC", high="FUTS_HGPR",
        low="FUTS_LWPR", ask="FUTS_ASKP1", bid="FUTS_BIDP1",
    )


def parse_stock_futures_tick(fields: list[str]) -> FuturesTick:
    """주식선물 체결가(H0ZFCNT0, 49필드) -> :class:`FuturesTick`. 가격은 주식(STCK_) 키를 쓴다."""
    return _futures_tick(
        fields, _STOCK_FUTURES_TICK_FIELDS,
        price="STCK_PRPR", change="PRDY_VRSS", open_="STCK_OPRC", high="STCK_HGPR",
        low="STCK_LWPR", ask="ASKP1", bid="BIDP1",
    )


# --------------------------------------------------------------------------- 옵션 체결가(tick)


@dataclass(frozen=True, slots=True)
class OptionTick:
    """옵션 실시간 체결(틱). 현재가/등락/거래량 + 그릭스(델타/감마/베가/세타/로우)/내재변동성.

    지수·야간·주식옵션 체결이 이 엔티티로 매핑된다. 전체 필드는 ``_raw`` 에 있고, 아래는 자주 쓰는
    헤드라인만 타입화한 것이다.
    """

    symbol: str
    time: str  # HHMMSS
    current_price: Decimal
    change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    trade_volume: Decimal  # 최종(이번) 체결 수량
    accumulated_volume: Decimal
    accumulated_value: Decimal
    theoretical_price: Decimal  # HTS 이론가
    open_interest: Decimal  # 미결제약정 수량
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    theta: Decimal
    rho: Decimal
    implied_volatility: Decimal  # HTS 내재변동성
    conclusion_strength: Decimal  # 체결강도
    best_ask: Decimal
    best_bid: Decimal
    _raw: Mapping[str, Any] = _RAW_FIELD


def _option_tick(fields: list[str], layout: tuple[str, ...]) -> OptionTick:
    """옵션 체결 레코드 -> :class:`OptionTick`. 세 옵션 체결 레이아웃이 헤드라인 키를 공유한다."""
    raw = MappingProxyType(dict(zip(layout, fields, strict=False)))
    return OptionTick(
        symbol=raw["OPTN_SHRN_ISCD"],
        time=raw["BSOP_HOUR"],
        current_price=_decimal(raw["OPTN_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["OPTN_PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["OPTN_OPRC"]),
        high=_decimal(raw["OPTN_HGPR"]),
        low=_decimal(raw["OPTN_LWPR"]),
        trade_volume=_decimal(raw["LAST_CNQN"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        theoretical_price=_decimal(raw["HTS_THPR"]),
        open_interest=_decimal(raw["HTS_OTST_STPL_QTY"]),
        delta=_decimal(raw["DELTA"]),
        gamma=_decimal(raw["GAMA"]),
        vega=_decimal(raw["VEGA"]),
        theta=_decimal(raw["THETA"]),
        rho=_decimal(raw["RHO"]),
        implied_volatility=_decimal(raw["HTS_INTS_VLTL"]),
        conclusion_strength=_decimal(raw["CTTR"]),
        best_ask=_decimal(raw["OPTN_ASKP1"]),
        best_bid=_decimal(raw["OPTN_BIDP1"]),
        _raw=raw,
    )


_NIGHT_OPTION_TICK_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "OPTN_PRPR", "PRDY_VRSS_SIGN", "OPTN_PRDY_VRSS",
    "PRDY_CTRT", "OPTN_OPRC", "OPTN_HGPR", "OPTN_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_NMIX_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_NMIX_PRPR",
    "SHNU_RATE", "PRMM_VAL", "INVL_VAL", "TMVL_VAL", "DELTA", "GAMA", "VEGA", "THETA", "RHO",
    "HTS_INTS_VLTL", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "UNAS_HIST_VLTL", "CTTR",
    "DPRT", "MRKT_BASIS", "OPTN_ASKP1", "OPTN_BIDP1", "ASKP_RSQN1", "BIDP_RSQN1",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "PRDY_VOL_VRSS_ACML_VOL_RATE", "DYNM_MXPR",
    "DYNM_PRC_LIMT_YN", "DYNM_LLAM",
)

_INDEX_OPTION_TICK_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "OPTN_PRPR", "PRDY_VRSS_SIGN", "OPTN_PRDY_VRSS",
    "PRDY_CTRT", "OPTN_OPRC", "OPTN_HGPR", "OPTN_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_NMIX_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_NMIX_PRPR",
    "SHNU_RATE", "PRMM_VAL", "INVL_VAL", "TMVL_VAL", "DELTA", "GAMA", "VEGA", "THETA", "RHO",
    "HTS_INTS_VLTL", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "UNAS_HIST_VLTL", "CTTR",
    "DPRT", "MRKT_BASIS", "OPTN_ASKP1", "OPTN_BIDP1", "ASKP_RSQN1", "BIDP_RSQN1",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "PRDY_VOL_VRSS_ACML_VOL_RATE", "AVRG_VLTL",
    "DSCS_LRQN_VOL", "DYNM_MXPR", "DYNM_LLAM", "DYNM_PRC_LIMT_YN",
)

_STOCK_OPTION_TICK_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "OPTN_PRPR", "PRDY_VRSS_SIGN", "OPTN_PRDY_VRSS",
    "PRDY_CTRT", "OPTN_OPRC", "OPTN_HGPR", "OPTN_LWPR", "LAST_CNQN", "ACML_VOL",
    "ACML_TR_PBMN", "HTS_THPR", "HTS_OTST_STPL_QTY", "OTST_STPL_QTY_ICDC", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_NMIX_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_NMIX_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_NMIX_PRPR",
    "SHNU_RATE", "PRMM_VAL", "INVL_VAL", "TMVL_VAL", "DELTA", "GAMA", "VEGA", "THETA", "RHO",
    "HTS_INTS_VLTL", "ESDG", "OTST_STPL_RGBF_QTY_ICDC", "THPR_BASIS", "UNAS_HIST_VLTL", "CTTR",
    "DPRT", "MRKT_BASIS", "OPTN_ASKP1", "OPTN_BIDP1", "ASKP_RSQN1", "BIDP_RSQN1",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "PRDY_VOL_VRSS_ACML_VOL_RATE",
)


def parse_night_option_tick(fields: list[str]) -> OptionTick:
    """KRX야간옵션 체결가(H0EUCNT0, 56필드) -> :class:`OptionTick`."""
    return _option_tick(fields, _NIGHT_OPTION_TICK_FIELDS)


def parse_index_option_tick(fields: list[str]) -> OptionTick:
    """지수옵션 체결가(H0IOCNT0, 58필드) -> :class:`OptionTick`."""
    return _option_tick(fields, _INDEX_OPTION_TICK_FIELDS)


def parse_stock_option_tick(fields: list[str]) -> OptionTick:
    """주식옵션 체결가(H0ZOCNT0, 53필드) -> :class:`OptionTick`."""
    return _option_tick(fields, _STOCK_OPTION_TICK_FIELDS)


# --------------------------------------------------------------------------- 예상체결(expected)


@dataclass(frozen=True, slots=True)
class ExpectedConclusion:
    """실시간 예상체결. 장 마감/동시호가 구간의 예상 체결가/대비/예상 수량.

    선물/옵션 예상체결이 이 엔티티로 매핑된다. 주식옵션 예상체결(H0ZOANC0)은 예상수량이 없어
    ``expected_volume`` 이 0 이 된다.
    """

    symbol: str
    time: str  # HHMMSS
    expected_price: Decimal
    expected_change: Decimal
    expected_change_sign: str  # 1상한 2상승 3보합 4하한 5하락
    expected_change_percent: Decimal
    market_operation_code: str  # 예상장운영구분코드
    expected_volume: Decimal
    _raw: Mapping[str, Any] = _RAW_FIELD


def _expected_conclusion(fields: list[str], layout: tuple[str, ...], *, symbol: str) -> ExpectedConclusion:
    """예상체결 레코드 -> :class:`ExpectedConclusion`. 종목코드 키(선물/옵션)를 인자로 지정."""
    raw = MappingProxyType(dict(zip(layout, fields, strict=False)))
    return ExpectedConclusion(
        symbol=raw[symbol],
        time=raw["BSOP_HOUR"],
        expected_price=_decimal(raw["ANTC_CNPR"]),
        expected_change=_decimal(raw["ANTC_CNTG_VRSS"]),
        expected_change_sign=raw["ANTC_CNTG_VRSS_SIGN"],
        expected_change_percent=_decimal(raw["ANTC_CNTG_PRDY_CTRT"]),
        market_operation_code=raw["ANTC_MKOP_CLS_CODE"],
        expected_volume=_decimal(raw.get("ANTC_CNQN", "")),
        _raw=raw,
    )


_NIGHT_OPTION_EXPECTED_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "ANTC_CNPR", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ANTC_MKOP_CLS_CODE", "ANTC_CNQN",
)

_STOCK_FUTURES_EXPECTED_FIELDS = (
    "FUTS_SHRN_ISCD", "BSOP_HOUR", "ANTC_CNPR", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ANTC_MKOP_CLS_CODE", "ANTC_CNQN",
)

_STOCK_OPTION_EXPECTED_FIELDS = (
    "OPTN_SHRN_ISCD", "BSOP_HOUR", "ANTC_CNPR", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ANTC_MKOP_CLS_CODE",
)


def parse_night_option_expected(fields: list[str]) -> ExpectedConclusion:
    """KRX야간옵션 예상체결(H0EUANC0, 8필드) -> :class:`ExpectedConclusion`."""
    return _expected_conclusion(fields, _NIGHT_OPTION_EXPECTED_FIELDS, symbol="OPTN_SHRN_ISCD")


def parse_stock_futures_expected(fields: list[str]) -> ExpectedConclusion:
    """주식선물 예상체결(H0ZFANC0, 8필드) -> :class:`ExpectedConclusion`."""
    return _expected_conclusion(fields, _STOCK_FUTURES_EXPECTED_FIELDS, symbol="FUTS_SHRN_ISCD")


def parse_stock_option_expected(fields: list[str]) -> ExpectedConclusion:
    """주식옵션 예상체결(H0ZOANC0, 7필드) -> :class:`ExpectedConclusion`. 예상수량 없음."""
    return _expected_conclusion(fields, _STOCK_OPTION_EXPECTED_FIELDS, symbol="OPTN_SHRN_ISCD")


# --------------------------------------------------------------------------- 체결통보(notice, 암호화)


@dataclass(frozen=True, slots=True)
class ExecutionNotice:
    """선물옵션 실시간 체결통보. 내 주문의 체결/접수/거부 통보(암호화 프레임을 복호화한 결과).

    선물옵션(H0IFCNI0)과 KRX야간(H0MFCNI0) 체결통보가 이 엔티티로 매핑된다. 야간 통보에는
    주문가격(``order_price``)이 없어 0 이 된다. 전체 필드는 ``_raw`` 에 있다.
    """

    customer_id: str
    account_number: str
    order_number: str
    original_order_number: str
    sell_buy: str  # 매도매수구분 01매도 02매수
    symbol: str
    filled_quantity: Decimal
    filled_price: Decimal
    time: str  # HHMMSS
    rejected: bool  # 거부여부
    fill_status: str  # 체결여부(1주문 2체결)
    accepted: bool  # 접수여부
    order_quantity: Decimal
    symbol_name: str  # 체결종목명
    account_name: str
    order_price: Decimal
    _raw: Mapping[str, Any] = _RAW_FIELD


def _execution_notice(fields: list[str], layout: tuple[str, ...]) -> ExecutionNotice:
    """체결통보 레코드(평문) -> :class:`ExecutionNotice`."""
    raw = MappingProxyType(dict(zip(layout, fields, strict=False)))
    return ExecutionNotice(
        customer_id=raw["CUST_ID"],
        account_number=raw["ACNT_NO"],
        order_number=raw["ODER_NO"],
        original_order_number=raw["OODER_NO"],
        sell_buy=raw["SELN_BYOV_CLS"],
        symbol=raw["STCK_SHRN_ISCD"],
        filled_quantity=_decimal(raw["CNTG_QTY"]),
        filled_price=_decimal(raw["CNTG_UNPR"]),
        time=raw["STCK_CNTG_HOUR"],
        rejected=raw["RFUS_YN"] == "Y",
        fill_status=raw["CNTG_YN"],
        accepted=raw["ACPT_YN"] == "Y",
        order_quantity=_decimal(raw["ODER_QTY"]),
        symbol_name=raw["CNTG_ISNM"],
        account_name=raw["ACNT_NAME"],
        order_price=_decimal(raw.get("ORDER_PRC", "")),
        _raw=raw,
    )


_EXECUTION_NOTICE_FIELDS = (
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS", "ODER_KIND2",
    "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR", "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN",
    "ACPT_YN", "BRNC_NO", "ODER_QTY", "ACNT_NAME", "CNTG_ISNM", "ODER_COND", "ORD_GRP",
    "ORD_GRPSEQ", "ORDER_PRC",
)

_NIGHT_EXECUTION_NOTICE_FIELDS = (
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS", "ODER_KIND2",
    "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR", "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN",
    "ACPT_YN", "BRNC_NO", "ODER_QTY", "ACNT_NAME", "CNTG_ISNM", "ODER_COND",
)


def parse_execution_notice(fields: list[str]) -> ExecutionNotice:
    """선물옵션 체결통보(H0IFCNI0, 22필드, 평문) -> :class:`ExecutionNotice`."""
    return _execution_notice(fields, _EXECUTION_NOTICE_FIELDS)


def parse_night_execution_notice(fields: list[str]) -> ExecutionNotice:
    """KRX야간 선물/옵션 체결통보(H0MFCNI0, 19필드, 평문) -> :class:`ExecutionNotice`."""
    return _execution_notice(fields, _NIGHT_EXECUTION_NOTICE_FIELDS)


# --------------------------------------------------------------------------- 레지스트리 등록

# 선물 5호가는 지수/상품/야간선물이 동일 레이아웃 -> 파서 공유.
for _tr_id in ("H0IFASP0", "H0CFASP0", "H0MFASP0"):
    register(TRSpec(_tr_id, field_count=len(_FUTURES_ORDER_BOOK_FIELDS), parser=parse_futures_order_book))

# 옵션 5호가는 지수/야간옵션이 동일 레이아웃 -> 파서 공유.
for _tr_id in ("H0IOASP0", "H0EUASP0"):
    register(TRSpec(_tr_id, field_count=len(_OPTION_ORDER_BOOK_FIELDS), parser=parse_option_order_book))

register(TRSpec("H0ZOASP0", field_count=len(_STOCK_OPTION_ORDER_BOOK_FIELDS), parser=parse_stock_option_order_book))
register(TRSpec("H0ZFASP0", field_count=len(_STOCK_FUTURES_ORDER_BOOK_FIELDS), parser=parse_stock_futures_order_book))

# 선물 체결가는 지수/상품선물이 동일 레이아웃 -> 파서 공유.
for _tr_id in ("H0IFCNT0", "H0CFCNT0"):
    register(TRSpec(_tr_id, field_count=len(_FUTURES_TICK_FIELDS), parser=parse_futures_tick))

register(TRSpec("H0MFCNT0", field_count=len(_NIGHT_FUTURES_TICK_FIELDS), parser=parse_night_futures_tick))
register(TRSpec("H0ZFCNT0", field_count=len(_STOCK_FUTURES_TICK_FIELDS), parser=parse_stock_futures_tick))

register(TRSpec("H0EUCNT0", field_count=len(_NIGHT_OPTION_TICK_FIELDS), parser=parse_night_option_tick))
register(TRSpec("H0IOCNT0", field_count=len(_INDEX_OPTION_TICK_FIELDS), parser=parse_index_option_tick))
register(TRSpec("H0ZOCNT0", field_count=len(_STOCK_OPTION_TICK_FIELDS), parser=parse_stock_option_tick))

register(TRSpec("H0EUANC0", field_count=len(_NIGHT_OPTION_EXPECTED_FIELDS), parser=parse_night_option_expected))
register(TRSpec("H0ZFANC0", field_count=len(_STOCK_FUTURES_EXPECTED_FIELDS), parser=parse_stock_futures_expected))
register(TRSpec("H0ZOANC0", field_count=len(_STOCK_OPTION_EXPECTED_FIELDS), parser=parse_stock_option_expected))

register(TRSpec("H0IFCNI0", field_count=len(_EXECUTION_NOTICE_FIELDS), parser=parse_execution_notice, encrypted=True))
register(TRSpec("H0MFCNI0", field_count=len(_NIGHT_EXECUTION_NOTICE_FIELDS), parser=parse_night_execution_notice, encrypted=True))

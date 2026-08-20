"""해외 자산군 실시간 파서 -- 해외주식/해외선물옵션.

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 각 TR 은 레이아웃이
서로 달라 파서를 공유하지 않는다. 통보류(체결통보/주문내역통보)는 프레임이 암호화되지만, 연결
계층이 파서 호출 전에 복호화하므로 파서는 평문 필드를 그대로 매핑한다(``encrypted=True`` 는
레지스트리 참고/검증용).

엔티티는 REST 결과 타입과 같은 관례를 따른다: 공개 식별자는 산업표준 영어, 설명은 한국어
docstring, 헤드라인 필드만 타입화하고 원장 전체(Element 이름->원문)는 ``_raw`` 에 담는다.
원장이 숫자 필드도 ``string`` 으로 표기하므로, 가격/수량/거래량/금액/등락률 같은 헤드라인은
도메인 판단으로 :class:`~decimal.Decimal` 화하고 코드/일자/시각/식별자/플래그는 문자열로 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .._registry import TRSpec, register

_EMPTY_RAW: Mapping[str, Any] = MappingProxyType({})


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _raw_field() -> Any:
    """모든 엔티티가 공유하는 ``_raw`` dataclass 필드 정의(원장 전체 Element->원문)."""
    return field(default_factory=lambda: _EMPTY_RAW, compare=False, hash=False, repr=False)


# ---------------------------------------------------------------------------
# 해외주식 실시간호가 (HDFSASP0)
# ---------------------------------------------------------------------------

# 원장 Response Body 필드 순서. 인덱스 = ^ 위치. (원장에 레벨3 블록이 중복 추출되어 있어
# 그대로 보존한다 -- 필드순이 정본. 중복 키는 ``_raw`` dict 에서 마지막 값으로 접힌다.)
_ORDERBOOK_FIELDS = (
    "RSYM", "SYMB", "ZDIV", "XYMD", "XHMS", "KYMD", "KHMS", "BVOL", "AVOL", "BDVL", "ADVL",
    "PBID1", "PASK1", "VBID1", "VASK1", "DBID1", "DASK1",
    "PBID2", "PASK2", "VBID2", "VASK2", "DBID2", "DASK2",
    "PBID3", "PASK3", "VBID3", "VASK3", "DBID3", "DASK3",
    "PBID3", "PASK3", "VBID3", "VASK3", "DBID3", "DASK3",
    "PBID4", "PASK4", "VBID4", "VASK4", "DBID4", "DASK4",
    "PBID5", "PASK5", "VBID5", "VASK5", "DBID5", "DASK5",
    "PBID6", "PASK6", "VBID6", "VASK6", "DBID6", "DASK6",
    "PBID7", "PASK7", "VBID7", "VASK7", "DBID7", "DASK7",
    "PBID8", "PASK8", "VBID8", "VASK8", "DBID8", "DASK8",
    "PBID9", "PASK9", "VBID9", "VASK9", "DBID9", "DASK9",
    "PBID10", "PASK10", "VBID10", "VASK10", "DBID10", "DASK10",
)


@dataclass(frozen=True, slots=True)
class OrderBook:
    """해외주식 실시간호가(HDFSASP0). 10단계 매수/매도 호가·잔량 스냅샷.

    전체 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 자주 쓰는 헤드라인만 타입화한 것.
    """

    symbol: str
    realtime_symbol: str
    decimal_places: str
    local_date: str
    local_time: str
    korea_date: str
    korea_time: str
    total_bid_quantity: Decimal
    total_ask_quantity: Decimal
    best_bid: Decimal
    best_ask: Decimal
    best_bid_quantity: Decimal
    best_ask_quantity: Decimal
    _raw: Mapping[str, Any] = _raw_field()


def parse_orderbook(fields: list[str]) -> OrderBook:
    """HDFSASP0 한 레코드 -> :class:`OrderBook`."""
    raw = MappingProxyType(dict(zip(_ORDERBOOK_FIELDS, fields, strict=False)))
    return OrderBook(
        symbol=raw["SYMB"],
        realtime_symbol=raw["RSYM"],
        decimal_places=raw["ZDIV"],
        local_date=raw["XYMD"],
        local_time=raw["XHMS"],
        korea_date=raw["KYMD"],
        korea_time=raw["KHMS"],
        total_bid_quantity=_decimal(raw["BVOL"]),
        total_ask_quantity=_decimal(raw["AVOL"]),
        best_bid=_decimal(raw["PBID1"]),
        best_ask=_decimal(raw["PASK1"]),
        best_bid_quantity=_decimal(raw["VBID1"]),
        best_ask_quantity=_decimal(raw["VASK1"]),
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외주식 실시간지연체결가 (HDFSCNT0)
# ---------------------------------------------------------------------------

_DELAYED_TRADE_FIELDS = (
    "RSYM", "SYMB", "ZDIV", "TYMD", "XYMD", "XHMS", "KYMD", "KHMS",
    "OPEN", "HIGH", "LOW", "LAST", "SIGN", "DIFF", "RATE",
    "PBID", "PASK", "VBID", "VASK", "EVOL", "TVOL", "TAMT",
    "BIVL", "ASVL", "STRN", "MTYP",
)


@dataclass(frozen=True, slots=True)
class DelayedTradeTick:
    """해외주식 실시간지연체결가(HDFSCNT0). 한 체결 이벤트의 시/고/저/현재가·거래량·체결강도."""

    symbol: str
    local_date: str
    local_time: str
    korea_date: str
    korea_time: str
    open: Decimal
    high: Decimal
    low: Decimal
    current_price: Decimal
    change_sign: str
    change: Decimal
    change_percent: Decimal
    best_bid: Decimal
    best_ask: Decimal
    trade_volume: Decimal  # 이번 체결량(EVOL)
    accumulated_volume: Decimal
    accumulated_value: Decimal
    conclusion_strength: Decimal
    market_type: str  # 1:장중 2:장전 3:장후
    _raw: Mapping[str, Any] = _raw_field()


def parse_delayed_trade_tick(fields: list[str]) -> DelayedTradeTick:
    """HDFSCNT0 한 레코드 -> :class:`DelayedTradeTick`."""
    raw = MappingProxyType(dict(zip(_DELAYED_TRADE_FIELDS, fields, strict=False)))
    return DelayedTradeTick(
        symbol=raw["SYMB"],
        local_date=raw["XYMD"],
        local_time=raw["XHMS"],
        korea_date=raw["KYMD"],
        korea_time=raw["KHMS"],
        open=_decimal(raw["OPEN"]),
        high=_decimal(raw["HIGH"]),
        low=_decimal(raw["LOW"]),
        current_price=_decimal(raw["LAST"]),
        change_sign=raw["SIGN"],
        change=_decimal(raw["DIFF"]),
        change_percent=_decimal(raw["RATE"]),
        best_bid=_decimal(raw["PBID"]),
        best_ask=_decimal(raw["PASK"]),
        trade_volume=_decimal(raw["EVOL"]),
        accumulated_volume=_decimal(raw["TVOL"]),
        accumulated_value=_decimal(raw["TAMT"]),
        conclusion_strength=_decimal(raw["STRN"]),
        market_type=raw["MTYP"],
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외주식 실시간체결통보 (H0GSCNI0, 암호화)
# ---------------------------------------------------------------------------

_EXECUTION_NOTICE_FIELDS = (
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS", "ODER_KIND2",
    "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR", "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN",
    "ACPT_YN", "BRNC_NO", "ODER_QTY", "ACNT_NAME", "CNTG_ISNM", "ODER_COND", "DEBT_GB",
    "DEBT_DATE", "START_TM", "END_TM", "TM_DIV_TP", "CNTG_UNPR12",
)


@dataclass(frozen=True, slots=True)
class ExecutionNotice:
    """해외주식 실시간체결통보(H0GSCNI0). 주문 접수/체결/거부 통보 한 건.

    프레임은 암호화되지만 연결 계층이 복호화한 뒤 파서를 호출한다.
    """

    customer_id: str
    account_no: str
    order_no: str
    original_order_no: str
    sell_buy: str  # 매도매수구분
    symbol: str
    symbol_name: str
    executed_quantity: Decimal
    executed_price: Decimal
    time: str  # 주식 체결 시간
    order_quantity: Decimal
    rejected: bool  # 거부여부
    executed: bool  # 체결여부
    accepted: bool  # 접수여부
    branch_no: str
    _raw: Mapping[str, Any] = _raw_field()


def parse_execution_notice(fields: list[str]) -> ExecutionNotice:
    """H0GSCNI0 한 레코드(복호화된 평문) -> :class:`ExecutionNotice`."""
    raw = MappingProxyType(dict(zip(_EXECUTION_NOTICE_FIELDS, fields, strict=False)))
    return ExecutionNotice(
        customer_id=raw["CUST_ID"],
        account_no=raw["ACNT_NO"],
        order_no=raw["ODER_NO"],
        original_order_no=raw["OODER_NO"],
        sell_buy=raw["SELN_BYOV_CLS"],
        symbol=raw["STCK_SHRN_ISCD"],
        symbol_name=raw["CNTG_ISNM"],
        executed_quantity=_decimal(raw["CNTG_QTY"]),
        executed_price=_decimal(raw["CNTG_UNPR"]),
        time=raw["STCK_CNTG_HOUR"],
        order_quantity=_decimal(raw["ODER_QTY"]),
        rejected=raw["RFUS_YN"] == "1",  # RFUS_YN 0:승인 1:거부 (Y/N 아님)
        executed=raw["CNTG_YN"] == "2",  # CNTG_YN 1:주문/정정/취소/거부 2:체결 (Y/N 아님)
        accepted=raw["ACPT_YN"] in ("1", "2"),  # ACPT_YN 1:주문접수 2:확인 3:취소(FOK/IOC); 3 은 미접수 취급, 원코드는 _raw
        branch_no=raw["BRNC_NO"],
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외선물옵션 실시간체결가 (HDFFF020)
# ---------------------------------------------------------------------------

_FUTURES_TRADE_FIELDS = (
    "SERIES_CD", "BSNS_DATE", "MRKT_OPEN_DATE", "MRKT_OPEN_TIME", "MRKT_CLOSE_DATE",
    "MRKT_CLOSE_TIME", "PREV_PRICE", "RECV_DATE", "RECV_TIME", "ACTIVE_FLAG",
    "LAST_PRICE", "LAST_QNTT", "PREV_DIFF_PRICE", "PREV_DIFF_RATE", "OPEN_PRICE",
    "HIGH_PRICE", "LOW_PRICE", "VOL", "PREV_SIGN", "QUOTSIGN", "RECV_TIME2",
    "PSTTL_PRICE", "PSTTL_SIGN", "PSTTL_DIFF_PRICE", "PSTTL_DIFF_RATE",
)


@dataclass(frozen=True, slots=True)
class FuturesTradeTick:
    """해외선물옵션 실시간체결가(HDFFF020). 한 체결의 가격/수량/등락/누적거래량."""

    symbol: str  # SERIES_CD
    business_date: str
    recv_date: str
    recv_time: str
    prev_close: Decimal
    current_price: Decimal  # LAST_PRICE
    trade_volume: Decimal  # LAST_QNTT
    change: Decimal
    change_percent: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    accumulated_volume: Decimal
    change_sign: str
    trade_sign: str  # 체결구분(QUOTSIGN)
    prev_settlement_price: Decimal
    _raw: Mapping[str, Any] = _raw_field()


def parse_futures_trade_tick(fields: list[str]) -> FuturesTradeTick:
    """HDFFF020 한 레코드 -> :class:`FuturesTradeTick`."""
    raw = MappingProxyType(dict(zip(_FUTURES_TRADE_FIELDS, fields, strict=False)))
    return FuturesTradeTick(
        symbol=raw["SERIES_CD"],
        business_date=raw["BSNS_DATE"],
        recv_date=raw["RECV_DATE"],
        recv_time=raw["RECV_TIME"],
        prev_close=_decimal(raw["PREV_PRICE"]),
        current_price=_decimal(raw["LAST_PRICE"]),
        trade_volume=_decimal(raw["LAST_QNTT"]),
        change=_decimal(raw["PREV_DIFF_PRICE"]),
        change_percent=_decimal(raw["PREV_DIFF_RATE"]),
        open=_decimal(raw["OPEN_PRICE"]),
        high=_decimal(raw["HIGH_PRICE"]),
        low=_decimal(raw["LOW_PRICE"]),
        accumulated_volume=_decimal(raw["VOL"]),
        change_sign=raw["PREV_SIGN"],
        trade_sign=raw["QUOTSIGN"],
        prev_settlement_price=_decimal(raw["PSTTL_PRICE"]),
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외선물옵션 실시간호가 (HDFFF010)
# ---------------------------------------------------------------------------

_FUTURES_ORDERBOOK_FIELDS = (
    "SERIES_CD", "RECV_DATE", "RECV_TIME", "PREV_PRICE",
    "BID_QNTT_1", "BID_NUM_1", "BID_PRICE_1", "ASK_QNTT_1", "ASK_NUM_1", "ASK_PRICE_1",
    "BID_QNTT_2", "BID_NUM_2", "BID_PRICE_2", "ASK_QNTT_2", "ASK_NUM_2", "ASK_PRICE_2",
    "BID_QNTT_3", "BID_NUM_3", "BID_PRICE_3", "ASK_QNTT_3", "ASK_NUM_3", "ASK_PRICE_3",
    "BID_QNTT_4", "BID_NUM_4", "BID_PRICE_4", "ASK_QNTT_4", "ASK_NUM_4", "ASK_PRICE_4",
    "BID_QNTT_5", "BID_NUM_5", "BID_PRICE_5", "ASK_QNTT_5", "ASK_NUM_5", "ASK_PRICE_5",
    "STTL_PRICE",
)


@dataclass(frozen=True, slots=True)
class FuturesOrderBook:
    """해외선물옵션 실시간호가(HDFFF010). 5단계 매수/매도 호가·잔량 스냅샷."""

    symbol: str  # SERIES_CD
    recv_date: str
    recv_time: str
    prev_close: Decimal
    best_bid: Decimal
    best_bid_quantity: Decimal
    best_ask: Decimal
    best_ask_quantity: Decimal
    settlement_price: Decimal
    _raw: Mapping[str, Any] = _raw_field()


def parse_futures_orderbook(fields: list[str]) -> FuturesOrderBook:
    """HDFFF010 한 레코드 -> :class:`FuturesOrderBook`."""
    raw = MappingProxyType(dict(zip(_FUTURES_ORDERBOOK_FIELDS, fields, strict=False)))
    return FuturesOrderBook(
        symbol=raw["SERIES_CD"],
        recv_date=raw["RECV_DATE"],
        recv_time=raw["RECV_TIME"],
        prev_close=_decimal(raw["PREV_PRICE"]),
        best_bid=_decimal(raw["BID_PRICE_1"]),
        best_bid_quantity=_decimal(raw["BID_QNTT_1"]),
        best_ask=_decimal(raw["ASK_PRICE_1"]),
        best_ask_quantity=_decimal(raw["ASK_QNTT_1"]),
        settlement_price=_decimal(raw["STTL_PRICE"]),
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외선물옵션 실시간주문내역통보 (HDFFF1C0, 암호화)
# ---------------------------------------------------------------------------

_FUTURES_ORDER_NOTICE_FIELDS = (
    "USER_ID", "ACCT_NO", "ORD_DT", "ODNO", "ORGN_ORD_DT", "ORGN_ODNO", "SERIES",
    "RVSE_CNCL_DVSN_CD", "SLL_BUY_DVSN_CD", "CPLX_ORD_DVSN_CD", "PRCE_TP",
    "FM_EXCG_RCIT_DVSN_CD", "ORD_QTY", "FM_LMT_PRIC", "FM_STOP_ORD_PRIC",
    "TOT_CCLD_QTY", "TOT_CCLD_UV", "ORD_REMQ", "FM_ORD_GRP_DT", "ORD_GRP_STNO",
    "ORD_DTL_DTIME", "OPRT_DTL_DTIME", "WORK_EMPL", "CRCY_CD", "LQD_YN",
    "LQD_LMT_PRIC", "LQD_STOP_PRIC", "TRD_COND", "TERM_ORD_VALD_DTIME", "SPEC_TP",
    "ECIS_RSVN_ORD_YN", "FUOP_ITEM_DVSN_CD", "AUTO_ORD_DVSN_CD",
)


@dataclass(frozen=True, slots=True)
class FuturesOrderNotice:
    """해외선물옵션 실시간주문내역통보(HDFFF1C0). 주문 접수/정정/취소 통보 한 건.

    프레임은 암호화되지만 연결 계층이 복호화한 뒤 파서를 호출한다.
    """

    user_id: str
    account_no: str
    order_date: str
    order_no: str
    original_order_date: str
    original_order_no: str
    symbol: str  # SERIES
    sell_buy: str
    order_quantity: Decimal
    limit_price: Decimal
    stop_price: Decimal
    total_executed_quantity: Decimal
    total_executed_price: Decimal
    remaining_quantity: Decimal
    currency: str
    liquidation: bool  # 청산여부(LQD_YN)
    _raw: Mapping[str, Any] = _raw_field()


def parse_futures_order_notice(fields: list[str]) -> FuturesOrderNotice:
    """HDFFF1C0 한 레코드(복호화된 평문) -> :class:`FuturesOrderNotice`."""
    raw = MappingProxyType(dict(zip(_FUTURES_ORDER_NOTICE_FIELDS, fields, strict=False)))
    return FuturesOrderNotice(
        user_id=raw["USER_ID"],
        account_no=raw["ACCT_NO"],
        order_date=raw["ORD_DT"],
        order_no=raw["ODNO"],
        original_order_date=raw["ORGN_ORD_DT"],
        original_order_no=raw["ORGN_ODNO"],
        symbol=raw["SERIES"],
        sell_buy=raw["SLL_BUY_DVSN_CD"],
        order_quantity=_decimal(raw["ORD_QTY"]),
        limit_price=_decimal(raw["FM_LMT_PRIC"]),
        stop_price=_decimal(raw["FM_STOP_ORD_PRIC"]),
        total_executed_quantity=_decimal(raw["TOT_CCLD_QTY"]),
        total_executed_price=_decimal(raw["TOT_CCLD_UV"]),
        remaining_quantity=_decimal(raw["ORD_REMQ"]),
        currency=raw["CRCY_CD"],
        liquidation=raw["LQD_YN"] == "Y",
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외선물옵션 실시간체결내역통보 (HDFFF2C0, 암호화)
# ---------------------------------------------------------------------------

_FUTURES_EXECUTION_NOTICE_FIELDS = (
    "USER_ID", "ACCT_NO", "ORD_DT", "ODNO", "ORGN_ORD_DT", "ORGN_ODNO", "SERIES",
    "RVSE_CNCL_DVSN_CD", "SLL_BUY_DVSN_CD", "CPLX_ORD_DVSN_CD", "PRCE_TP",
    "FM_EXCG_RCIT_DVSN_CD", "ORD_QTY", "FM_LMT_PRIC", "FM_STOP_ORD_PRIC",
    "TOT_CCLD_QTY", "TOT_CCLD_UV", "ORD_REMQ", "FM_ORD_GRP_DT", "ORD_GRP_STNO",
    "ORD_DTL_DTIME", "OPRT_DTL_DTIME", "WORK_EMPL", "CCLD_DT", "CCNO", "API_CCNO",
    "CCLD_QTY", "FM_CCLD_PRIC", "CRCY_CD", "TRST_FEE", "ORD_MDIA_ONLINE_YN",
    "FM_CCLD_AMT", "FUOP_ITEM_DVSN_CD",
)


@dataclass(frozen=True, slots=True)
class FuturesExecutionNotice:
    """해외선물옵션 실시간체결내역통보(HDFFF2C0). 체결 통보 한 건(수수료·통화 포함).

    프레임은 암호화되지만 연결 계층이 복호화한 뒤 파서를 호출한다.
    """

    user_id: str
    account_no: str
    order_date: str
    order_no: str
    symbol: str  # SERIES
    sell_buy: str
    order_quantity: Decimal
    executed_date: str
    execution_no: str
    executed_quantity: Decimal
    executed_price: Decimal
    executed_amount: Decimal
    commission: Decimal
    currency: str
    online: bool  # 주문매체온라인여부(ORD_MDIA_ONLINE_YN)
    _raw: Mapping[str, Any] = _raw_field()


def parse_futures_execution_notice(fields: list[str]) -> FuturesExecutionNotice:
    """HDFFF2C0 한 레코드(복호화된 평문) -> :class:`FuturesExecutionNotice`."""
    raw = MappingProxyType(dict(zip(_FUTURES_EXECUTION_NOTICE_FIELDS, fields, strict=False)))
    return FuturesExecutionNotice(
        user_id=raw["USER_ID"],
        account_no=raw["ACCT_NO"],
        order_date=raw["ORD_DT"],
        order_no=raw["ODNO"],
        symbol=raw["SERIES"],
        sell_buy=raw["SLL_BUY_DVSN_CD"],
        order_quantity=_decimal(raw["ORD_QTY"]),
        executed_date=raw["CCLD_DT"],
        execution_no=raw["CCNO"],
        executed_quantity=_decimal(raw["CCLD_QTY"]),
        executed_price=_decimal(raw["FM_CCLD_PRIC"]),
        executed_amount=_decimal(raw["FM_CCLD_AMT"]),
        commission=_decimal(raw["TRST_FEE"]),
        currency=raw["CRCY_CD"],
        online=raw["ORD_MDIA_ONLINE_YN"] == "Y",
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 해외주식 실시간지연호가 아시아 (HDFSASP1)
# ---------------------------------------------------------------------------

_ASIA_ORDERBOOK_FIELDS = (
    "RSYM", "SYMB", "ZDIV", "XYMD", "XHMS", "KYMD", "KHMS", "BVOL", "AVOL", "BDVL", "ADVL",
    "PBID1", "PASK1", "VBID1", "VASK1", "DBID1", "DASK1",
)


@dataclass(frozen=True, slots=True)
class AsiaDelayedOrderBook:
    """해외주식 실시간지연호가 아시아(HDFSASP1). 아시아 거래소 1단계 매수/매도 호가·잔량 스냅샷.

    아시아권 지연호가는 최우선 1단계만 제공한다(미주/유럽의 10단계 :class:`OrderBook` 과 구분).
    전체 필드는 ``_raw`` (KIS Element 이름 기준)에 있고, 아래는 헤드라인만 타입화한 것.
    """

    symbol: str
    realtime_symbol: str
    decimal_places: str
    local_date: str
    local_time: str
    korea_date: str
    korea_time: str
    total_bid_quantity: Decimal
    total_ask_quantity: Decimal
    best_bid: Decimal
    best_ask: Decimal
    best_bid_quantity: Decimal
    best_ask_quantity: Decimal
    _raw: Mapping[str, Any] = _raw_field()


def parse_asia_orderbook(fields: list[str]) -> AsiaDelayedOrderBook:
    """HDFSASP1 한 레코드(17필드) -> :class:`AsiaDelayedOrderBook`."""
    raw = MappingProxyType(dict(zip(_ASIA_ORDERBOOK_FIELDS, fields, strict=False)))
    return AsiaDelayedOrderBook(
        symbol=raw["SYMB"],
        realtime_symbol=raw["RSYM"],
        decimal_places=raw["ZDIV"],
        local_date=raw["XYMD"],
        local_time=raw["XHMS"],
        korea_date=raw["KYMD"],
        korea_time=raw["KHMS"],
        total_bid_quantity=_decimal(raw["BVOL"]),
        total_ask_quantity=_decimal(raw["AVOL"]),
        best_bid=_decimal(raw["PBID1"]),
        best_ask=_decimal(raw["PASK1"]),
        best_bid_quantity=_decimal(raw["VBID1"]),
        best_ask_quantity=_decimal(raw["VASK1"]),
        _raw=raw,
    )


# ---------------------------------------------------------------------------
# 레지스트리 등록
# ---------------------------------------------------------------------------

register(TRSpec("HDFSASP0", field_count=len(_ORDERBOOK_FIELDS), parser=parse_orderbook))
register(TRSpec("HDFSASP1", field_count=len(_ASIA_ORDERBOOK_FIELDS), parser=parse_asia_orderbook))
register(TRSpec("HDFSCNT0", field_count=len(_DELAYED_TRADE_FIELDS), parser=parse_delayed_trade_tick))
register(
    TRSpec(
        "H0GSCNI0",
        field_count=len(_EXECUTION_NOTICE_FIELDS),
        parser=parse_execution_notice,
        encrypted=True,
    )
)
register(TRSpec("HDFFF020", field_count=len(_FUTURES_TRADE_FIELDS), parser=parse_futures_trade_tick))
register(TRSpec("HDFFF010", field_count=len(_FUTURES_ORDERBOOK_FIELDS), parser=parse_futures_orderbook))
register(
    TRSpec(
        "HDFFF1C0",
        field_count=len(_FUTURES_ORDER_NOTICE_FIELDS),
        parser=parse_futures_order_notice,
        encrypted=True,
    )
)
register(
    TRSpec(
        "HDFFF2C0",
        field_count=len(_FUTURES_EXECUTION_NOTICE_FIELDS),
        parser=parse_futures_execution_notice,
        encrypted=True,
    )
)

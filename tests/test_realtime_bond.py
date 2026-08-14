"""채권 실시간 파서/엔티티 테스트 -- 일반채권 체결가/호가, 채권지수 체결가.

원장 필드 레이아웃대로 파싱되는지, 레지스트리 등록으로 연결 계층이 타입 엔티티를 내는지 검증.
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry  # noqa: F401  (import 시 파서 등록)
from kis_trader.realtime.parsers.bond import (
    BondIndexTick,
    BondOrderBook,
    BondTradeTick,
    parse_bond_index_tick,
    parse_bond_order_book,
    parse_bond_trade_tick,
)


# --- 일반채권 실시간체결가 (H0BJCNT0) -------------------------------------------------

def _trade_tick_fields() -> list[str]:
    fields = ["0"] * 19
    fields[0] = "KR103501GA34"   # symbol
    fields[1] = "국고01500-3006"  # name
    fields[2] = "093000"          # time
    fields[3] = "2"               # change_sign (상승)
    fields[4] = "5"               # change
    fields[5] = "0.05"            # change_percent
    fields[6] = "10250"           # current_price
    fields[7] = "100"             # trade_volume
    fields[8] = "10240"           # open
    fields[9] = "10260"           # high
    fields[10] = "10230"          # low
    fields[11] = "10245"          # previous_close
    fields[12] = "3.512"          # current_yield
    fields[16] = "50000"          # accumulated_volume
    fields[18] = "1"              # trade_type_code
    return fields


def test_parse_bond_trade_tick_maps_headline_fields():
    tick = parse_bond_trade_tick(_trade_tick_fields())
    assert isinstance(tick, BondTradeTick)
    assert tick.symbol == "KR103501GA34"
    assert tick.name == "국고01500-3006"
    assert tick.time == "093000"
    assert tick.change_sign == "2"
    assert tick.change == Decimal("5")
    assert tick.change_percent == Decimal("0.05")
    assert tick.current_price == Decimal("10250")
    assert tick.trade_volume == Decimal("100")
    assert tick.open == Decimal("10240")
    assert tick.high == Decimal("10260")
    assert tick.low == Decimal("10230")
    assert tick.previous_close == Decimal("10245")
    assert tick.current_yield == Decimal("3.512")
    assert tick.accumulated_volume == Decimal("50000")
    assert tick.trade_type_code == "1"


def test_bond_trade_tick_raw_has_all_ledger_keys():
    tick = parse_bond_trade_tick(_trade_tick_fields())
    assert len(tick._raw) == 19
    assert tick._raw["STND_ISCD"] == "KR103501GA34"
    assert tick._raw["CNTG_TYPE_CLS_CODE"] == "1"


# --- 일반채권 실시간호가 (H0BJASP0) --------------------------------------------------

def _order_book_fields() -> list[str]:
    fields = ["0"] * 34
    fields[0] = "KR103501GA34"   # symbol
    fields[1] = "093000"          # time
    fields[2] = "3.510"           # best_ask_yield
    fields[3] = "3.515"           # best_bid_yield
    fields[4] = "10251"           # best_ask_price
    fields[5] = "10249"           # best_bid_price
    fields[6] = "300"             # best_ask_volume
    fields[7] = "250"             # best_bid_volume
    fields[32] = "9000"           # total_ask_volume
    fields[33] = "8500"           # total_bid_volume
    return fields


def test_parse_bond_order_book_maps_headline_fields():
    book = parse_bond_order_book(_order_book_fields())
    assert isinstance(book, BondOrderBook)
    assert book.symbol == "KR103501GA34"
    assert book.time == "093000"
    assert book.best_ask_price == Decimal("10251")
    assert book.best_bid_price == Decimal("10249")
    assert book.best_ask_yield == Decimal("3.510")
    assert book.best_bid_yield == Decimal("3.515")
    assert book.best_ask_volume == Decimal("300")
    assert book.best_bid_volume == Decimal("250")
    assert book.total_ask_volume == Decimal("9000")
    assert book.total_bid_volume == Decimal("8500")


def test_bond_order_book_raw_has_all_ledger_keys():
    book = parse_bond_order_book(_order_book_fields())
    assert len(book._raw) == 34
    assert book._raw["STND_ISCD"] == "KR103501GA34"
    assert book._raw["TOTAL_BIDP_RSQN"] == "8500"


# --- 채권지수 실시간체결가 (H0BICNT0) ------------------------------------------------

def _index_tick_fields() -> list[str]:
    fields = ["0"] * 20
    fields[0] = "BMKI300"        # index_id
    fields[1] = "20260814"        # base_date
    fields[2] = "093000"          # time
    fields[3] = "10100.11"        # open
    fields[4] = "10120.55"        # high
    fields[5] = "10095.02"        # low
    fields[6] = "10110.33"        # total_return_index
    fields[7] = "10105.00"        # previous_total_return_index
    fields[8] = "5.33"            # change
    fields[9] = "2"               # change_sign
    fields[10] = "0.05"           # change_percent
    fields[11] = "9800.12"        # clean_price_index
    fields[12] = "9850.44"        # market_price_index
    fields[16] = "4.21"           # average_duration
    fields[17] = "0.31"           # average_convexity
    fields[18] = "3.487"          # average_ytm
    fields[19] = "3.512"          # average_forward_ytm
    return fields


def test_parse_bond_index_tick_maps_headline_fields():
    idx = parse_bond_index_tick(_index_tick_fields())
    assert isinstance(idx, BondIndexTick)
    assert idx.index_id == "BMKI300"
    assert idx.base_date == "20260814"
    assert idx.time == "093000"
    assert idx.open == Decimal("10100.11")
    assert idx.high == Decimal("10120.55")
    assert idx.low == Decimal("10095.02")
    assert idx.total_return_index == Decimal("10110.33")
    assert idx.previous_total_return_index == Decimal("10105.00")
    assert idx.change == Decimal("5.33")
    assert idx.change_sign == "2"
    assert idx.change_percent == Decimal("0.05")
    assert idx.clean_price_index == Decimal("9800.12")
    assert idx.market_price_index == Decimal("9850.44")
    assert idx.average_duration == Decimal("4.21")
    assert idx.average_convexity == Decimal("0.31")
    assert idx.average_ytm == Decimal("3.487")
    assert idx.average_forward_ytm == Decimal("3.512")


def test_bond_index_tick_raw_has_all_ledger_keys():
    idx = parse_bond_index_tick(_index_tick_fields())
    assert len(idx._raw) == 20
    assert idx._raw["NMIX_ID"] == "BMKI300"
    assert idx._raw["BOND_AVRG_FRDL_YTM_VAL"] == "3.512"


# --- 레지스트리 등록 -----------------------------------------------------------------

def test_registry_has_bond_specs():
    import kis_trader.realtime.parsers.bond  # noqa: F401  (파서 등록 보장)

    trade = _registry.lookup("H0BJCNT0")
    assert trade is not None
    assert trade.field_count == 19
    assert trade.parser is parse_bond_trade_tick

    book = _registry.lookup("H0BJASP0")
    assert book is not None
    assert book.field_count == 34
    assert book.parser is parse_bond_order_book

    index = _registry.lookup("H0BICNT0")
    assert index is not None
    assert index.field_count == 20
    assert index.parser is parse_bond_index_tick

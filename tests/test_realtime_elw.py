"""ELW 실시간 파서/엔티티 테스트 -- 호가/체결가/예상체결.

원장 필드순대로 파싱되는지, 헤드라인 필드 매핑과 ``_raw`` 전량 보존, 레지스트리 등록을 검증한다.
순수 함수 테스트(네트워크 없음).
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.parsers.elw import (
    _EXECUTION_TICK_FIELDS,
    _EXPECTED_CONCLUSION_FIELDS,
    _ORDER_BOOK_FIELDS,
    ExecutionTick,
    ExpectedConclusion,
    OrderBook,
    parse_execution_tick,
    parse_expected_conclusion,
    parse_order_book,
)


def _index(fields: tuple[str, ...], name: str) -> int:
    return fields.index(name)


# --------------------------------------------------------------------------------------
# OrderBook (H0EWASP0)
# --------------------------------------------------------------------------------------


def _order_book_fields() -> list[str]:
    fields = ["0"] * len(_ORDER_BOOK_FIELDS)

    def put(name: str, value: str) -> None:
        fields[_index(_ORDER_BOOK_FIELDS, name)] = value

    put("MKSC_SHRN_ISCD", "58J300")
    put("BSOP_HOUR", "093015")
    put("HOUR_CLS_CODE", "0")
    for i in range(1, 11):
        put(f"ASKP{i}", str(100 + i))
        put(f"BIDP{i}", str(90 - i))
        put(f"ASKP_RSQN{i}", str(1000 + i))
        put(f"BIDP_RSQN{i}", str(2000 + i))
    put("TOTAL_ASKP_RSQN", "50000")
    put("TOTAL_BIDP_RSQN", "60000")
    put("ANTC_CNPR", "105")
    put("ANTC_CNQN", "42")
    put("ANTC_CNTG_VRSS_SIGN", "2")
    put("ANTC_CNTG_VRSS", "5")
    put("ANTC_CNTG_PRDY_CTRT", "5.0")
    put("LP_TOTAL_ASKP_RSQN", "7000")
    put("LP_TOTAL_BIDP_RSQN", "8000")
    return fields


def test_parse_order_book_maps_headline_fields():
    ob = parse_order_book(_order_book_fields())
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "58J300"
    assert ob.time == "093015"
    assert ob.hour_class == "0"
    assert ob.ask_prices == tuple(Decimal(100 + i) for i in range(1, 11))
    assert ob.bid_prices == tuple(Decimal(90 - i) for i in range(1, 11))
    assert ob.ask_quantities == tuple(Decimal(1000 + i) for i in range(1, 11))
    assert ob.bid_quantities == tuple(Decimal(2000 + i) for i in range(1, 11))
    assert ob.ask_prices[0] == Decimal(101)
    assert ob.bid_prices[0] == Decimal(89)
    assert ob.total_ask_quantity == Decimal(50000)
    assert ob.total_bid_quantity == Decimal(60000)
    assert ob.expected_price == Decimal(105)
    assert ob.expected_volume == Decimal(42)
    assert ob.expected_change_sign == "2"
    assert ob.expected_change == Decimal(5)
    assert ob.expected_change_percent == Decimal("5.0")
    assert ob.lp_total_ask_quantity == Decimal(7000)
    assert ob.lp_total_bid_quantity == Decimal(8000)


def test_order_book_raw_has_all_ledger_keys():
    ob = parse_order_book(_order_book_fields())
    assert len(ob._raw) == len(_ORDER_BOOK_FIELDS) == 73
    assert ob._raw["MKSC_SHRN_ISCD"] == "58J300"
    assert ob._raw["ANTC_VOL"] == "0"
    assert ob._raw["LP_BIDP_RSQN1"] == "0"


# --------------------------------------------------------------------------------------
# ExecutionTick (H0EWCNT0)
# --------------------------------------------------------------------------------------


def _execution_tick_fields() -> list[str]:
    fields = ["0"] * len(_EXECUTION_TICK_FIELDS)

    def put(name: str, value: str) -> None:
        fields[_index(_EXECUTION_TICK_FIELDS, name)] = value

    put("MKSC_SHRN_ISCD", "58J300")
    put("STCK_CNTG_HOUR", "093030")
    put("STCK_PRPR", "125")
    put("PRDY_VRSS_SIGN", "2")
    put("PRDY_VRSS", "5")
    put("PRDY_CTRT", "4.17")
    put("STCK_OPRC", "120")
    put("STCK_HGPR", "130")
    put("STCK_LWPR", "118")
    put("ASKP1", "126")
    put("BIDP1", "124")
    put("CNTG_VOL", "10")
    put("ACML_VOL", "500000")
    put("ACML_TR_PBMN", "62500000")
    put("CTTR", "110.5")
    put("CNTG_CLS_CODE", "1")
    put("BSOP_DATE", "20260814")
    put("TRHT_YN", "N")
    put("TMVL_VAL", "3.5")
    put("PRIT", "98.2")
    put("PRMM_VAL", "1.2")
    put("PRMM_RATE", "2.4")
    put("GEAR", "12.3")
    put("LVRG_VAL", "8.1")
    put("PRLS_QRYR_RATE", "1.05")
    put("INVL_VAL", "2.0")
    put("DELTA", "0.55")
    put("GAMA", "0.02")
    put("VEGA", "0.11")
    put("THETA", "-0.09")
    put("RHO", "0.03")
    put("HTS_INTS_VLTL", "35.7")
    put("HTS_THPR", "124.8")
    put("LP_HVOL", "300000")
    put("LP_HLDN_RATE", "12.5")
    put("LP_NTBY_QTY", "-1500")
    return fields


def test_parse_execution_tick_maps_headline_fields():
    tick = parse_execution_tick(_execution_tick_fields())
    assert isinstance(tick, ExecutionTick)
    assert tick.symbol == "58J300"
    assert tick.time == "093030"
    assert tick.current_price == Decimal(125)
    assert tick.change_sign == "2"
    assert tick.change == Decimal(5)
    assert tick.change_percent == Decimal("4.17")
    assert tick.open == Decimal(120)
    assert tick.high == Decimal(130)
    assert tick.low == Decimal(118)
    assert tick.best_ask == Decimal(126)
    assert tick.best_bid == Decimal(124)
    assert tick.trade_volume == Decimal(10)
    assert tick.accumulated_volume == Decimal(500000)
    assert tick.accumulated_value == Decimal(62500000)
    assert tick.conclusion_strength == Decimal("110.5")
    assert tick.trade_sign == "1"
    assert tick.business_date == "20260814"
    assert tick.trading_halted is False
    assert tick.time_value == Decimal("3.5")
    assert tick.parity == Decimal("98.2")
    assert tick.premium == Decimal("1.2")
    assert tick.premium_percent == Decimal("2.4")
    assert tick.gearing == Decimal("12.3")
    assert tick.leverage == Decimal("8.1")
    assert tick.breakeven_percent == Decimal("1.05")
    assert tick.intrinsic_value == Decimal("2.0")
    assert tick.delta == Decimal("0.55")
    assert tick.gamma == Decimal("0.02")
    assert tick.vega == Decimal("0.11")
    assert tick.theta == Decimal("-0.09")
    assert tick.rho == Decimal("0.03")
    assert tick.implied_volatility == Decimal("35.7")
    assert tick.theoretical_price == Decimal("124.8")
    assert tick.lp_holding == Decimal(300000)
    assert tick.lp_holding_percent == Decimal("12.5")
    assert tick.lp_net_sell_volume == Decimal(-1500)


def test_execution_tick_raw_has_all_ledger_keys():
    tick = parse_execution_tick(_execution_tick_fields())
    assert len(tick._raw) == len(_EXECUTION_TICK_FIELDS) == 63
    assert tick._raw["MKSC_SHRN_ISCD"] == "58J300"
    assert tick._raw["APPRCH_RATE"] == "0"
    assert tick._raw["LP_NTBY_QTY"] == "-1500"


# --------------------------------------------------------------------------------------
# ExpectedConclusion (H0EWANC0)
# --------------------------------------------------------------------------------------


def _expected_conclusion_fields() -> list[str]:
    fields = ["0"] * len(_EXPECTED_CONCLUSION_FIELDS)

    def put(name: str, value: str) -> None:
        fields[_index(_EXPECTED_CONCLUSION_FIELDS, name)] = value

    put("MKSC_SHRN_ISCD", "58J300")
    put("STCK_CNTG_HOUR", "085959")
    put("STCK_PRPR", "123")
    put("PRDY_VRSS_SIGN", "5")
    put("PRDY_VRSS", "3")
    put("PRDY_CTRT", "-2.38")
    put("STCK_OPRC", "125")
    put("STCK_HGPR", "127")
    put("STCK_LWPR", "121")
    put("ASKP1", "124")
    put("BIDP1", "122")
    put("CNTG_VOL", "7")
    put("ACML_VOL", "12000")
    put("ACML_TR_PBMN", "1476000")
    put("CTTR", "95.0")
    put("CNTG_CLS_CODE", "5")
    put("BSOP_DATE", "20260814")
    put("TRHT_YN", "N")
    put("TMVL_VAL", "3.1")
    put("PRIT", "97.0")
    put("PRMM_VAL", "1.0")
    put("PRMM_RATE", "2.1")
    put("GEAR", "11.0")
    put("LVRG_VAL", "7.5")
    put("PRLS_QRYR_RATE", "1.02")
    put("INVL_VAL", "1.8")
    put("DELTA", "0.50")
    put("GAMA", "0.03")
    put("VEGA", "0.12")
    put("THETA", "-0.08")
    put("RHO", "0.02")
    put("HTS_INTS_VLTL", "33.2")
    put("HTS_THPR", "122.9")
    put("LP_HVOL", "250000")
    put("LP_HLDN_RATE", "10.8")
    return fields


def test_parse_expected_conclusion_maps_headline_fields():
    ec = parse_expected_conclusion(_expected_conclusion_fields())
    assert isinstance(ec, ExpectedConclusion)
    assert ec.symbol == "58J300"
    assert ec.time == "085959"
    assert ec.expected_price == Decimal(123)
    assert ec.change_sign == "5"
    assert ec.change == Decimal(3)
    assert ec.change_percent == Decimal("-2.38")
    assert ec.open == Decimal(125)
    assert ec.high == Decimal(127)
    assert ec.low == Decimal(121)
    assert ec.best_ask == Decimal(124)
    assert ec.best_bid == Decimal(122)
    assert ec.expected_volume == Decimal(7)
    assert ec.accumulated_volume == Decimal(12000)
    assert ec.accumulated_value == Decimal(1476000)
    assert ec.conclusion_strength == Decimal("95.0")
    assert ec.trade_sign == "5"
    assert ec.business_date == "20260814"
    assert ec.trading_halted is False
    assert ec.time_value == Decimal("3.1")
    assert ec.parity == Decimal("97.0")
    assert ec.premium == Decimal("1.0")
    assert ec.premium_percent == Decimal("2.1")
    assert ec.gearing == Decimal("11.0")
    assert ec.leverage == Decimal("7.5")
    assert ec.breakeven_percent == Decimal("1.02")
    assert ec.intrinsic_value == Decimal("1.8")
    assert ec.delta == Decimal("0.50")
    assert ec.gamma == Decimal("0.03")
    assert ec.vega == Decimal("0.12")
    assert ec.theta == Decimal("-0.08")
    assert ec.rho == Decimal("0.02")
    assert ec.implied_volatility == Decimal("33.2")
    assert ec.theoretical_price == Decimal("122.9")
    assert ec.lp_holding == Decimal(250000)
    assert ec.lp_holding_percent == Decimal("10.8")


def test_expected_conclusion_raw_has_all_ledger_keys():
    ec = parse_expected_conclusion(_expected_conclusion_fields())
    assert len(ec._raw) == len(_EXPECTED_CONCLUSION_FIELDS) == 59
    assert ec._raw["MKSC_SHRN_ISCD"] == "58J300"
    assert ec._raw["VOL_TNRT"] == "0"


# --------------------------------------------------------------------------------------
# 레지스트리 등록
# --------------------------------------------------------------------------------------


def test_registry_has_elw_specs():
    import kis_trader.realtime.parsers.elw  # noqa: F401  (import 시 파서 등록)

    expected = {
        "H0EWASP0": (73, parse_order_book),
        "H0EWCNT0": (63, parse_execution_tick),
        "H0EWANC0": (59, parse_expected_conclusion),
    }
    for tr_id, (count, parser) in expected.items():
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == count
        assert spec.parser is parser
        assert spec.encrypted is False


def test_empty_numeric_field_defaults_to_zero():
    fields = _execution_tick_fields()
    fields[_index(_EXECUTION_TICK_FIELDS, "STCK_PRPR")] = ""
    tick = parse_execution_tick(fields)
    assert tick.current_price == Decimal(0)

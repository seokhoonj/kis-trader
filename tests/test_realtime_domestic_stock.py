"""실시간 파서/엔티티 테스트 -- 국내주식 호가/예상체결/시간외/프로그램매매/회원사/체결통보.

각 파서가 원장 필드순대로 헤드라인을 타입화하고 ``_raw`` 에 전체 원장 키를 담는지, 레지스트리
등록(필드 수/파서/암호화 플래그)이 올바른지 검증한다. 순수 함수 테스트 -- 네트워크 없음.
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.parsers.domestic_stock import (
    _AFTER_HOURS_TICK_FIELDS,
    _ETF_NAV_FIELDS,
    _EXECUTION_NOTICE_FIELDS,
    _EXPECTED_CONCLUSION_EXT_FIELDS,
    _EXPECTED_CONCLUSION_KRX_FIELDS,
    _MARKET_OPERATION_FIELDS,
    _MARKET_OPERATION_UNIFIED_FIELDS,
    _MEMBER_ACTIVITY_FIELDS,
    _ORDER_BOOK_AFTER_HOURS_FIELDS,
    _ORDER_BOOK_KRX_FIELDS,
    _ORDER_BOOK_NXT_FIELDS,
    _ORDER_BOOK_UNIFIED_FIELDS,
    _PROGRAM_TRADE_FIELDS,
    AfterHoursTick,
    ETFNav,
    ExecutionNotice,
    ExpectedConclusion,
    MarketOperation,
    MemberActivity,
    OrderBook,
    ProgramTrade,
    parse_after_hours_tick,
    parse_etf_nav,
    parse_execution_notice,
    parse_expected_conclusion_ext,
    parse_expected_conclusion_krx,
    parse_market_operation,
    parse_market_operation_unified,
    parse_member_activity,
    parse_order_book_after_hours,
    parse_order_book_krx,
    parse_order_book_nxt,
    parse_order_book_unified,
    parse_program_trade,
)


def _at(fields_spec: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """레이아웃 Element 순서대로, 지정한 Element 만 값을 채운 레코드(list[str])를 만든다."""
    record = ["0"] * len(fields_spec)
    index = {el: i for i, el in enumerate(fields_spec)}
    for el, value in values.items():
        record[index[el]] = value
    return record


# --------------------------------------------------------------------------- OrderBook


def test_parse_order_book_krx_headline_and_raw():
    record = _at(_ORDER_BOOK_KRX_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "093000", "HOUR_CLS_CODE": "0",
        "ASKP1": "71500", "BIDP1": "71400", "ASKP_RSQN1": "120", "BIDP_RSQN1": "300",
        "TOTAL_ASKP_RSQN": "5000", "TOTAL_BIDP_RSQN": "6000",
        "ANTC_CNPR": "71450", "ANTC_CNQN": "42", "MID_PRC": "71450",
    })
    ob = parse_order_book_krx(record)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "005930"
    assert ob.time == "093000"
    assert ob.hour_class == "0"
    assert ob.best_ask == Decimal(71500)
    assert ob.best_bid == Decimal(71400)
    assert ob.best_ask_qty == Decimal(120)
    assert ob.best_bid_qty == Decimal(300)
    assert ob.total_ask_qty == Decimal(5000)
    assert ob.total_bid_qty == Decimal(6000)
    assert ob.expected_price == Decimal(71450)
    assert ob.expected_qty == Decimal(42)
    assert ob._raw["MID_PRC"] == "71450"
    assert ob._raw["ASKP10"] == "0"
    assert len(ob._raw) == len(_ORDER_BOOK_KRX_FIELDS) == 62


def test_parse_order_book_nxt_uses_nxt_mid_price():
    record = _at(_ORDER_BOOK_NXT_FIELDS, {
        "MKSC_SHRN_ISCD": "000660", "BSOP_HOUR": "100000", "ASKP1": "180000",
        "BIDP1": "179500", "NMID_PRC": "179800",
    })
    ob = parse_order_book_nxt(record)
    assert ob.symbol == "000660"
    assert ob.best_ask == Decimal(180000)
    assert ob._raw["NMID_PRC"] == "179800"
    assert len(ob._raw) == 62


def test_parse_order_book_unified_has_both_mid_prices():
    record = _at(_ORDER_BOOK_UNIFIED_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "101500",
        "KMID_PRC": "71450", "NMID_PRC": "71460",
    })
    ob = parse_order_book_unified(record)
    assert ob._raw["KMID_PRC"] == "71450"
    assert ob._raw["NMID_PRC"] == "71460"
    assert len(ob._raw) == len(_ORDER_BOOK_UNIFIED_FIELDS) == 65


def test_parse_order_book_after_hours_nine_levels():
    record = _at(_ORDER_BOOK_AFTER_HOURS_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "173000", "HOUR_CLS_CODE": "A",
        "ASKP1": "71500", "BIDP1": "71400", "TOTAL_ASKP_RSQN": "10",
        "TOTAL_BIDP_RSQN": "20", "ANTC_CNPR": "71450", "ANTC_CNQN": "5",
    })
    ob = parse_order_book_after_hours(record)
    assert ob.symbol == "005930"
    assert ob.time == "173000"
    assert ob.expected_price == Decimal(71450)
    assert "ASKP9" in ob._raw
    assert "ASKP10" not in ob._raw  # 시간외는 9단계
    assert len(ob._raw) == len(_ORDER_BOOK_AFTER_HOURS_FIELDS) == 54


def test_registry_order_book_variants():
    for tr_id, parser, count in (
        ("H0STASP0", parse_order_book_krx, 62),
        ("H0NXASP0", parse_order_book_nxt, 62),
        ("H0UNASP0", parse_order_book_unified, 65),
        ("H0STOAA0", parse_order_book_after_hours, 54),
    ):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == count
        assert spec.parser is parser


# --------------------------------------------------------------------------- ExpectedConclusion


def test_parse_expected_conclusion_krx():
    record = _at(_EXPECTED_CONCLUSION_KRX_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "STCK_CNTG_HOUR": "090000", "STCK_PRPR": "71500",
        "PRDY_VRSS_SIGN": "2", "PRDY_VRSS": "100", "PRDY_CTRT": "0.14",
        "STCK_OPRC": "71400", "STCK_HGPR": "71800", "STCK_LWPR": "70900",
        "CNTG_VOL": "1234", "ACML_VOL": "5000000", "BSOP_DATE": "20260814",
        "TRHT_YN": "N",
    })
    ec = parse_expected_conclusion_krx(record)
    assert isinstance(ec, ExpectedConclusion)
    assert ec.symbol == "005930"
    assert ec.time == "090000"
    assert ec.expected_price == Decimal(71500)
    assert ec.change_sign == "2"
    assert ec.change == Decimal(100)
    assert ec.change_percent == Decimal("0.14")
    assert ec.open == Decimal(71400)
    assert ec.high == Decimal(71800)
    assert ec.low == Decimal(70900)
    assert ec.expected_volume == Decimal(1234)
    assert ec.accumulated_volume == Decimal(5000000)
    assert ec.business_date == "20260814"
    assert ec.trading_halted is False
    assert len(ec._raw) == 45
    assert "VI_STND_PRC" not in ec._raw


def test_parse_expected_conclusion_ext_has_vi_reference():
    record = _at(_EXPECTED_CONCLUSION_EXT_FIELDS, {
        "MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "153000", "STCK_PRPR": "180000",
        "TRHT_YN": "Y", "VI_STND_PRC": "175000",
    })
    ec = parse_expected_conclusion_ext(record)
    assert ec.expected_price == Decimal(180000)
    assert ec.trading_halted is True
    assert ec._raw["VI_STND_PRC"] == "175000"
    assert len(ec._raw) == 46


def test_registry_expected_conclusion_variants():
    krx = _registry.lookup("H0STANC0")
    assert krx is not None
    assert krx.field_count == 45
    assert krx.parser is parse_expected_conclusion_krx
    for tr_id in ("H0NXANC0", "H0UNANC0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 46
        assert spec.parser is parse_expected_conclusion_ext


# --------------------------------------------------------------------------- AfterHoursTick


def test_parse_after_hours_tick():
    record = _at(_AFTER_HOURS_TICK_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "STCK_CNTG_HOUR": "173000", "STCK_PRPR": "71000",
        "PRDY_VRSS_SIGN": "5", "PRDY_VRSS": "-500", "PRDY_CTRT": "-0.70",
        "STCK_OPRC": "71500", "STCK_HGPR": "71600", "STCK_LWPR": "70900",
        "CNTG_VOL": "10", "ACML_VOL": "123456", "ACML_TR_PBMN": "8765432100",
        "BSOP_DATE": "20260814", "TRHT_YN": "N",
    })
    tick = parse_after_hours_tick(record)
    assert isinstance(tick, AfterHoursTick)
    assert tick.symbol == "005930"
    assert tick.time == "173000"
    assert tick.current_price == Decimal(71000)
    assert tick.change_sign == "5"
    assert tick.change == Decimal(-500)
    assert tick.change_percent == Decimal("-0.70")
    assert tick.open == Decimal(71500)
    assert tick.high == Decimal(71600)
    assert tick.low == Decimal(70900)
    assert tick.trade_volume == Decimal(10)
    assert tick.accumulated_volume == Decimal(123456)
    assert tick.accumulated_value == Decimal(8765432100)
    assert tick.business_date == "20260814"
    assert tick.trading_halted is False
    assert len(tick._raw) == 43


def test_registry_after_hours_tick_variants():
    for tr_id in ("H0STOAC0", "H0STOUP0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 43
        assert spec.parser is parse_after_hours_tick


# --------------------------------------------------------------------------- ProgramTrade


def test_parse_program_trade():
    record = _at(_PROGRAM_TRADE_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "STCK_CNTG_HOUR": "100000",
        "SELN_CNQN": "1000", "SELN_TR_PBMN": "71500000",
        "SHNU_CNQN": "1500", "SHNU_TR_PBMN": "107250000",
        "NTBY_CNQN": "500", "NTBY_TR_PBMN": "35750000",
    })
    pt = parse_program_trade(record)
    assert isinstance(pt, ProgramTrade)
    assert pt.symbol == "005930"
    assert pt.time == "100000"
    assert pt.sell_volume == Decimal(1000)
    assert pt.sell_value == Decimal(71500000)
    assert pt.buy_volume == Decimal(1500)
    assert pt.buy_value == Decimal(107250000)
    assert pt.net_buy_volume == Decimal(500)
    assert pt.net_buy_value == Decimal(35750000)
    assert len(pt._raw) == 11
    assert pt._raw["WHOL_NTBY_QTY"] == "0"


def test_registry_program_trade_variants():
    for tr_id in ("H0STPGM0", "H0NXPGM0", "H0UNPGM0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 11
        assert spec.parser is parse_program_trade


# --------------------------------------------------------------------------- MemberActivity


def test_parse_member_activity():
    record = _at(_MEMBER_ACTIVITY_FIELDS, {
        "MKSC_SHRN_ISCD": "005930",
        "SELN2_MBCR_NAME1": "미래에셋",
        "BYOV_MBCR_NAME1": "키움증권",
        "GLOB_TOTAL_SELN_QTY": "12000",
        "GLOB_TOTAL_SHNU_QTY": "20000",
        "GLOB_NTBY_QTY": "8000",
    })
    ma = parse_member_activity(record)
    assert isinstance(ma, MemberActivity)
    assert ma.symbol == "005930"
    assert ma.top_seller == "미래에셋"
    assert ma.top_buyer == "키움증권"
    assert ma.foreign_sell_volume == Decimal(12000)
    assert ma.foreign_buy_volume == Decimal(20000)
    assert ma.foreign_net_buy_volume == Decimal(8000)
    assert len(ma._raw) == 78
    assert ma._raw["BYOV_MBCR_ENG_NAME5"] == "0"


def test_registry_member_activity_variants():
    for tr_id in ("H0STMBC0", "H0NXMBC0", "H0UNMBC0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 78
        assert spec.parser is parse_member_activity


# --------------------------------------------------------------------------- ExecutionNotice


def test_parse_execution_notice():
    record = _at(_EXECUTION_NOTICE_FIELDS, {
        "CUST_ID": "CUST01", "ACNT_NO": "5000000000", "ODER_NO": "0001",
        "OODER_NO": "0000", "SELN_BYOV_CLS": "02", "STCK_SHRN_ISCD": "005930",
        "CNTG_QTY": "10", "CNTG_UNPR": "71500", "STCK_CNTG_HOUR": "093015",
        "RFUS_YN": "0", "CNTG_YN": "2", "ACPT_YN": "2", "ODER_QTY": "10",
        "ODER_PRC": "71500",
    })
    en = parse_execution_notice(record)
    assert isinstance(en, ExecutionNotice)
    assert en.customer_id == "CUST01"
    assert en.account_no == "5000000000"
    assert en.order_no == "0001"
    assert en.original_order_no == "0000"
    assert en.sell_buy_class == "02"
    assert en.symbol == "005930"
    assert en.executed_qty == Decimal(10)
    assert en.executed_price == Decimal(71500)
    assert en.time == "093015"
    assert en.refused is False
    assert en.conclusion_flag == "2"
    assert en.accepted_flag == "2"
    assert en.order_qty == Decimal(10)
    assert en.order_price == Decimal(71500)
    assert len(en._raw) == 26


def test_parse_execution_notice_refused_uses_ledger_code():
    # H0STCNI0 RFUS_YN 0:승인 1:거부 (Y/N 아님) -- 거부 통보에서 refused 가 True 여야 한다.
    record = _at(_EXECUTION_NOTICE_FIELDS, {
        "CUST_ID": "CUST01", "ACNT_NO": "5000000000", "ODER_NO": "0001",
        "OODER_NO": "0000", "SELN_BYOV_CLS": "02", "STCK_SHRN_ISCD": "005930",
        "CNTG_QTY": "0", "CNTG_UNPR": "0", "STCK_CNTG_HOUR": "093015",
        "RFUS_YN": "1", "CNTG_YN": "1", "ACPT_YN": "3", "ODER_QTY": "10",
        "ODER_PRC": "71500",
    })
    en = parse_execution_notice(record)
    assert en.refused is True
    assert en.conclusion_flag == "1"
    assert en.accepted_flag == "3"


def test_registry_execution_notice_is_encrypted():
    spec = _registry.lookup("H0STCNI0")
    assert spec is not None
    assert spec.field_count == 26
    assert spec.parser is parse_execution_notice
    assert spec.encrypted is True


def test_execution_notice_empty_numbers_default_to_zero():
    # 접수(체결 전) 통보는 체결 수량/단가가 빈 값 -> Decimal(0).
    record = _at(_EXECUTION_NOTICE_FIELDS, {
        "STCK_SHRN_ISCD": "005930", "CNTG_QTY": "", "CNTG_UNPR": "",
    })
    en = parse_execution_notice(record)
    assert en.executed_qty == Decimal(0)
    assert en.executed_price == Decimal(0)


# --------------------------------------------------------------------------- ETFNav


def test_parse_etf_nav():
    record = _at(_ETF_NAV_FIELDS, {
        "MKSC_SHRN_ISCD": "069500", "NAV": "39250.15", "NAV_PRDY_VRSS_SIGN": "2",
        "NAV_PRDY_VRSS": "120.30", "NAV_PRDY_CTRT": "0.31", "OPRC_NAV": "39100.00",
        "HPRC_NAV": "39400.50", "LPRC_NAV": "39050.75",
    })
    nav = parse_etf_nav(record)
    assert isinstance(nav, ETFNav)
    assert nav.symbol == "069500"
    assert nav.nav == Decimal("39250.15")
    assert nav.nav_change_sign == "2"
    assert nav.nav_change == Decimal("120.30")
    assert nav.nav_change_percent == Decimal("0.31")
    assert nav.nav_open == Decimal("39100.00")
    assert nav.nav_high == Decimal("39400.50")
    assert nav.nav_low == Decimal("39050.75")
    assert len(nav._raw) == 8
    assert nav._raw["LPRC_NAV"] == "39050.75"


def test_registry_etf_nav():
    spec = _registry.lookup("H0STNAV0")
    assert spec is not None
    assert spec.field_count == 8
    assert spec.parser is parse_etf_nav


# --------------------------------------------------------------------------- MarketOperation


def test_parse_market_operation_krx():
    record = _at(_MARKET_OPERATION_FIELDS, {
        "MKSC_SHRN_ISCD": "005930", "TRHT_YN": "Y", "TR_SUSP_REAS_CNTT": "VI 발동",
        "MKOP_CLS_CODE": "20", "ANTC_MKOP_CLS_CODE": "21", "VI_CLS_CODE": "1",
        "EXCH_CLS_CODE": "1",
    })
    mo = parse_market_operation(record)
    assert isinstance(mo, MarketOperation)
    assert mo.symbol == "005930"
    assert mo.trading_halted is True
    assert mo.halt_reason == "VI 발동"
    assert mo.operation_code == "20"
    assert mo.expected_operation_code == "21"
    assert mo.vi_code == "1"
    assert mo.exchange_code == "1"
    assert len(mo._raw) == 11
    assert mo._raw["OVTM_VI_CLS_CODE"] == "0"


def test_parse_market_operation_unified_has_no_symbol():
    record = _at(_MARKET_OPERATION_UNIFIED_FIELDS, {
        "TRHT_YN": "N", "TR_SUSP_REAS_CNTT": "", "MKOP_CLS_CODE": "11",
        "ANTC_MKOP_CLS_CODE": "0", "VI_CLS_CODE": "0", "EXCH_CLS_CODE": "3",
    })
    mo = parse_market_operation_unified(record)
    assert mo.symbol == ""  # 통합 피드엔 종목코드가 없다
    assert mo.trading_halted is False
    assert mo.operation_code == "11"
    assert mo.exchange_code == "3"
    assert "MKSC_SHRN_ISCD" not in mo._raw
    assert len(mo._raw) == 10


def test_registry_market_operation_variants():
    for tr_id in ("H0STMKO0", "H0NXMKO0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 11
        assert spec.parser is parse_market_operation
    unified = _registry.lookup("H0UNMKO0")
    assert unified is not None
    assert unified.field_count == 10
    assert unified.parser is parse_market_operation_unified

"""파생상품 실시간 파서/엔티티 테스트 -- 선물/옵션 체결가·호가·예상체결·체결통보.

원장 필드순대로 헤드라인이 매핑되는지, ``_raw`` 가 전체 Element 키를 담는지, 레지스트리에
필드수/암호화 여부가 등록되는지 검증. 순수 함수 테스트라 네트워크가 없다.
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.parsers import derivatives as der


def _fields(layout: tuple[str, ...], **values: str) -> list[str]:
    """레이아웃 길이만큼 "0" 으로 채운 뒤, Element 이름으로 값을 꽂는다."""
    out = ["0"] * len(layout)
    index = {el: i for i, el in enumerate(layout)}
    for el, val in values.items():
        out[index[el]] = val
    return out


# --------------------------------------------------------------------------- 호가


def test_parse_futures_order_book():
    fields = _fields(
        der._FUTURES_ORDER_BOOK_FIELDS,
        FUTS_SHRN_ISCD="101W09", BSOP_HOUR="093000", FUTS_ASKP1="330.50",
        FUTS_BIDP1="330.40", ASKP_RSQN1="12", BIDP_RSQN1="8",
        TOTAL_ASKP_RSQN="500", TOTAL_BIDP_RSQN="450",
    )
    ob = der.parse_futures_order_book(fields)
    assert isinstance(ob, der.OrderBook)
    assert ob.symbol == "101W09"
    assert ob.time == "093000"
    assert ob.best_ask == Decimal("330.50")
    assert ob.best_bid == Decimal("330.40")
    assert ob.best_ask_quantity == Decimal("12")
    assert ob.best_bid_quantity == Decimal("8")
    assert ob.total_ask_quantity == Decimal("500")
    assert ob.total_bid_quantity == Decimal("450")
    assert ob._raw["TOTAL_BIDP_RSQN_ICDC"] == "0"
    assert len(ob._raw) == len(der._FUTURES_ORDER_BOOK_FIELDS)


def test_parse_option_order_book():
    fields = _fields(
        der._OPTION_ORDER_BOOK_FIELDS,
        OPTN_SHRN_ISCD="201S1305", BSOP_HOUR="101500", OPTN_ASKP1="2.35",
        OPTN_BIDP1="2.30", ASKP_RSQN1="40", BIDP_RSQN1="55",
    )
    ob = der.parse_option_order_book(fields)
    assert ob.symbol == "201S1305"
    assert ob.best_ask == Decimal("2.35")
    assert ob.best_bid == Decimal("2.30")
    assert ob.best_ask_quantity == Decimal("40")
    assert ob.best_bid_quantity == Decimal("55")
    assert len(ob._raw) == len(der._OPTION_ORDER_BOOK_FIELDS)


def test_parse_stock_option_order_book_10_depth():
    fields = _fields(
        der._STOCK_OPTION_ORDER_BOOK_FIELDS,
        OPTN_SHRN_ISCD="A05930C", BSOP_HOUR="093000", OPTN_ASKP1="1.10",
        OPTN_BIDP1="1.05", OPTN_ASKP10="1.55", ASKP_RSQN1="3", BIDP_RSQN1="7",
    )
    ob = der.parse_stock_option_order_book(fields)
    assert ob.symbol == "A05930C"
    assert ob.best_ask == Decimal("1.10")
    assert ob.best_bid == Decimal("1.05")
    assert ob._raw["OPTN_ASKP10"] == "1.55"  # 10호가까지 _raw 에 있음
    assert len(ob._raw) == 68


def test_parse_stock_futures_order_book_10_depth():
    fields = _fields(
        der._STOCK_FUTURES_ORDER_BOOK_FIELDS,
        FUTS_SHRN_ISCD="111V06", BSOP_HOUR="093000", ASKP1="72500",
        BIDP1="72400", ASKP10="73000", ASKP_RSQN1="10", BIDP_RSQN1="9",
    )
    ob = der.parse_stock_futures_order_book(fields)
    assert ob.symbol == "111V06"
    assert ob.best_ask == Decimal("72500")
    assert ob.best_bid == Decimal("72400")
    assert ob._raw["ASKP10"] == "73000"
    assert len(ob._raw) == 68


# --------------------------------------------------------------------------- 선물 체결가


def test_parse_futures_tick():
    fields = _fields(
        der._FUTURES_TICK_FIELDS,
        FUTS_SHRN_ISCD="101W09", BSOP_HOUR="093000", FUTS_PRPR="330.45",
        PRDY_VRSS_SIGN="2", FUTS_PRDY_VRSS="1.20", FUTS_PRDY_CTRT="0.36",
        FUTS_OPRC="329.00", FUTS_HGPR="331.00", FUTS_LWPR="328.50", LAST_CNQN="3",
        ACML_VOL="120000", ACML_TR_PBMN="9500000000", HTS_THPR="330.60",
        MRKT_BASIS="0.85", CTTR="98.5", HTS_OTST_STPL_QTY="250000",
        FUTS_ASKP1="330.50", FUTS_BIDP1="330.40",
    )
    tick = der.parse_futures_tick(fields)
    assert isinstance(tick, der.FuturesTick)
    assert tick.symbol == "101W09"
    assert tick.current_price == Decimal("330.45")
    assert tick.change_sign == "2"
    assert tick.change == Decimal("1.20")
    assert tick.change_percent == Decimal("0.36")
    assert tick.open == Decimal("329.00")
    assert tick.high == Decimal("331.00")
    assert tick.low == Decimal("328.50")
    assert tick.trade_volume == Decimal("3")
    assert tick.accumulated_volume == Decimal("120000")
    assert tick.accumulated_value == Decimal("9500000000")
    assert tick.theoretical_price == Decimal("330.60")
    assert tick.market_basis == Decimal("0.85")
    assert tick.conclusion_strength == Decimal("98.5")
    assert tick.open_interest == Decimal("250000")
    assert tick.best_ask == Decimal("330.50")
    assert tick.best_bid == Decimal("330.40")
    assert tick._raw["DSCS_BLTR_ACML_QTY"] == "0"
    assert len(tick._raw) == 50


def test_parse_night_futures_tick_has_no_dscs_field():
    fields = _fields(
        der._NIGHT_FUTURES_TICK_FIELDS,
        FUTS_SHRN_ISCD="101W09", BSOP_HOUR="180000", FUTS_PRPR="331.00",
        FUTS_ASKP1="331.10", FUTS_BIDP1="330.90",
    )
    tick = der.parse_night_futures_tick(fields)
    assert tick.symbol == "101W09"
    assert tick.current_price == Decimal("331.00")
    assert tick.best_ask == Decimal("331.10")
    assert "DSCS_BLTR_ACML_QTY" not in tick._raw
    assert len(tick._raw) == 49


def test_parse_stock_futures_tick_uses_stock_price_keys():
    fields = _fields(
        der._STOCK_FUTURES_TICK_FIELDS,
        FUTS_SHRN_ISCD="111V06", BSOP_HOUR="093000", STCK_PRPR="72450",
        PRDY_VRSS_SIGN="5", PRDY_VRSS="-150", FUTS_PRDY_CTRT="-0.21",
        STCK_OPRC="72600", STCK_HGPR="72900", STCK_LWPR="72300",
        HTS_THPR="72500", ASKP1="72500", BIDP1="72400",
    )
    tick = der.parse_stock_futures_tick(fields)
    assert tick.symbol == "111V06"
    assert tick.current_price == Decimal("72450")
    assert tick.change_sign == "5"
    assert tick.change == Decimal("-150")
    assert tick.change_percent == Decimal("-0.21")
    assert tick.open == Decimal("72600")
    assert tick.high == Decimal("72900")
    assert tick.low == Decimal("72300")
    assert tick.best_ask == Decimal("72500")
    assert tick.best_bid == Decimal("72400")
    assert len(tick._raw) == 49


# --------------------------------------------------------------------------- 옵션 체결가


def test_parse_index_option_tick_greeks():
    fields = _fields(
        der._INDEX_OPTION_TICK_FIELDS,
        OPTN_SHRN_ISCD="201S1305", BSOP_HOUR="093000", OPTN_PRPR="2.34",
        PRDY_VRSS_SIGN="2", OPTN_PRDY_VRSS="0.12", PRDY_CTRT="5.41",
        OPTN_OPRC="2.20", OPTN_HGPR="2.40", OPTN_LWPR="2.15", LAST_CNQN="5",
        ACML_VOL="30000", ACML_TR_PBMN="4500000", HTS_THPR="2.35",
        HTS_OTST_STPL_QTY="18000", DELTA="0.48", GAMA="0.03", VEGA="0.12",
        THETA="-0.05", RHO="0.01", HTS_INTS_VLTL="14.2", CTTR="88.0",
        OPTN_ASKP1="2.35", OPTN_BIDP1="2.33",
    )
    tick = der.parse_index_option_tick(fields)
    assert isinstance(tick, der.OptionTick)
    assert tick.symbol == "201S1305"
    assert tick.current_price == Decimal("2.34")
    assert tick.change_sign == "2"
    assert tick.change == Decimal("0.12")
    assert tick.change_percent == Decimal("5.41")
    assert tick.open == Decimal("2.20")
    assert tick.high == Decimal("2.40")
    assert tick.low == Decimal("2.15")
    assert tick.trade_volume == Decimal("5")
    assert tick.theoretical_price == Decimal("2.35")
    assert tick.open_interest == Decimal("18000")
    assert tick.delta == Decimal("0.48")
    assert tick.gamma == Decimal("0.03")
    assert tick.vega == Decimal("0.12")
    assert tick.theta == Decimal("-0.05")
    assert tick.rho == Decimal("0.01")
    assert tick.implied_volatility == Decimal("14.2")
    assert tick.conclusion_strength == Decimal("88.0")
    assert tick.best_ask == Decimal("2.35")
    assert tick.best_bid == Decimal("2.33")
    assert tick._raw["DSCS_LRQN_VOL"] == "0"
    assert len(tick._raw) == 58


def test_parse_night_option_tick():
    fields = _fields(
        der._NIGHT_OPTION_TICK_FIELDS,
        OPTN_SHRN_ISCD="201S1305", BSOP_HOUR="180000", OPTN_PRPR="2.50",
        DELTA="0.51", OPTN_ASKP1="2.51", OPTN_BIDP1="2.49",
    )
    tick = der.parse_night_option_tick(fields)
    assert tick.symbol == "201S1305"
    assert tick.current_price == Decimal("2.50")
    assert tick.delta == Decimal("0.51")
    assert len(tick._raw) == 56


def test_parse_stock_option_tick():
    fields = _fields(
        der._STOCK_OPTION_TICK_FIELDS,
        OPTN_SHRN_ISCD="A05930C", BSOP_HOUR="093000", OPTN_PRPR="1.08",
        THETA="-0.02", OPTN_ASKP1="1.10", OPTN_BIDP1="1.05",
    )
    tick = der.parse_stock_option_tick(fields)
    assert tick.symbol == "A05930C"
    assert tick.current_price == Decimal("1.08")
    assert tick.theta == Decimal("-0.02")
    assert tick.best_ask == Decimal("1.10")
    assert len(tick._raw) == 53


# --------------------------------------------------------------------------- 예상체결


def test_parse_stock_futures_expected():
    fields = _fields(
        der._STOCK_FUTURES_EXPECTED_FIELDS,
        FUTS_SHRN_ISCD="111V06", BSOP_HOUR="153000", ANTC_CNPR="72500",
        ANTC_CNTG_VRSS="100", ANTC_CNTG_VRSS_SIGN="2", ANTC_CNTG_PRDY_CTRT="0.14",
        ANTC_MKOP_CLS_CODE="1", ANTC_CNQN="1200",
    )
    exp = der.parse_stock_futures_expected(fields)
    assert isinstance(exp, der.ExpectedConclusion)
    assert exp.symbol == "111V06"
    assert exp.time == "153000"
    assert exp.expected_price == Decimal("72500")
    assert exp.expected_change == Decimal("100")
    assert exp.expected_change_sign == "2"
    assert exp.expected_change_percent == Decimal("0.14")
    assert exp.market_operation_code == "1"
    assert exp.expected_volume == Decimal("1200")
    assert len(exp._raw) == 8


def test_parse_night_option_expected():
    fields = _fields(
        der._NIGHT_OPTION_EXPECTED_FIELDS,
        OPTN_SHRN_ISCD="201S1305", BSOP_HOUR="180000", ANTC_CNPR="2.40",
        ANTC_CNQN="300",
    )
    exp = der.parse_night_option_expected(fields)
    assert exp.symbol == "201S1305"
    assert exp.expected_price == Decimal("2.40")
    assert exp.expected_volume == Decimal("300")
    assert len(exp._raw) == 8


def test_parse_stock_option_expected_has_no_volume():
    fields = _fields(
        der._STOCK_OPTION_EXPECTED_FIELDS,
        OPTN_SHRN_ISCD="A05930C", BSOP_HOUR="153000", ANTC_CNPR="1.10",
        ANTC_CNTG_VRSS_SIGN="5",
    )
    exp = der.parse_stock_option_expected(fields)
    assert exp.symbol == "A05930C"
    assert exp.expected_price == Decimal("1.10")
    assert exp.expected_change_sign == "5"
    assert exp.expected_volume == Decimal(0)  # 예상수량 필드 없음 -> 0
    assert "ANTC_CNQN" not in exp._raw
    assert len(exp._raw) == 7


# --------------------------------------------------------------------------- 체결통보(암호화)


def test_parse_execution_notice():
    fields = _fields(
        der._EXECUTION_NOTICE_FIELDS,
        CUST_ID="CUST01", ACNT_NO="12345678", ODER_NO="0001", OODER_NO="0000",
        SELN_BYOV_CLS="02", STCK_SHRN_ISCD="101W09", CNTG_QTY="2", CNTG_UNPR="330.45",
        STCK_CNTG_HOUR="093015", RFUS_YN="N", CNTG_YN="2", ACPT_YN="Y",
        ODER_QTY="2", ACNT_NAME="홍길동", CNTG_ISNM="KOSPI200 F 202509",
        ORDER_PRC="330.45",
    )
    note = der.parse_execution_notice(fields)
    assert isinstance(note, der.ExecutionNotice)
    assert note.customer_id == "CUST01"
    assert note.account_number == "12345678"
    assert note.order_number == "0001"
    assert note.sell_buy == "02"
    assert note.symbol == "101W09"
    assert note.filled_quantity == Decimal("2")
    assert note.filled_price == Decimal("330.45")
    assert note.time == "093015"
    assert note.rejected is False
    assert note.fill_status == "2"
    assert note.accepted is True
    assert note.order_quantity == Decimal("2")
    assert note.symbol_name == "KOSPI200 F 202509"
    assert note.account_name == "홍길동"
    assert note.order_price == Decimal("330.45")
    assert len(note._raw) == 22


def test_parse_night_execution_notice_has_no_order_price():
    fields = _fields(
        der._NIGHT_EXECUTION_NOTICE_FIELDS,
        CUST_ID="CUST01", ACNT_NO="12345678", ODER_NO="0002", SELN_BYOV_CLS="01",
        STCK_SHRN_ISCD="101W09", CNTG_QTY="1", CNTG_UNPR="331.00",
        STCK_CNTG_HOUR="181500", RFUS_YN="N", CNTG_YN="2", ACPT_YN="Y",
    )
    note = der.parse_night_execution_notice(fields)
    assert note.customer_id == "CUST01"
    assert note.sell_buy == "01"
    assert note.filled_price == Decimal("331.00")
    assert note.rejected is False
    assert note.accepted is True
    assert note.order_price == Decimal(0)  # 야간 통보엔 주문가격 없음 -> 0
    assert "ORDER_PRC" not in note._raw
    assert len(note._raw) == 19


# --------------------------------------------------------------------------- 레지스트리


def test_registry_shared_futures_order_book_parser():
    for tr_id in ("H0IFASP0", "H0CFASP0", "H0MFASP0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 38
        assert spec.parser is der.parse_futures_order_book


def test_registry_shared_futures_tick_parser():
    for tr_id in ("H0IFCNT0", "H0CFCNT0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 50
        assert spec.parser is der.parse_futures_tick


def test_registry_execution_notices_marked_encrypted():
    for tr_id in ("H0IFCNI0", "H0MFCNI0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.encrypted is True


def test_registry_night_option_notice_reuses_night_parser():
    # KRX야간옵션 체결통보(H0EUCNI0)는 야간선물(H0MFCNI0)과 동일 19필드 레이아웃을 공유한다.
    spec = _registry.lookup("H0EUCNI0")
    assert spec is not None
    assert spec.field_count == 19
    assert spec.parser is der.parse_night_execution_notice
    assert spec.encrypted is True


def test_parse_night_option_notice_via_shared_parser():
    fields = _fields(
        der._NIGHT_EXECUTION_NOTICE_FIELDS,
        CUST_ID="CUST01", ACNT_NO="12345678", ODER_NO="0003", SELN_BYOV_CLS="02",
        STCK_SHRN_ISCD="201S1305", CNTG_QTY="1", CNTG_UNPR="2.50",
        STCK_CNTG_HOUR="181500", RFUS_YN="N", CNTG_YN="2", ACPT_YN="Y",
    )
    note = der.parse_night_execution_notice(fields)
    assert note.symbol == "201S1305"
    assert note.filled_price == Decimal("2.50")
    assert note.accepted is True
    assert note.order_price == Decimal(0)  # 야간 통보엔 주문가격 없음
    assert len(note._raw) == 19


def test_registry_field_counts_match_layouts():
    expected = {
        "H0ZOASP0": 68, "H0ZFASP0": 68, "H0IOASP0": 38, "H0EUASP0": 38,
        "H0MFCNT0": 49, "H0ZFCNT0": 49, "H0EUCNT0": 56, "H0IOCNT0": 58,
        "H0ZOCNT0": 53, "H0EUANC0": 8, "H0ZFANC0": 8, "H0ZOANC0": 7,
        "H0IFCNI0": 22, "H0MFCNI0": 19, "H0EUCNI0": 19,
    }
    for tr_id, count in expected.items():
        spec = _registry.lookup(tr_id)
        assert spec is not None, tr_id
        assert spec.field_count == count, tr_id

"""해외 자산군 실시간 파서/엔티티 테스트 -- 해외주식/해외선물옵션.

각 TR 레이아웃대로 원장 필드순이 헤드라인 필드에 매핑되는지, ``_raw`` 가 원장 전체 키를 담는지,
레지스트리 등록(암호화 플래그 포함)이 맞는지 검증. 순수 함수 테스트, 네트워크 없음.
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.parsers.overseas import (
    _ASIA_ORDERBOOK_FIELDS,
    _DELAYED_TRADE_FIELDS,
    _EXECUTION_NOTICE_FIELDS,
    _FUTURES_EXECUTION_NOTICE_FIELDS,
    _FUTURES_ORDER_NOTICE_FIELDS,
    _FUTURES_ORDERBOOK_FIELDS,
    _FUTURES_TRADE_FIELDS,
    _ORDERBOOK_FIELDS,
    OverseasAsiaDelayedOrderBook,
    OverseasDelayedTradeTick,
    OverseasDerivativeExecutionNotice,
    OverseasDerivativeOrderBook,
    OverseasDerivativeOrderNotice,
    OverseasDerivativeTradeTick,
    OverseasExecutionNotice,
    OverseasOrderBook,
    parse_asia_orderbook,
    parse_delayed_trade_tick,
    parse_execution_notice,
    parse_futures_execution_notice,
    parse_futures_order_notice,
    parse_futures_orderbook,
    parse_futures_trade_tick,
    parse_orderbook,
)


def _blank(fields: tuple[str, ...]) -> list[str]:
    return ["0"] * len(fields)


# ---------------------------------------------------------------------------
# HDFSASP0 -- OverseasOrderBook
# ---------------------------------------------------------------------------


def _orderbook_fields() -> list[str]:
    fields = _blank(_ORDERBOOK_FIELDS)
    fields[0] = "DNASAAPL"  # RSYM
    fields[1] = "AAPL"      # SYMB
    fields[2] = "4"         # ZDIV
    fields[3] = "20260814"  # XYMD
    fields[4] = "093000"    # XHMS
    fields[5] = "20260814"  # KYMD
    fields[6] = "223000"    # KHMS
    fields[7] = "1500"      # BVOL (total bid volume)
    fields[8] = "2200"      # AVOL (total ask volume)
    fields[11] = "231.50"   # PBID1
    fields[12] = "231.55"   # PASK1
    fields[13] = "300"      # VBID1
    fields[14] = "450"      # VASK1
    return fields


def test_parse_orderbook_headline():
    ob = parse_orderbook(_orderbook_fields())
    assert isinstance(ob, OverseasOrderBook)
    assert ob.symbol == "AAPL"
    assert ob.realtime_symbol == "DNASAAPL"
    assert ob.decimal_places == "4"
    assert ob.local_date == "20260814"
    assert ob.local_time == "093000"
    assert ob.korea_date == "20260814"
    assert ob.korea_time == "223000"
    assert ob.total_bid_quantity == Decimal(1500)
    assert ob.total_ask_quantity == Decimal(2200)
    assert ob.best_bid == Decimal("231.50")
    assert ob.best_ask == Decimal("231.55")
    assert ob.best_bid_quantity == Decimal(300)
    assert ob.best_ask_quantity == Decimal(450)
    # F46: 호가 잔량은 int(거래소 집계 = 정수), 가격은 Decimal 유지.
    assert isinstance(ob.total_bid_quantity, int) and isinstance(ob.best_ask_quantity, int)
    assert isinstance(ob.best_bid, Decimal) and isinstance(ob.best_ask, Decimal)


def test_orderbook_71_unique_fields():
    ob = parse_orderbook(_orderbook_fields())
    # 와이어 레이아웃은 71 유니크 필드(헤더 11 + 10단계 x 6), 2026-10-08 미국장 라이브 검증.
    # 리터럴로 고정 -- production 상수에 의존하면 중복 블록 재유입(77)도 통과해 회귀를 못 잡는다.
    assert len(_ORDERBOOK_FIELDS) == len(set(_ORDERBOOK_FIELDS)) == 71
    assert len(ob._raw) == 71
    assert ob._raw["PBID10"] == "0"
    assert ob._raw["SYMB"] == "AAPL"


def test_registry_orderbook():
    spec = _registry.lookup("HDFSASP0")
    assert spec is not None
    assert spec.field_count == 71
    assert spec.parser is parse_orderbook
    assert spec.encrypted is False


# ---------------------------------------------------------------------------
# HDFSASP1 -- OverseasAsiaDelayedOrderBook (1단계)
# ---------------------------------------------------------------------------


def _asia_orderbook_fields() -> list[str]:
    fields = _blank(_ASIA_ORDERBOOK_FIELDS)
    fields[0] = "TSE7203"   # RSYM
    fields[1] = "7203"      # SYMB
    fields[2] = "0"         # ZDIV
    fields[3] = "20260814"  # XYMD
    fields[4] = "093000"    # XHMS
    fields[5] = "20260814"  # KYMD
    fields[6] = "093000"    # KHMS
    fields[7] = "1500"      # BVOL (total bid volume)
    fields[8] = "2200"      # AVOL (total ask volume)
    fields[11] = "2850.0"   # PBID1
    fields[12] = "2851.0"   # PASK1
    fields[13] = "300"      # VBID1
    fields[14] = "450"      # VASK1
    return fields


def test_parse_asia_orderbook_headline():
    ob = parse_asia_orderbook(_asia_orderbook_fields())
    assert isinstance(ob, OverseasAsiaDelayedOrderBook)
    assert ob.symbol == "7203"
    assert ob.realtime_symbol == "TSE7203"
    assert ob.decimal_places == "0"
    assert ob.local_date == "20260814"
    assert ob.local_time == "093000"
    assert ob.korea_date == "20260814"
    assert ob.korea_time == "093000"
    assert ob.total_bid_quantity == Decimal(1500)
    assert ob.total_ask_quantity == Decimal(2200)
    assert ob.best_bid == Decimal("2850.0")
    assert ob.best_ask == Decimal("2851.0")
    assert ob.best_bid_quantity == Decimal(300)
    assert ob.best_ask_quantity == Decimal(450)
    assert isinstance(ob.total_ask_quantity, int) and isinstance(ob.best_bid_quantity, int)
    assert isinstance(ob.best_bid, Decimal)


def test_asia_orderbook_raw_and_registry():
    ob = parse_asia_orderbook(_asia_orderbook_fields())
    assert len(ob._raw) == len(_ASIA_ORDERBOOK_FIELDS) == 17
    assert ob._raw["DASK1"] == "0"
    assert ob._raw["SYMB"] == "7203"
    spec = _registry.lookup("HDFSASP1")
    assert spec is not None
    assert spec.field_count == len(_ASIA_ORDERBOOK_FIELDS)
    assert spec.parser is parse_asia_orderbook
    assert spec.encrypted is False


# ---------------------------------------------------------------------------
# HDFSCNT0 -- OverseasDelayedTradeTick
# ---------------------------------------------------------------------------


def _delayed_trade_fields() -> list[str]:
    fields = _blank(_DELAYED_TRADE_FIELDS)
    fields[1] = "AAPL"      # SYMB
    fields[4] = "20260814"  # XYMD
    fields[5] = "093000"    # XHMS
    fields[6] = "20260814"  # KYMD
    fields[7] = "223000"    # KHMS
    fields[8] = "230.00"    # OPEN
    fields[9] = "232.00"    # HIGH
    fields[10] = "229.50"   # LOW
    fields[11] = "231.50"   # LAST
    fields[12] = "2"        # SIGN
    fields[13] = "1.50"     # DIFF
    fields[14] = "0.65"     # RATE
    fields[15] = "231.45"   # PBID
    fields[16] = "231.55"   # PASK
    fields[19] = "10"       # EVOL (trade volume)
    fields[20] = "50000"    # TVOL
    fields[21] = "11500000" # TAMT
    fields[24] = "115.2"    # STRN
    fields[25] = "1"        # MTYP
    return fields


def test_parse_delayed_trade_tick_headline():
    tick = parse_delayed_trade_tick(_delayed_trade_fields())
    assert isinstance(tick, OverseasDelayedTradeTick)
    assert tick.symbol == "AAPL"
    assert tick.local_date == "20260814"
    assert tick.local_time == "093000"
    assert tick.korea_date == "20260814"
    assert tick.korea_time == "223000"
    assert tick.open == Decimal("230.00")
    assert tick.high == Decimal("232.00")
    assert tick.low == Decimal("229.50")
    assert tick.current_price == Decimal("231.50")
    assert tick.change_sign == "2"
    assert tick.change == Decimal("1.50")
    assert tick.change_percent == Decimal("0.65")
    assert tick.best_bid == Decimal("231.45")
    assert tick.best_ask == Decimal("231.55")
    assert tick.trade_volume == Decimal(10)
    assert tick.accumulated_volume == Decimal(50000)
    assert isinstance(tick.trade_volume, int) and isinstance(tick.accumulated_volume, int)
    assert tick.accumulated_value == Decimal(11500000)
    assert tick.conclusion_strength == Decimal("115.2")
    assert tick.market_type == "1"


def test_delayed_trade_raw_and_registry():
    tick = parse_delayed_trade_tick(_delayed_trade_fields())
    assert len(tick._raw) == 26                             # 라이브 검증(2026-10-08): HDFSCNT0 = 26 필드
    assert tick._raw["LAST"] == "231.50"
    spec = _registry.lookup("HDFSCNT0")
    assert spec is not None
    assert spec.field_count == 26
    assert spec.parser is parse_delayed_trade_tick
    assert spec.encrypted is False


def test_delayed_trade_tick_downtick_signs_both_change_fields():
    # 하락 부호(SIGN=5) + 부호 없는 DIFF/RATE -> change/change_percent 둘 다 음수. 부호가 SIGN
    # 키에서 왔음을 고정한다(이미 음수인 크기면 멱등성으로 통과해 키 배선을 검증하지 못한다).
    fields = _delayed_trade_fields()
    fields[12] = "5"  # SIGN 하락
    tick = parse_delayed_trade_tick(fields)
    assert tick.change_sign == "5"
    assert tick.change == Decimal("-1.50")
    assert tick.change_percent == Decimal("-0.65")


# ---------------------------------------------------------------------------
# H0GSCNI0 -- OverseasExecutionNotice (encrypted)
# ---------------------------------------------------------------------------


def _execution_notice_fields() -> list[str]:
    fields = _blank(_EXECUTION_NOTICE_FIELDS)
    fields[0] = "CUST01"    # CUST_ID
    fields[1] = "50012345"  # ACNT_NO
    fields[2] = "0000123"   # ODER_NO
    fields[3] = "0000000"   # OODER_NO
    fields[4] = "02"        # SELN_BYOV_CLS
    fields[7] = "AAPL"      # STCK_SHRN_ISCD
    fields[8] = "5"         # CNTG_QTY
    fields[9] = "231.50"    # CNTG_UNPR
    fields[10] = "223015"   # STCK_CNTG_HOUR
    fields[11] = "0"        # RFUS_YN 0:승인 1:거부
    fields[12] = "2"        # CNTG_YN 1:주문/정정/취소/거부 2:체결
    fields[13] = "2"        # ACPT_YN 1:주문접수 2:확인 3:취소
    fields[14] = "01234"    # BRNC_NO
    fields[15] = "10"       # ODER_QTY
    fields[17] = "APPLE INC"  # CNTG_ISNM
    return fields


def test_parse_execution_notice_headline():
    notice = parse_execution_notice(_execution_notice_fields())
    assert isinstance(notice, OverseasExecutionNotice)
    assert notice.customer_id == "CUST01"
    assert notice.account_number == "50012345"
    assert notice.order_id == "0000123"
    assert notice.original_order_id == "0000000"
    assert notice.sell_buy == "02"
    assert notice.symbol == "AAPL"
    assert notice.name == "APPLE INC"
    assert notice.filled_quantity == Decimal(5)
    assert isinstance(notice.filled_quantity, Decimal)  # 주문/체결 수량은 Decimal 유지(분수주식)
    assert notice.filled_price == Decimal("231.50")
    assert notice.time == "223015"
    assert notice.order_quantity == Decimal(10)
    assert notice.rejected is False
    assert notice.fill_status == "2"
    assert notice.accepted_status == "2"
    assert notice.branch_number == "01234"


def test_execution_notice_flags_use_ledger_codes_not_y_n():
    # H0GSCNI0: RFUS_YN 0:승인 1:거부, CNTG_YN 1:주문/정정/취소/거부 2:체결,
    # ACPT_YN 1:주문접수 2:확인 3:취소(FOK/IOC) -- 어느 것도 Y/N 이 아니다.
    fields = _execution_notice_fields()
    fields[11] = "1"  # RFUS_YN 거부
    fields[12] = "1"  # CNTG_YN 접수(미체결)
    fields[13] = "3"  # ACPT_YN 취소
    notice = parse_execution_notice(fields)
    assert notice.rejected is True
    assert notice.fill_status == "1"
    assert notice.accepted_status == "3"


def test_execution_notice_raw_and_registry():
    notice = parse_execution_notice(_execution_notice_fields())
    assert len(notice._raw) == len(_EXECUTION_NOTICE_FIELDS)
    assert notice._raw["CNTG_UNPR12"] == "0"
    spec = _registry.lookup("H0GSCNI0")
    assert spec is not None
    assert spec.field_count == len(_EXECUTION_NOTICE_FIELDS) == 25  # 리터럴 고정(field_count=len 파생이라 tautology 방지; 라이브 미검증 레이아웃의 구조적 드리프트 포착)
    assert spec.parser is parse_execution_notice
    assert spec.encrypted is True


# ---------------------------------------------------------------------------
# HDFFF020 -- OverseasDerivativeTradeTick
# ---------------------------------------------------------------------------


def _futures_trade_fields() -> list[str]:
    fields = _blank(_FUTURES_TRADE_FIELDS)
    fields[0] = "ESU26"     # SERIES_CD
    fields[1] = "20260814"  # BSNS_DATE
    fields[7] = "20260814"  # RECV_DATE
    fields[8] = "223000"    # RECV_TIME
    fields[6] = "5500.25"   # PREV_PRICE
    fields[10] = "5510.50"  # LAST_PRICE
    fields[11] = "3"        # LAST_QNTT
    fields[12] = "10.25"    # PREV_DIFF_PRICE
    fields[13] = "0.19"     # PREV_DIFF_RATE
    fields[14] = "5502.00"  # OPEN_PRICE
    fields[15] = "5515.00"  # HIGH_PRICE
    fields[16] = "5498.00"  # LOW_PRICE
    fields[17] = "120000"   # VOL
    fields[18] = "2"        # PREV_SIGN
    fields[19] = "1"        # QUOTSIGN
    fields[21] = "5501.00"  # PSTTL_PRICE
    return fields


def test_parse_futures_trade_tick_headline():
    tick = parse_futures_trade_tick(_futures_trade_fields())
    assert isinstance(tick, OverseasDerivativeTradeTick)
    assert tick.symbol == "ESU26"
    assert tick.business_date == "20260814"
    assert tick.recv_date == "20260814"
    assert tick.recv_time == "223000"
    assert tick.prev_close == Decimal("5500.25")
    assert tick.current_price == Decimal("5510.50")
    assert tick.trade_volume == Decimal(3)
    assert isinstance(tick.trade_volume, int)
    assert tick.change == Decimal("10.25")
    assert tick.change_percent == Decimal("0.19")
    assert tick.open == Decimal("5502.00")
    assert tick.high == Decimal("5515.00")
    assert tick.low == Decimal("5498.00")
    assert tick.accumulated_volume == Decimal(120000)
    assert isinstance(tick.accumulated_volume, int)
    assert tick.change_sign == "2"
    assert tick.trade_sign == "1"
    assert tick.prev_settlement_price == Decimal("5501.00")


def test_futures_trade_raw_and_registry():
    tick = parse_futures_trade_tick(_futures_trade_fields())
    assert len(tick._raw) == len(_FUTURES_TRADE_FIELDS)
    assert tick._raw["LAST_PRICE"] == "5510.50"
    spec = _registry.lookup("HDFFF020")
    assert spec is not None
    assert spec.field_count == len(_FUTURES_TRADE_FIELDS) == 25  # 리터럴 고정(field_count=len 파생이라 tautology 방지; 라이브 미검증 레이아웃의 구조적 드리프트 포착)
    assert spec.parser is parse_futures_trade_tick
    assert spec.encrypted is False


def test_futures_trade_tick_downtick_signs_both_change_fields():
    # 하락 부호(PREV_SIGN=5) + 부호 없는 PREV_DIFF_PRICE/RATE -> change/change_percent 둘 다 음수.
    # 부호가 PREV_SIGN 키에서 왔음을 고정한다(멱등성 통과로 가려지지 않게 크기는 양수로 둔다).
    fields = _futures_trade_fields()
    fields[18] = "5"  # PREV_SIGN 하락
    tick = parse_futures_trade_tick(fields)
    assert tick.change_sign == "5"
    assert tick.change == Decimal("-10.25")
    assert tick.change_percent == Decimal("-0.19")


# ---------------------------------------------------------------------------
# HDFFF010 -- OverseasDerivativeOrderBook
# ---------------------------------------------------------------------------


def _futures_orderbook_fields() -> list[str]:
    fields = _blank(_FUTURES_ORDERBOOK_FIELDS)
    fields[0] = "ESU26"     # SERIES_CD
    fields[1] = "20260814"  # RECV_DATE
    fields[2] = "223000"    # RECV_TIME
    fields[3] = "5500.25"   # PREV_PRICE
    fields[4] = "12"        # BID_QNTT_1
    fields[6] = "5510.00"   # BID_PRICE_1
    fields[7] = "8"         # ASK_QNTT_1
    fields[9] = "5510.50"   # ASK_PRICE_1
    fields[34] = "5501.00"  # STTL_PRICE
    return fields


def test_parse_futures_orderbook_headline():
    ob = parse_futures_orderbook(_futures_orderbook_fields())
    assert isinstance(ob, OverseasDerivativeOrderBook)
    assert ob.symbol == "ESU26"
    assert ob.recv_date == "20260814"
    assert ob.recv_time == "223000"
    assert ob.prev_close == Decimal("5500.25")
    assert ob.best_bid == Decimal("5510.00")
    assert ob.best_bid_quantity == Decimal(12)
    assert isinstance(ob.best_bid_quantity, int)
    assert ob.best_ask == Decimal("5510.50")
    assert ob.best_ask_quantity == Decimal(8)
    assert ob.settlement_price == Decimal("5501.00")


def test_futures_orderbook_raw_and_registry():
    ob = parse_futures_orderbook(_futures_orderbook_fields())
    assert len(ob._raw) == len(_FUTURES_ORDERBOOK_FIELDS)
    assert ob._raw["ASK_PRICE_1"] == "5510.50"
    spec = _registry.lookup("HDFFF010")
    assert spec is not None
    assert spec.field_count == len(_FUTURES_ORDERBOOK_FIELDS) == 35  # 리터럴 고정(field_count=len 파생이라 tautology 방지; 라이브 미검증 레이아웃의 구조적 드리프트 포착)
    assert spec.parser is parse_futures_orderbook
    assert spec.encrypted is False


# ---------------------------------------------------------------------------
# HDFFF1C0 -- OverseasDerivativeOrderNotice (encrypted)
# ---------------------------------------------------------------------------


def _futures_order_notice_fields() -> list[str]:
    fields = _blank(_FUTURES_ORDER_NOTICE_FIELDS)
    fields[0] = "USER01"    # USER_ID
    fields[1] = "50012345"  # ACCT_NO
    fields[2] = "20260814"  # ORD_DT
    fields[3] = "0000123"   # ODNO
    fields[4] = "20260813"  # ORGN_ORD_DT
    fields[5] = "0000100"   # ORGN_ODNO
    fields[6] = "ESU26"     # SERIES
    fields[8] = "02"        # SLL_BUY_DVSN_CD
    fields[12] = "2"        # ORD_QTY
    fields[13] = "5510.00"  # FM_LMT_PRIC
    fields[14] = "5490.00"  # FM_STOP_ORD_PRIC
    fields[15] = "1"        # TOT_CCLD_QTY
    fields[16] = "5509.50"  # TOT_CCLD_UV
    fields[17] = "1"        # ORD_REMQ
    fields[23] = "USD"      # CRCY_CD
    fields[24] = "Y"        # LQD_YN
    return fields


def test_parse_futures_order_notice_headline():
    notice = parse_futures_order_notice(_futures_order_notice_fields())
    assert isinstance(notice, OverseasDerivativeOrderNotice)
    assert notice.user_id == "USER01"
    assert notice.account_number == "50012345"
    assert notice.order_date == "20260814"
    assert notice.order_id == "0000123"
    assert notice.original_order_date == "20260813"
    assert notice.original_order_id == "0000100"
    assert notice.symbol == "ESU26"
    assert notice.sell_buy == "02"
    assert notice.order_quantity == Decimal(2)
    assert notice.limit_price == Decimal("5510.00")
    assert notice.stop_price == Decimal("5490.00")
    assert notice.total_filled_quantity == Decimal(1)
    assert isinstance(notice.order_quantity, Decimal) and isinstance(notice.total_filled_quantity, Decimal)
    assert notice.average_filled_price == Decimal("5509.50")
    assert notice.remaining_quantity == Decimal(1)
    assert notice.currency == "USD"
    assert notice.liquidation is True


def test_futures_order_notice_raw_and_registry():
    notice = parse_futures_order_notice(_futures_order_notice_fields())
    assert len(notice._raw) == len(_FUTURES_ORDER_NOTICE_FIELDS)
    assert notice._raw["AUTO_ORD_DVSN_CD"] == "0"
    spec = _registry.lookup("HDFFF1C0")
    assert spec is not None
    assert spec.field_count == len(_FUTURES_ORDER_NOTICE_FIELDS) == 33  # 리터럴 고정(field_count=len 파생이라 tautology 방지; 라이브 미검증 레이아웃의 구조적 드리프트 포착)
    assert spec.parser is parse_futures_order_notice
    assert spec.encrypted is True


# ---------------------------------------------------------------------------
# HDFFF2C0 -- OverseasDerivativeExecutionNotice (encrypted)
# ---------------------------------------------------------------------------


def _futures_execution_notice_fields() -> list[str]:
    fields = _blank(_FUTURES_EXECUTION_NOTICE_FIELDS)
    fields[0] = "USER01"    # USER_ID
    fields[1] = "50012345"  # ACCT_NO
    fields[2] = "20260814"  # ORD_DT
    fields[3] = "0000123"   # ODNO
    fields[6] = "ESU26"     # SERIES
    fields[8] = "02"        # SLL_BUY_DVSN_CD
    fields[12] = "2"        # ORD_QTY
    fields[23] = "20260814" # CCLD_DT
    fields[24] = "0000999"  # CCNO
    fields[26] = "1"        # CCLD_QTY
    fields[27] = "5509.50"  # FM_CCLD_PRIC
    fields[28] = "USD"      # CRCY_CD
    fields[29] = "2.50"     # TRST_FEE
    fields[30] = "Y"        # ORD_MDIA_ONLINE_YN
    fields[31] = "5509.50"  # FM_CCLD_AMT
    return fields


def test_parse_futures_execution_notice_headline():
    notice = parse_futures_execution_notice(_futures_execution_notice_fields())
    assert isinstance(notice, OverseasDerivativeExecutionNotice)
    assert notice.user_id == "USER01"
    assert notice.account_number == "50012345"
    assert notice.order_date == "20260814"
    assert notice.order_id == "0000123"
    assert notice.symbol == "ESU26"
    assert notice.sell_buy == "02"
    assert notice.order_quantity == Decimal(2)
    assert notice.filled_date == "20260814"
    assert notice.fill_number == "0000999"
    assert notice.filled_quantity == Decimal(1)
    assert isinstance(notice.order_quantity, Decimal) and isinstance(notice.filled_quantity, Decimal)
    assert notice.filled_price == Decimal("5509.50")
    assert notice.filled_amount == Decimal("5509.50")
    assert notice.commission == Decimal("2.50")
    assert notice.currency == "USD"
    assert notice.online is True


def test_futures_execution_notice_raw_and_registry():
    notice = parse_futures_execution_notice(_futures_execution_notice_fields())
    assert len(notice._raw) == len(_FUTURES_EXECUTION_NOTICE_FIELDS)
    assert notice._raw["FM_CCLD_PRIC"] == "5509.50"
    spec = _registry.lookup("HDFFF2C0")
    assert spec is not None
    assert spec.field_count == len(_FUTURES_EXECUTION_NOTICE_FIELDS) == 33  # 리터럴 고정(field_count=len 파생이라 tautology 방지; 라이브 미검증 레이아웃의 구조적 드리프트 포착)
    assert spec.parser is parse_futures_execution_notice
    assert spec.encrypted is True

"""실시간 파서/엔티티 테스트 -- 국내지수 예상체결(H0UPANC0) / 프로그램매매(H0UPPGM0).

원장 필드 레이아웃대로 파싱되는지, ``_raw`` 에 전체 Element 키가 담기는지, 레지스트리 등록이
되는지 검증. 순수(네트워크 없음).
"""

from __future__ import annotations

from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.parsers.index import (
    IndexExpectedConclusion,
    IndexProgramTrade,
    IndexTick,
    parse_expected_conclusion,
    parse_index_tick,
    parse_program_trade,
)


def test_parse_index_tick_shares_layout_and_registers():
    # H0UPCNT0(체결)은 H0UPANC0(예상체결)과 동일한 30필드 레이아웃을 공유한다.
    fields = _expected_conclusion_fields()
    fields[22] = "3"  # UPLM_ISSU_CNT -> upper_limit_count
    fields[26] = "1"  # LSLM_ISSU_CNT -> lower_limit_count
    tick = parse_index_tick(fields)
    assert isinstance(tick, IndexTick)
    assert tick.sector_code == "0001"
    assert tick.time == "153000"
    assert tick.current_index == Decimal("2650.55")
    assert tick.change == Decimal("12.30")
    assert tick.change_percent == Decimal("0.47")
    assert tick.rising_count == Decimal(480)
    assert tick.falling_count == Decimal(360)
    assert tick.upper_limit_count == Decimal(3)
    assert tick.lower_limit_count == Decimal(1)
    assert len(tick._raw) == 30
    spec = _registry.lookup("H0UPCNT0")
    assert spec is not None and spec.field_count == 30


def _expected_conclusion_fields() -> list[str]:
    fields = ["0"] * 30
    fields[0] = "0001"      # sector_code
    fields[1] = "153000"    # time
    fields[2] = "2650.55"   # expected_index
    fields[3] = "2"         # change_sign (상승)
    fields[4] = "12.30"     # change
    fields[5] = "500000"    # accumulated_volume
    fields[6] = "8000000"   # accumulated_value
    fields[9] = "0.47"      # change_percent
    fields[10] = "2640.00"  # open
    fields[11] = "2661.10"  # high
    fields[12] = "2638.20"  # low
    fields[23] = "480"      # rising_count
    fields[24] = "60"       # unchanged_count
    fields[25] = "360"      # falling_count
    return fields


def _program_trade_fields() -> list[str]:
    fields = ["0"] * 88
    fields[0] = "0001"       # sector_code
    fields[1] = "153000"     # time
    fields[66] = "1000"      # TOTAL_SELN_QTY -> total_sell_quantity
    fields[70] = "1200"      # SHNU_CNTG_SMTN -> total_buy_quantity
    fields[74] = "200"       # WHOL_NTBY_QTY -> whole_net_buy_volume
    fields[76] = "5500000"   # WHOL_NTBY_TR_PBMN -> whole_net_buy_value
    fields[26] = "80"        # ARBT_SMTN_NTBY_QTY -> arbitrage_net_buy_volume
    fields[38] = "120"       # NABT_SMTN_NTBY_QTY -> nonarbitrage_net_buy_volume
    fields[86] = "900000"    # ACML_VOL -> accumulated_volume
    fields[87] = "45000000"  # ACML_TR_PBMN -> accumulated_value
    return fields


def test_parse_expected_conclusion_maps_headline_fields():
    ec = parse_expected_conclusion(_expected_conclusion_fields())
    assert isinstance(ec, IndexExpectedConclusion)
    assert ec.sector_code == "0001"
    assert ec.time == "153000"
    assert ec.expected_index == Decimal("2650.55")
    assert ec.change_sign == "2"
    assert ec.change == Decimal("12.30")
    assert ec.change_percent == Decimal("0.47")
    assert ec.accumulated_volume == Decimal(500000)
    assert ec.accumulated_value == Decimal(8000000)
    assert ec.open == Decimal("2640.00")
    assert ec.high == Decimal("2661.10")
    assert ec.low == Decimal("2638.20")
    assert ec.rising_count == Decimal(480)
    assert ec.unchanged_count == Decimal(60)
    assert ec.falling_count == Decimal(360)


def test_expected_conclusion_raw_has_all_ledger_keys():
    ec = parse_expected_conclusion(_expected_conclusion_fields())
    assert len(ec._raw) == 30
    assert ec._raw["BSTP_CLS_CODE"] == "0001"
    assert ec._raw["TICK_VRSS"] == "0"


def test_parse_program_trade_maps_headline_fields():
    pt = parse_program_trade(_program_trade_fields())
    assert isinstance(pt, IndexProgramTrade)
    assert pt.sector_code == "0001"
    assert pt.time == "153000"
    assert pt.total_sell_quantity == Decimal(1000)
    assert pt.total_buy_quantity == Decimal(1200)
    assert pt.whole_net_buy_volume == Decimal(200)
    assert pt.whole_net_buy_value == Decimal(5500000)
    assert pt.arbitrage_net_buy_volume == Decimal(80)
    assert pt.nonarbitrage_net_buy_volume == Decimal(120)
    assert pt.accumulated_volume == Decimal(900000)
    assert pt.accumulated_value == Decimal(45000000)


def test_program_trade_raw_has_all_ledger_keys():
    pt = parse_program_trade(_program_trade_fields())
    assert len(pt._raw) == 88
    assert pt._raw["BSTP_CLS_CODE"] == "0001"
    assert pt._raw["ACML_TR_PBMN"] == "45000000"


def test_decimal_helper_defaults_empty_to_zero():
    fields = _expected_conclusion_fields()
    fields[2] = ""  # expected_index empty -> Decimal(0)
    ec = parse_expected_conclusion(fields)
    assert ec.expected_index == Decimal(0)


def test_registry_has_index_specs():
    ec_spec = _registry.lookup("H0UPANC0")
    assert ec_spec is not None
    assert ec_spec.field_count == 30
    assert ec_spec.parser is parse_expected_conclusion

    pt_spec = _registry.lookup("H0UPPGM0")
    assert pt_spec is not None
    assert pt_spec.field_count == 88
    assert pt_spec.parser is parse_program_trade

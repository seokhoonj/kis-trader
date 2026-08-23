"""실시간 파서/엔티티 테스트 -- 국내주식 체결가(H0STCNT0) StockTradeTick.

원장 46필드 레이아웃대로 파싱되는지, 레지스트리 등록으로 연결 계층이 타입 엔티티를 내는지 검증.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from kis_trader.realtime import _registry
from kis_trader.realtime.messages import StockTradeTick
from kis_trader.realtime.parsers.domestic_stock import parse_trade_tick


def _sample_fields() -> list[str]:
    fields = ["0"] * 46
    fields[0] = "005930"       # symbol
    fields[1] = "093000"       # time
    fields[2] = "71500"        # current_price
    fields[3] = "2"            # change_sign (상승)
    fields[4] = "100"          # change
    fields[5] = "0.14"         # change_percent
    fields[7] = "71400"        # open
    fields[8] = "71800"        # high
    fields[9] = "70900"        # low
    fields[10] = "71500"       # best_ask
    fields[11] = "71400"       # best_bid
    fields[12] = "50"          # trade_volume
    fields[13] = "1000000"     # accumulated_volume
    fields[14] = "71500000000" # accumulated_value
    fields[18] = "120.5"       # conclusion_strength
    fields[21] = "1"           # trade_sign (매수)
    fields[33] = "20260814"    # business_date
    fields[35] = "N"           # trading_halted -> False
    fields[45] = "64350"       # static_vi_reference_price
    return fields


def test_parse_trade_tick_maps_headline_fields():
    tick = parse_trade_tick(_sample_fields())
    assert isinstance(tick, StockTradeTick)
    assert tick.symbol == "005930"
    assert tick.time == "093000"
    assert tick.current_price == Decimal(71500)
    assert tick.change_sign == "2"
    assert tick.change == Decimal(100)
    assert tick.change_percent == Decimal("0.14")
    assert tick.open == Decimal(71400)
    assert tick.high == Decimal(71800)
    assert tick.low == Decimal(70900)
    assert tick.best_ask == Decimal(71500)
    assert tick.best_bid == Decimal(71400)
    assert tick.trade_volume == Decimal(50)
    assert tick.accumulated_volume == Decimal(1000000)
    assert tick.accumulated_value == Decimal(71500000000)
    assert tick.conclusion_strength == Decimal("120.5")
    assert tick.trade_sign == "1"
    assert tick.business_date == "20260814"
    assert tick.trading_halted is False
    assert tick.static_vi_reference_price == Decimal(64350)


def test_trade_tick_raw_has_all_ledger_keys():
    tick = parse_trade_tick(_sample_fields())
    assert tick._raw["MKSC_SHRN_ISCD"] == "005930"
    assert tick._raw["VI_STND_PRC"] == "64350"
    assert len(tick._raw) == 46


def test_registry_has_trade_tick_variants():
    for tr_id in ("H0STCNT0", "H0NXCNT0", "H0UNCNT0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 46


def test_nxt_and_unified_raw_use_ledger_key_cntg_cls_code():
    # NXT/통합 시트는 index 21 을 CNTG_CLS_CODE 로 명명(KRX 는 CCLD_DVSN). _raw 가 각 원장 키로.
    fields = _sample_fields()
    fields[21] = "1"  # 체결구분(매수)
    krx = _registry.lookup("H0STCNT0").parser(fields)
    nxt = _registry.lookup("H0NXCNT0").parser(fields)
    assert "CCLD_DVSN" in krx._raw and "CNTG_CLS_CODE" not in krx._raw
    assert "CNTG_CLS_CODE" in nxt._raw and "CCLD_DVSN" not in nxt._raw
    # 값(체결구분)은 두 경우 모두 올바르게 읽힌다.
    assert krx.trade_sign == "1"
    assert nxt.trade_sign == "1"


def test_connection_dispatches_typed_trade_tick():
    # 연결 계층이 등록된 파서로 프레임을 StockTradeTick 으로 변환하는지(엔드투엔드).
    from kis_trader.realtime._connection import RealtimeConnection

    payload = "^".join(_sample_fields())

    class FakeWS:
        def __init__(self, frames):
            self._frames = list(frames)
            self.sent = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._frames:
                raise StopAsyncIteration
            return self._frames.pop(0)

        async def send(self, m):
            self.sent.append(m)

        async def pong(self, data):
            pass

        async def close(self):
            pass

    async def scenario():
        ws = FakeWS([f"0|H0STCNT0|001|{payload}"])

        async def connect(url):
            return ws

        conn = RealtimeConnection("KEY", "ws://x", connect=connect, reconnect=False)
        async with conn:
            return [m async for m in conn]

    msgs = asyncio.run(scenario())
    assert len(msgs) == 1
    assert isinstance(msgs[0].data, StockTradeTick)
    assert msgs[0].data.symbol == "005930"
    assert msgs[0].data.current_price == Decimal(71500)

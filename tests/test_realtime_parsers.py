"""실시간 파서/엔티티 테스트 -- 국내주식 체결가(H0STCNT0) StockTick.

원장 47필드 레이아웃대로 파싱되는지, 레지스트리 등록으로 연결 계층이 타입 엔티티를 내는지 검증.
2026-09-14 KRX 애프터마켓 도입으로 체결 레이아웃 끝에 MARKET_CLS_CODE 가 append 됐다(46->47).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from kis_trader.realtime import StockTick, _registry
from kis_trader.realtime.parsers.domestic_stock import parse_trade_tick


def _sample_fields() -> list[str]:
    fields = ["0"] * 47
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
    fields[18] = "120.5"       # execution_strength
    fields[21] = "1"           # trade_sign (매수)
    fields[33] = "20260814"    # business_date
    fields[35] = "N"           # trading_halted -> False
    fields[45] = "64350"       # static_vi_reference_price
    fields[46] = "2"           # MARKET_CLS_CODE (장 구분; 1프리 2정규 3애프터 5종가)
    return fields


def test_parse_trade_tick_maps_headline_fields():
    tick = parse_trade_tick(_sample_fields())
    assert isinstance(tick, StockTick)
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
    assert tick.execution_strength == Decimal("120.5")
    assert tick.trade_sign == "1"
    assert tick.business_date == "20260814"
    assert tick.trading_halted is False
    assert tick.static_vi_reference_price == Decimal(64350)


def test_parse_trade_tick_applies_downtick_sign():
    # SIGN=5(하락)면 양수 크기를 음수 change/change_percent 로 -- _apply_change_sign 회귀 포착.
    fields = _sample_fields()
    fields[3] = "5"
    tick = parse_trade_tick(fields)
    assert tick.change == Decimal(-100)
    assert tick.change_percent == Decimal("-0.14")


def test_trade_tick_raw_has_all_ledger_keys():
    tick = parse_trade_tick(_sample_fields())
    assert tick._raw["MKSC_SHRN_ISCD"] == "005930"
    assert tick._raw["VI_STND_PRC"] == "64350"
    assert tick._raw["MARKET_CLS_CODE"] == "2"
    assert len(tick._raw) == 47


def test_registry_has_trade_tick_variants():
    for tr_id in ("H0STCNT0", "H0NXCNT0", "H0UNCNT0"):
        spec = _registry.lookup(tr_id)
        assert spec is not None
        assert spec.field_count == 47


def test_protocol_accepts_47_field_trade_tick_after_aftermarket():
    # 2026-09-14 KRX 애프터마켓 도입으로 체결(H0STCNT0/NXT/통합)에 MARKET_CLS_CODE 가 append 되어
    # 레코드당 47필드가 온다(실서버 프로브로 확정). records() 가 이를 받아들여야 프레임이 드롭되지
    # 않는다 -- field_count 가 옛 46 이면 초과로 fail-closed 되어 구독 성공·틱0(silent) 회귀가 난다.
    from kis_trader.realtime._protocol import DataFrame

    for tr_id in ("H0STCNT0", "H0NXCNT0", "H0UNCNT0"):
        spec = _registry.lookup(tr_id)
        fields = _sample_fields()  # 47
        frame = DataFrame(False, tr_id, 2, "^".join(fields * 2))  # 멀티레코드도 정확히 분해되는지
        records = frame.records(spec.field_count)
        assert len(records) == 2
        tick = spec.parser(records[0])
        assert tick.symbol == "005930"
        assert tick._raw["MARKET_CLS_CODE"] == "2"


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
    # 연결 계층이 등록된 파서로 프레임을 StockTick 으로 변환하는지(엔드투엔드).
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
    assert isinstance(msgs[0].data, StockTick)
    assert msgs[0].data.symbol == "005930"
    assert msgs[0].data.current_price == Decimal(71500)
    assert isinstance(msgs[0].data.trade_volume, int) and isinstance(msgs[0].data.accumulated_volume, int)


def test_realtime_decimal_is_fail_closed_on_non_finite_and_garbage() -> None:
    # F30 회귀: 실시간 _decimal 은 _wire 와 같은 fail-closed 정책 -- 빈 값만 0, 비어있지 않은데
    # 파싱 불가하거나 유한하지 않으면(NaN/Infinity) 조작된 0 을 지어내지 않고 KISError 로 닫는다
    # (그러면 _connection._decode 가 그 프레임을 드롭한다). 안 그러면 NaN 이 frozen 엔티티로 흘러
    # price > x 비교가 조용히 깨진다.
    from kis_trader.errors import KISError
    from kis_trader.realtime.parsers._numeric import _decimal

    assert _decimal("") == Decimal(0)          # 빈 값은 스트림 연속성 위해 0
    assert _decimal("71500") == Decimal(71500)
    assert _decimal("-12.5") == Decimal("-12.5")
    for bad in ("NaN", "Infinity", "-Infinity", "abc", "1,000"):
        try:
            _decimal(bad)
        except KISError:
            continue
        raise AssertionError(f"_decimal({bad!r}) 는 KISError 로 닫혀야 한다")


def test_realtime_int_is_fail_closed_and_rejects_fractional() -> None:
    # F46: 시장데이터 수량(호가 잔량·거래량·종목 수 등)은 정수다. 실시간 _int 는 _decimal 과 같은
    # 빈값->0 스트림 연속성을 유지하되, 소수부가 있으면(진짜 정수가 아니면) 조작해 버리지 않고
    # KISError 로 닫아 그 프레임을 드롭한다 -- REST required_int 와 같은 fail-closed. 분수주식(소수점)은
    # 주문/보유 수량에만 생기고 거래소 집계 호가/거래량엔 없으므로, 소수부는 신뢰 못 할 프레임이다.
    from kis_trader.errors import KISError
    from kis_trader.realtime.parsers._numeric import _int

    assert _int("") == 0                       # 빈 값은 스트림 연속성 위해 0
    assert _int("1500") == 1500
    assert _int("-42") == -42                   # 순매수 수량 등 음수 가능
    assert _int("1234.0") == 1234               # KIS 가 정수를 "1234.0" 으로 줄 때도 정수화
    assert isinstance(_int("1500"), int)
    for bad in ("300.5", "NaN", "Infinity", "-Infinity", "abc", "1,000"):
        try:
            _int(bad)
        except KISError:
            continue
        raise AssertionError(f"_int({bad!r}) 는 KISError 로 닫혀야 한다")


def test_non_finite_field_drops_only_that_frame_end_to_end() -> None:
    # F30 end-to-end: 비유한 수치 필드를 담은 프레임은 연결 계층(_decode/_handle 의 except)이 드롭하고
    # (조작된 0/NaN 을 내보내지 않음), 뒤따르는 정상 프레임은 정상 전달된다 -- 스트림은 안 끊긴다.
    from kis_trader.realtime._connection import RealtimeConnection

    bad_fields = _sample_fields()
    bad_fields[2] = "NaN"                      # current_price(STCK_PRPR) -> _decimal 에서 KISError
    bad_payload = "^".join(bad_fields)
    good_payload = "^".join(_sample_fields())

    class FakeWS:
        def __init__(self, frames: list[str]) -> None:
            self._frames = list(frames)
            self.sent: list[str] = []

        def __aiter__(self) -> FakeWS:
            return self

        async def __anext__(self) -> str:
            if not self._frames:
                raise StopAsyncIteration
            return self._frames.pop(0)

        async def send(self, m: str) -> None:
            self.sent.append(m)

        async def pong(self, data: str) -> None:
            pass

        async def close(self) -> None:
            pass

    async def scenario() -> list[object]:
        ws = FakeWS([f"0|H0STCNT0|001|{bad_payload}", f"0|H0STCNT0|001|{good_payload}"])

        async def connect(url: str) -> FakeWS:
            return ws

        conn = RealtimeConnection("KEY", "ws://x", connect=connect, reconnect=False)
        async with conn:
            return [m async for m in conn]

    msgs = asyncio.run(scenario())
    assert len(msgs) == 1                                   # 불량 프레임은 드롭, 정상만 남음
    assert isinstance(msgs[0].data, StockTick)
    assert msgs[0].data.current_price == Decimal(71500)


def test_fractional_int_field_drops_only_that_frame_end_to_end() -> None:
    # F46 end-to-end: 정수여야 할 시장데이터 수량(CNTG_VOL)에 소수부가 담긴 프레임은 _int 가
    # KISError 로 닫고 연결 계층이 그 프레임만 드롭한다(조작된 절삭값을 내보내지 않음). 뒤따르는
    # 정상 프레임은 정상 전달된다 -- _decimal 비유한 드롭과 같은 경로이되 진입점이 _int 다.
    from kis_trader.realtime._connection import RealtimeConnection

    bad_fields = _sample_fields()
    bad_fields[12] = "300.5"                   # CNTG_VOL(체결 수량) 소수부 -> _int 에서 KISError
    bad_payload = "^".join(bad_fields)
    good_payload = "^".join(_sample_fields())

    class FakeWS:
        def __init__(self, frames: list[str]) -> None:
            self._frames = list(frames)
            self.sent: list[str] = []

        def __aiter__(self) -> FakeWS:
            return self

        async def __anext__(self) -> str:
            if not self._frames:
                raise StopAsyncIteration
            return self._frames.pop(0)

        async def send(self, m: str) -> None:
            self.sent.append(m)

        async def pong(self, data: str) -> None:
            pass

        async def close(self) -> None:
            pass

    async def scenario() -> list[object]:
        ws = FakeWS([f"0|H0STCNT0|001|{bad_payload}", f"0|H0STCNT0|001|{good_payload}"])

        async def connect(url: str) -> FakeWS:
            return ws

        conn = RealtimeConnection("KEY", "ws://x", connect=connect, reconnect=False)
        async with conn:
            return [m async for m in conn]

    msgs = asyncio.run(scenario())
    assert len(msgs) == 1                                   # 소수부 프레임만 드롭, 정상만 남음
    assert isinstance(msgs[0].data, StockTick)
    assert msgs[0].data.trade_volume == 50               # 정상 프레임의 CNTG_VOL(정수)
    assert isinstance(msgs[0].data.trade_volume, int)

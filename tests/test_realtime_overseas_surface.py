"""해외 실시간 타입드 구독 표면(rt.overseas) 검증 -- 잎이 올바른 (tr_id, tr_key) 로 구독하는지,
심볼->RSYM 해석·거래소 전달·모호/미주입 fail-closed·venue fail-closed·__all__ 노출.

파서/엔티티 자체는 test_realtime_overseas.py 가 검증한다 -- 여기선 표면(surface)만 본다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import kis_trader.realtime as rt
from kis_trader._internal._masters import InstrumentRecord
from kis_trader.errors import KISUsageError
from kis_trader.realtime.client import RealtimeClient
from kis_trader.realtime.overseas_namespace import RealtimeOverseasNamespace
from kis_trader.realtime.subscription import RealtimeSubscription


class _StubResolver:
    """KISClient.instrument 를 흉내내는 stub -- 호출 인자를 기록하고 RSYM 을 돌려준다."""

    def __init__(self, rsym: str = "DNASAAPL", ambiguous: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._rsym = rsym
        self._ambiguous = set(ambiguous)

    def __call__(self, symbol: str, *, exchange: str | None = None) -> InstrumentRecord:
        self.calls.append((symbol, exchange))
        if exchange is None and symbol in self._ambiguous:
            raise KISUsageError(f"'{symbol}' 는 여러 거래소에 있어 exchange 를 명시해야 한다.")
        return InstrumentRecord(
            symbol=symbol,
            exchange=exchange or "NAS",
            currency="USD",
            security_type="stock",
            korean_name="애플",
            english_name="APPLE INC",
            realtime_symbol=self._rsym,
        )


def _client(resolver: _StubResolver | None = None) -> RealtimeClient:
    return RealtimeClient(
        "approval", "wss://example/ws", connect=None, instrument_resolver=resolver
    )


def _ns(resolver: _StubResolver | None = None) -> tuple[RealtimeClient, RealtimeOverseasNamespace]:
    resolver = resolver if resolver is not None else _StubResolver()
    c = _client(resolver)
    return c, c.overseas


# (id, ns -> subscription, expected tr_id, expected tr_key) -- 모든 잎의 정확한 (TR, tr_key) 매핑.
# 해외주식 tr_key 는 resolver 가 돌려주는 RSYM(DNASAAPL), 선물/옵션은 시리즈코드, 통보는 hts_id.
_TR_ROWS: list[
    tuple[str, Callable[[RealtimeOverseasNamespace], RealtimeSubscription[object]], str, str]
] = [
    ("stock_trades", lambda ns: ns.stock("AAPL").trades(), "HDFSCNT0", "DNASAAPL"),
    ("stock_book_global", lambda ns: ns.stock("AAPL").order_book(venue="global"), "HDFSASP0", "DNASAAPL"),
    ("stock_book_asia", lambda ns: ns.stock("AAPL").order_book(venue="asia"), "HDFSASP1", "DNASAAPL"),
    ("stock_book_default", lambda ns: ns.stock("AAPL").order_book(), "HDFSASP0", "DNASAAPL"),
    ("futures_trades", lambda ns: ns.futures("ESZ25").trades(), "HDFFF020", "ESZ25"),
    ("futures_book", lambda ns: ns.futures("ESZ25").order_book(), "HDFFF010", "ESZ25"),
    ("option_trades", lambda ns: ns.option("ESZ25").trades(), "HDFFF020", "ESZ25"),
    ("option_book", lambda ns: ns.option("ESZ25").order_book(), "HDFFF010", "ESZ25"),
    ("notice_stock", lambda ns: ns.execution_notices.stock("myhtsid"), "H0GSCNI0", "myhtsid"),
    ("notice_deriv_orders", lambda ns: ns.execution_notices.derivative_orders("myhtsid"), "HDFFF1C0", "myhtsid"),
    ("notice_deriv_fills", lambda ns: ns.execution_notices.derivative_fills("myhtsid"), "HDFFF2C0", "myhtsid"),
]


@pytest.mark.parametrize(
    ("build", "expected_tr", "expected_key"),
    [pytest.param(build, tr, key, id=name) for name, build, tr, key in _TR_ROWS],
)
def test_every_mapping_subscribes_exact_tr_and_key(
    build: Callable[[RealtimeOverseasNamespace], RealtimeSubscription[object]],
    expected_tr: str,
    expected_key: str,
) -> None:
    c, ns = _ns()
    sub = build(ns)
    assert (sub.tr_id, sub.tr_key) == (expected_tr, expected_key)
    # _open_typed 이 라우팅 테이블에 실제로 등록했는지 확인.
    assert (sub.tr_id, sub.tr_key) in c._subscriptions


def test_stock_resolves_symbol_to_rsym() -> None:
    resolver = _StubResolver(rsym="DNASAAPL")
    _, ns = _ns(resolver)
    sub = ns.stock("AAPL").trades()
    assert sub.tr_key == "DNASAAPL"  # 심볼이 아니라 마스터 RSYM 이 tr_key
    assert resolver.calls == [("AAPL", None)]  # exchange 미지정 -> 자동 해석


def test_stock_exchange_passthrough() -> None:
    resolver = _StubResolver()
    _, ns = _ns(resolver)
    ns.stock("AAPL", exchange="NYS")
    assert resolver.calls == [("AAPL", "NYS")]  # 준 exchange 가 resolver 로 그대로 전달


def test_ambiguous_symbol_propagates_error() -> None:
    resolver = _StubResolver(ambiguous=("BRK",))
    _, ns = _ns(resolver)
    with pytest.raises(KISUsageError):
        ns.stock("BRK")  # exchange 미지정 + 모호 -> resolver 가 raise


def test_missing_resolver_fails_closed() -> None:
    c = _client(resolver=None)  # KISClient 없이 만든 클라이언트(주입된 resolver 없음)
    with pytest.raises(KISUsageError):
        c.overseas.stock("AAPL")


def test_unknown_venue_fails_closed() -> None:
    _, ns = _ns()
    with pytest.raises(KISUsageError):
        ns.stock("AAPL").order_book(venue="mars")  # type: ignore[arg-type]


def test_futures_and_option_share_trs() -> None:
    _, ns = _ns()
    f_trades = ns.futures("ESZ25").trades()
    o_trades = ns.option("ESZ25").trades()
    f_book = ns.futures("ESZ25").order_book()
    o_book = ns.option("ESZ25").order_book()
    assert f_trades.tr_id == o_trades.tr_id == "HDFFF020"
    assert f_book.tr_id == o_book.tr_id == "HDFFF010"


def test_futures_and_option_accept_series_code_keyword() -> None:
    # 공개 인자명은 series_code -- 키워드로 불러도 구독이 만들어진다(위치인자 회귀 방지).
    _, ns = _ns()
    assert ns.futures(series_code="ESZ25").trades().tr_id == "HDFFF020"
    assert ns.option(series_code="ESZ25").order_book().tr_id == "HDFFF010"


def test_client_exposes_overseas_namespace() -> None:
    c = _client(_StubResolver())
    assert isinstance(c.overseas, RealtimeOverseasNamespace)


def test_all_exports_new_overseas_names() -> None:
    for name in (
        "RealtimeOverseasNamespace",
        "DelayedTradeTick",
        "OverseasOrderBook",
        "AsiaDelayedOrderBook",
        "FuturesTradeTick",
        "FuturesOrderBook",
        "OverseasExecutionNotice",
        "FuturesOrderNotice",
        "FuturesExecutionNotice",
    ):
        assert name in rt.__all__, name
        assert hasattr(rt, name), name

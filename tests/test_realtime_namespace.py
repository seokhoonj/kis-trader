from __future__ import annotations

from collections.abc import Callable

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.realtime.client import RealtimeClient
from kis_trader.realtime.domestic_namespace import RealtimeDomesticNamespace
from kis_trader.realtime.subscription import RealtimeSubscription


def _ns() -> tuple[RealtimeClient, RealtimeDomesticNamespace]:
    c = RealtimeClient("approval", "wss://example/ws", connect=None)
    return c, RealtimeDomesticNamespace(c)


# (id, ns -> subscription, expected tr_id) -- 모든 잎의 정확한 TR 매핑을 한 표로 검증.
_TR_ROWS: list[tuple[str, Callable[[RealtimeDomesticNamespace], RealtimeSubscription[object]], str]] = [
    # 국내주식 체결 x 3 거래소
    ("stock_trades_krx", lambda ns: ns.stock("005930").trades(venue="KRX"), "H0STCNT0"),
    ("stock_trades_nxt", lambda ns: ns.stock("005930").trades(venue="NXT"), "H0NXCNT0"),
    ("stock_trades_unified", lambda ns: ns.stock("005930").trades(venue="unified"), "H0UNCNT0"),
    # 국내주식 호가 x 3 거래소
    ("stock_book_krx", lambda ns: ns.stock("005930").order_book(venue="KRX"), "H0STASP0"),
    ("stock_book_nxt", lambda ns: ns.stock("005930").order_book(venue="NXT"), "H0NXASP0"),
    ("stock_book_unified", lambda ns: ns.stock("005930").order_book(venue="unified"), "H0UNASP0"),
    # 국내주식 체결통보
    ("stock_notice", lambda ns: ns.execution_notices.stock("myhtsid"), "H0STCNI0"),
    # 선물 체결 x 4 kind
    ("futures_trades_index", lambda ns: ns.futures("101W09", kind="index").trades(), "H0IFCNT0"),
    ("futures_trades_commodity", lambda ns: ns.futures("101W09", kind="commodity").trades(), "H0CFCNT0"),
    ("futures_trades_stock", lambda ns: ns.futures("101W09", kind="stock").trades(), "H0ZFCNT0"),
    ("futures_trades_night", lambda ns: ns.futures("101W09", kind="night").trades(), "H0MFCNT0"),
    # 선물 호가 x 4 kind
    ("futures_book_index", lambda ns: ns.futures("101W09", kind="index").order_book(), "H0IFASP0"),
    ("futures_book_commodity", lambda ns: ns.futures("101W09", kind="commodity").order_book(), "H0CFASP0"),
    ("futures_book_stock", lambda ns: ns.futures("101W09", kind="stock").order_book(), "H0ZFASP0"),
    ("futures_book_night", lambda ns: ns.futures("101W09", kind="night").order_book(), "H0MFASP0"),
    # 옵션 체결 x 3 kind
    ("option_trades_index", lambda ns: ns.option("201W09", kind="index").trades(), "H0IOCNT0"),
    ("option_trades_stock", lambda ns: ns.option("201W09", kind="stock").trades(), "H0ZOCNT0"),
    ("option_trades_night", lambda ns: ns.option("201W09", kind="night").trades(), "H0EUCNT0"),
    # 옵션 호가 x 3 kind
    ("option_book_index", lambda ns: ns.option("201W09", kind="index").order_book(), "H0IOASP0"),
    ("option_book_stock", lambda ns: ns.option("201W09", kind="stock").order_book(), "H0ZOASP0"),
    ("option_book_night", lambda ns: ns.option("201W09", kind="night").order_book(), "H0EUASP0"),
    # 파생 체결통보 x 3 session
    ("deriv_notice_regular", lambda ns: ns.execution_notices.derivative("id", session="regular"), "H0IFCNI0"),
    ("deriv_notice_night_futures", lambda ns: ns.execution_notices.derivative("id", session="night_futures"), "H0MFCNI0"),
    ("deriv_notice_night_option", lambda ns: ns.execution_notices.derivative("id", session="night_option"), "H0EUCNI0"),
]


@pytest.mark.parametrize(
    ("build", "expected_tr"),
    [pytest.param(build, tr, id=name) for name, build, tr in _TR_ROWS],
)
def test_every_mapping_subscribes_exact_tr(
    build: Callable[[RealtimeDomesticNamespace], RealtimeSubscription[object]], expected_tr: str
) -> None:
    _, ns = _ns()
    sub = build(ns)
    assert sub.tr_id == expected_tr


def test_stock_trades_subscribes_correct_tr_and_key() -> None:
    c, ns = _ns()
    sub = ns.stock("005930").trades(venue="NXT")
    assert (sub.tr_id, sub.tr_key) == ("H0NXCNT0", "005930")
    assert (sub.tr_id, sub.tr_key) in c._subscriptions


def test_futures_trades_index_tr() -> None:
    _, ns = _ns()
    sub = ns.futures("101W09").trades()  # kind 기본 index
    assert sub.tr_id == "H0IFCNT0"


def test_futures_night_order_book_tr() -> None:
    _, ns = _ns()
    sub = ns.futures("101W09", kind="night").order_book()
    assert sub.tr_id == "H0MFASP0"


def test_option_stock_trades_tr() -> None:
    _, ns = _ns()
    sub = ns.option("201W09", kind="stock").trades()
    assert sub.tr_id == "H0ZOCNT0"


def test_execution_notices_stock_and_derivative() -> None:
    _, ns = _ns()
    s = ns.execution_notices.stock("myhtsid")
    d = ns.execution_notices.derivative("myhtsid", session="night_option")
    assert s.tr_id == "H0STCNI0"
    assert d.tr_id == "H0EUCNI0"


def test_stock_execution_notice_uses_demo_tr_on_paper() -> None:
    # 체결통보 TR-id 는 모의(paper)에서 H0STCNI9 다 -- 실전 TR 로 구독하면 모의에서 통보를 못 받는다.
    c = RealtimeClient("approval", "wss://example/ws", connect=None, environment="paper")
    ns = RealtimeDomesticNamespace(c)
    assert ns.execution_notices.stock("myhtsid").tr_id == "H0STCNI9"


def test_bad_kind_fails_closed() -> None:
    _, ns = _ns()
    with pytest.raises(KISUsageError):
        ns.futures("101W09", kind="bogus")  # type: ignore[arg-type]


def test_bad_venue_fails_closed() -> None:
    _, ns = _ns()
    with pytest.raises(KISUsageError):
        ns.stock("005930").trades(venue="XXX")  # type: ignore[arg-type]


def test_bad_session_fails_closed() -> None:
    _, ns = _ns()
    with pytest.raises(KISUsageError):
        ns.execution_notices.derivative("id", session="bogus")  # type: ignore[arg-type]

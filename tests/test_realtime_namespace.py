from __future__ import annotations

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.realtime.client import RealtimeClient
from kis_trader.realtime.namespace import RealtimeDomesticNamespace


def _ns() -> tuple[RealtimeClient, RealtimeDomesticNamespace]:
    c = RealtimeClient("approval", "wss://example/ws", connect=None)
    return c, RealtimeDomesticNamespace(c)


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

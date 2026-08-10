"""자산군 최상위 네임스페이스 -- kis.domestic / kis.overseas / kis.pension / kis.orders.

각 네임스페이스 메서드가 올바른 원장 엔진(같은 TR/파라미터/경로)을 때리는지 검증한다. 네트워크
없이 FakeTransport 로 wire 콜만 본다.
"""

from __future__ import annotations

import contextlib
import threading

from kis_openapi import InstrumentRecord, KISClient, MasterIndex
from kis_openapi.calendar import CalendarQueries
from kis_openapi.market import MarketQueries
from kis_openapi.namespaces import (
    DomesticAccount,
    DomesticNamespace,
    OrdersNamespace,
    OverseasAccount,
    OverseasNamespace,
    PensionNamespace,
)
from kis_openapi.ranking import RankingQueries
from kis_openapi.ticker import Ticker
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        assert self.response is not None
        return self.response


def _client(transport=None, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport or FakeTransport())


# --- 네임스페이스가 세션에 붙어 있고 타입이 맞나 ---------------------------
def test_namespaces_present_and_typed():
    k = _client()
    assert isinstance(k.domestic, DomesticNamespace)
    assert isinstance(k.overseas, OverseasNamespace)
    assert isinstance(k.pension, PensionNamespace)
    assert isinstance(k.orders, OrdersNamespace)
    assert isinstance(k.domestic.account, DomesticAccount)
    assert isinstance(k.overseas.account, OverseasAccount)


# --- 핸들 팩토리가 기존과 같은 핸들을 주나 ---------------------------------
def test_domestic_stock_returns_ticker():
    k = _client()
    t = k.domestic.stock("005930")
    assert isinstance(t, Ticker)
    assert t.symbol == "005930"
    assert not t.is_overseas


def test_overseas_stock_returns_ticker():
    k = _client()
    t = k.overseas.stock("AAPL", exchange="NAS")
    assert isinstance(t, Ticker)
    assert t.is_overseas


def test_overseas_stock_auto_resolves_exchange():
    # market 자동: exchange 를 안 줘도 종목 마스터로 거래소가 채워진다.
    index = MasterIndex([InstrumentRecord("AAPL", "NAS", "USD", "stock", "애플", "APPLE", "NASAAPL")])
    k = KISClient(app_key="k", app_secret="s", account="12345678-01",
                  transport=FakeTransport(), master_index=index)
    t = k.overseas.stock("AAPL")            # exchange 생략
    assert t.is_overseas
    assert t.exchange == "NAS"


def test_domestic_query_namespaces_expose_query_objects():
    k = _client()
    assert isinstance(k.domestic.ranking, RankingQueries)
    assert isinstance(k.domestic.market, MarketQueries)
    assert isinstance(k.domestic.calendar, CalendarQueries)


# --- 계좌 네임스페이스가 올바른 TR/경로를 때리나 --------------------------
def _last_call(fn, fake):
    """fn() 을 호출하고 마지막 wire 콜을 돌려준다. 응답 파싱 실패는 무시(위임=와이어 콜만 검증)."""
    with contextlib.suppress(Exception):
        fn()
    return fake.calls[-1]


def test_domestic_account_balance_hits_balance_tr():
    # 네임스페이스 경로가 국내 잔고 조회 TR/경로를 때리는지.
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="M", msg1="", body={}, tr_cont=""))
    k = _client(fake)
    call = _last_call(k.domestic.account.balance, fake)
    assert call["tr_id"] == "TTTC8434R"
    assert call["path"] == "/uapi/domestic-stock/v1/trading/inquire-balance"


def test_overseas_account_present_balance_delegates():
    body = {"output1": [], "output2": [], "output3": {}}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    fake = FakeTransport(response=resp)
    k = _client(fake)
    k.overseas.account.present_balance()
    assert fake.calls[-1]["tr_id"] == "CTRP6504R"
    assert fake.calls[-1]["path"] == "/uapi/overseas-stock/v1/trading/inquire-present-balance"


def test_overseas_algo_orders_delegates():
    body = {"output": [], "ctx_area_nk200": "", "ctx_area_fk200": ""}
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    fake = FakeTransport(response=resp)
    k = _client(fake)
    k.overseas.account.algo_orders()
    assert fake.calls[-1]["tr_id"] == "TTTS6058R"


def test_pension_balance_delegates():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="M", msg1="", body={}, tr_cont=""))
    k = _client(fake)
    assert _last_call(k.pension.balance, fake)["tr_id"] == "TTTC2208R"

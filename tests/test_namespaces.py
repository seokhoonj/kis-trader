"""자산군 최상위 네임스페이스 -- kis.domestic / kis.overseas / kis.pension / kis.orders.

각 네임스페이스 메서드가 올바른 원장 엔진(같은 TR/파라미터/경로)을 때리는지 검증한다. 네트워크
없이 FakeTransport 로 wire 콜만 본다.
"""

from __future__ import annotations

import contextlib
import threading

import pytest

from kis_trader import InstrumentRecord, KISClient, MasterIndex
from kis_trader._stock_base import _StockBase
from kis_trader.domestic.calendar import CalendarQueries
from kis_trader.domestic.market import MarketQueries
from kis_trader.domestic.namespace import DomesticAccount, DomesticNamespace
from kis_trader.domestic.ranking import RankingQueries
from kis_trader.domestic.stock import DomesticStock
from kis_trader.namespaces import OrdersNamespace
from kis_trader.overseas.namespace import OverseasAccount, OverseasNamespace
from kis_trader.pension.namespace import PensionNamespace
from kis_trader.overseas.stock import OverseasStock
from kis_trader.transport import RawResponse


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


# --- 핸들 팩토리가 올바른 자산군 핸들을 주나 -------------------------------
def test_domestic_stock_returns_domestic_stock():
    k = _client()
    t = k.domestic.stock("005930")
    assert isinstance(t, DomesticStock)
    assert t.symbol == "005930"
    assert not t.is_overseas


def test_overseas_stock_returns_overseas_stock():
    k = _client()
    t = k.overseas.stock("AAPL", exchange="NAS")
    assert isinstance(t, OverseasStock)
    assert t.is_overseas


def _public_methods(cls: type) -> set[str]:
    """클래스의 공개 호출가능 멤버 이름(언더스코어 제외)."""
    return {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}


def test_stock_surfaces_are_asset_specific():
    # 자산군 분리의 핵심 계약: 국내 전용은 해외 핸들에 없고, 해외 전용은 국내 핸들에 없다(전수).
    dom = _public_methods(DomesticStock)
    ovs = _public_methods(OverseasStock)
    assert ovs - dom == {"current_price", "overnight_buy", "overnight_sell"}   # 해외 전용은 정확히 이 셋
    assert {"nav", "balance_sheet", "investor_flows", "credit_buy", "buyable"} <= dom - ovs
    assert {"quote", "bars", "order_book", "trades"} <= dom & ovs          # 공유 표면은 양쪽에
    # 추상 베이스는 직접 생성 불가.
    with pytest.raises(TypeError):
        _StockBase(_client(), "005930")


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

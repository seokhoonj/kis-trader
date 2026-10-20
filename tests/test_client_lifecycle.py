"""KISClient 수명 관리 회귀 -- close()/__enter__/__exit__ 가 주입 저장소의 단일-writer flock 을 푼다.

``with KISClient(..., store=OrderStore(path=...))`` 블록을 벗어나면 저장소 flock 이 풀려 같은 경로를
다시 열 수 있어야 한다(안 풀리면 재시작/재오픈이 KISError 로 막힌다). close() 중복 호출은 안전해야 한다.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.order import Order
from kis_trader.store import OrderStore


class _StubTransport:
    def request(self, **_kw):  # 네트워크 호출 없음 -- 수명만 검증
        raise AssertionError("lifecycle test must not hit the wire")


def _client(store):
    return KISClient(app_key="k", app_secret="s", environment="real", account="12345678-01",
                     transport=_StubTransport(), store=store)


def test_with_block_releases_store_lock_so_path_can_reopen(tmp_path):
    path = tmp_path / "orders.json"
    with _client(OrderStore(path=path)):
        pass  # 블록을 벗어나면 __exit__ -> close() -> flock 해제
    # 같은 경로를 다시 열 수 있어야 한다(이전 세션이 락을 쥔 채면 KISError).
    reopened = OrderStore(path=path)
    reopened.close()


def test_double_close_does_not_raise(tmp_path):
    store = OrderStore(path=tmp_path / "orders.json")
    kis = _client(store)
    kis.close()
    kis.close()  # 두 번째 close 는 조용해야 한다


def test_open_while_locked_raises(tmp_path):
    # 전제 확인: 같은 경로를 두 번 열면(첫 세션이 락 보유) 단일-writer flock 이 거부한다.
    path = tmp_path / "orders.json"
    with _client(OrderStore(path=path)), pytest.raises(KISError):
        OrderStore(path=path)


@pytest.mark.parametrize("field", ["cano", "product_code", "environment"])
def test_client_scope_constructor_refuses_mismatch_without_closing_external_store(tmp_path, field):
    path = tmp_path / "scope.json"
    store = OrderStore(path)
    first = _client(store)
    cano, product = first._require_account()
    target = {"cano": cano, "product_code": product, "environment": "real"}
    target[field] = {"cano": "other", "product_code": "02", "environment": "paper"}[field]
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(KISUsageError):
        KISClient(app_key="rotated", app_secret="rotated", environment=target["environment"],
                  account=target["cano"] + "-" + target["product_code"], transport=_StubTransport(), store=store)
    assert (path.read_bytes() if path.exists() else None) == before
    store.bind_scope(cano=cano, product_code=product, environment="real")
    store.try_claim("usable", Order.market("005930", side="buy", quantity=1).fingerprint)
    store.close()


def test_client_unknown_journal_requires_explicit_attestation(tmp_path):
    path = tmp_path / "unknown.json"
    with OrderStore(path) as seed:
        seed.try_claim("pending", Order.market("005930", side="buy", quantity=1).fingerprint)
    before = path.read_bytes()
    with OrderStore(path) as store:
        with pytest.raises(KISUsageError):
            _client(store)
        assert path.read_bytes() == before
        assert store.is_in_flight("pending")
        reference = _client(OrderStore())
        cano, product = reference._require_account()
        store.bind_scope(cano=cano, product_code=product, environment="real", adopt_legacy=True)
        kis = _client(store)
        with pytest.raises(KISUsageError):
            kis.domestic.stock("005930").buy(quantity=1, client_order_id="pending")


@pytest.mark.parametrize("entry", ["place", "change", "reconcile", "reserved", "overseas_reserved"])
def test_independent_engine_scope_precedes_any_store_lookup(monkeypatch, entry):
    from kis_trader._order_engine import OrderEngine
    store = OrderStore()
    store.bind_scope(cano="owner", product_code="01", environment="real")
    def lookup(*args, **kwargs):
        pytest.fail("scope mismatch must precede any store lookup")
    for name in ("classify", "binding_for", "fingerprint_for", "report_for", "try_claim"):
        monkeypatch.setattr(store, name, lookup)
    engine = OrderEngine(transport=_StubTransport(), store=store, environment="real", orderable=True,
                         risk=None, require_account=lambda: ("other", "01"))
    with pytest.raises(KISUsageError):
        if entry == "place":
            engine.place_order(Order.market("005930", side="buy", quantity=1))
        elif entry == "change":
            engine.change_order("existing", action="cancel", quantity=None, limit_price=None, request_id=None)
        elif entry == "reconcile":
            engine.reconcile("existing")
        elif entry == "reserved":
            engine.place_reserved_order(symbol="005930", side="buy", quantity=1, limit_price=70000,
                                        end_date=None, client_order_id="reserved")
        else:
            engine.place_overseas_reserved_order(symbol="AAPL", side="buy", quantity=1, limit_price=150,
                exchange="NAS", currency=None, algo_strategy=None, client_order_id="reserved")


def test_same_scope_sharing_preserves_pending_and_normalized_account():
    store = OrderStore()
    first = _client(store)
    second = KISClient(app_key="rotated", app_secret="rotated", environment="real",
        account=" " + first._account + " ", profile="alias", transport=_StubTransport(), store=store)
    order = Order.market("005930", side="buy", quantity=1, client_order_id="pending")
    store.try_claim("pending", order.fingerprint)
    for client in (first, second):
        with pytest.raises(KISUsageError):
            client.domestic.stock("005930").buy(quantity=1, client_order_id="pending")
    assert store.is_in_flight("pending")

    from datetime import datetime
    from decimal import Decimal

    from kis_trader.report import ExecutionReport, OrderStatus
    report = ExecutionReport(client_order_id="completed", order_id="broker-number", symbol="005930", side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.now(UTC))
    store.record(report, order.fingerprint)
    for client in (first, second):
        assert client.domestic.stock("005930").buy(quantity=1, client_order_id="completed") is report


@pytest.mark.parametrize("field", ["cano", "product_code", "environment"])
@pytest.mark.parametrize("operation", ["replay", "modify", "cancel", "domestic_cancel", "domestic_modify", "overseas_cancel"])
def test_public_scope_guards_precede_lookup_and_direct_reserved_wire(monkeypatch, field, operation):
    client = _client(OrderStore())
    account_view = client.account
    cano, product = client._require_account()
    target = {"cano": cano, "product_code": product, "environment": "real"}
    target[field] = {"cano": "other", "product_code": "02", "environment": "paper"}[field]
    wrong = OrderStore()
    wrong.bind_scope(**target)
    client._store = wrong
    client._order_engine._store = wrong
    def lookup(*args, **kwargs):
        pytest.fail("scope must precede lookup")
    for name in ("classify", "binding_for", "fingerprint_for", "report_for", "try_claim"):
        monkeypatch.setattr(wrong, name, lookup)
    with pytest.raises(KISUsageError):
        if operation == "replay":
            client.domestic.stock("005930").buy(quantity=1, client_order_id="completed")
        elif operation == "modify":
            client.orders.modify("completed", quantity=1, limit_price=None)
        elif operation == "cancel":
            client.orders.cancel("completed")
        elif operation == "domestic_cancel":
            account_view.domestic.cancel_reserved_order("reservation")
        elif operation == "domestic_modify":
            account_view.domestic.modify_reserved_order("reservation", symbol="005930", side="buy", quantity=1)
        else:
            account_view.overseas.cancel_reserved_order("reservation", receipt_date="20261021")


def test_constructor_scope_check_precedes_default_transport_creation(monkeypatch):
    import kis_trader._internal._http as http
    store = OrderStore()
    store.bind_scope(cano="owner", product_code="01", environment="real")
    monkeypatch.setattr(http, "RequestsTransport", lambda **kwargs: pytest.fail("transport allocation"))
    with pytest.raises(KISUsageError):
        KISClient(app_key="k", app_secret="s", account="other-01", environment="real", store=store)
    store.bind_scope(cano="owner", product_code="01", environment="real")


def test_no_account_client_does_not_invent_scope(monkeypatch):
    store = OrderStore()
    store.try_claim("unknown", Order.market("005930", side="buy", quantity=1).fingerprint)
    def bind(**kwargs):
        pytest.fail("account-free client must not bind")
    monkeypatch.setattr(store, "bind_scope", bind)
    client = KISClient(app_key="k", app_secret="s", environment="paper", transport=_StubTransport(), store=store)
    assert client.domestic.stock("005930").symbol == "005930"
    with pytest.raises(KISUsageError):
        client.domestic.stock("005930").buy(quantity=1)

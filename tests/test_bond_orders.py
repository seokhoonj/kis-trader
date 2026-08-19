"""국내 장내채권 매수 발주 -- kis.domestic.bond(code).buy(...) (buy TTTC0952U, 실전 전용).

현금주문과 같은 안전 코어(place)를 공유하되 와이어 조립기만 채권용이다. 이중체결 방지·재시도
금지·dedup 지문(exchange 슬롯의 "BOND" 값으로 구분)을 네트워크 없이 가짜 전송으로 검증한다.
채권은 실전 전용(모의 미지원)이라 라이브 검증은 별도이며, 여기선 모의 전송으로만 확인한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader.domestic._engine import bond_orders as bond
from kis_trader.errors import KISUsageError, OrderError
from kis_trader.report import ExecutionReport
from kis_trader.transport import RawResponse

_BUY = "/uapi/domestic-bond/v1/trading/buy"
_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0001234567", "ORD_TMD": "101530"}},
)


class FakeTransport:
    """모든 호출을 기록하고 고정 응답을 주는 가짜 전송."""

    def __init__(self, response=_ACCEPTED):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        return self.response

    @property
    def request_count(self) -> int:
        return len(self.calls)


def _client(transport, *, environment="real", store=None, risk=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment=environment, transport=transport, store=store, risk=risk)


def _bond_order(**kw):
    base = {
        "symbol": "KR2033022D33", "side": "buy", "order_type": "limit", "quantity": Decimal(10),
        "limit_price": Decimal(10000), "exchange": "BOND", "client_order_id": "c",
    }
    base.update(kw)
    return Order(**base)


# --- exchange marker ------------------------------------------------------
def test_is_bond_exchange():
    assert bond.is_bond_exchange("BOND") is True
    assert bond.is_bond_exchange("XKRX") is False
    assert bond.is_bond_exchange("XKFE") is False


# --- 와이어 골든바디 ------------------------------------------------------
def test_make_order_request_buy_body():
    req = bond.make_order_request(_bond_order(), cano="81012345", product_code="03",
                                  environment="real")
    assert req.method == "POST"
    assert req.path == bond._PLACE_PATH
    assert req.tr_id == "TTTC0952U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "03", "PDNO": "KR2033022D33",
        "ORD_QTY2": "10", "BOND_ORD_UNPR": "10000",
        "SAMT_MKET_PTCI_YN": "N", "BOND_RTL_MKET_YN": "N",
        "IDCR_STFNO": "", "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0", "CTAC_TLNO": "",
    }


def test_make_order_request_rejects_non_bond():
    order = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
                  limit_price=Decimal(70000), exchange="XKRX", client_order_id="c")
    with pytest.raises(OrderError):
        bond.make_order_request(order, cano="8", product_code="03", environment="real")


def test_make_order_request_paper_rejected():
    with pytest.raises(KISUsageError):
        bond.make_order_request(_bond_order(), cano="8", product_code="03", environment="paper")


def test_make_order_request_sell_not_supported():
    order = _bond_order(side="sell")
    with pytest.raises(KISUsageError, match="매도"):
        bond.make_order_request(order, cano="8", product_code="03", environment="real")


def test_make_order_request_requires_limit_price():
    # 채권은 지정가(채권단가) 전용 -- 시장가(가격 없음)로 만든 주문은 빌더가 fail-closed.
    order = _bond_order(order_type="market", limit_price=None)
    with pytest.raises(KISUsageError, match="지정가"):
        bond.make_order_request(order, cano="8", product_code="03", environment="real")


@pytest.mark.parametrize(
    "kw, match",
    [
        # 지정가 아님(스탑리밋) -- 직접 만든 주문이 평범한 지정가로 조용히 나가지 않게 거부.
        ({"order_type": "stop_limit", "stop_price": Decimal(9000)}, "지정가만"),
        # day 아님(ioc) -- 채권 엔드포인트에 TIF 필드가 없어 지문/와이어 불일치가 된다.
        ({"time_in_force": "ioc"}, "day"),
    ],
)
def test_make_order_request_rejects_non_representable_shape(kw, match):
    order = _bond_order(**kw)
    with pytest.raises(KISUsageError, match=match):
        bond.make_order_request(order, cano="8", product_code="03", environment="real")


# --- end-to-end: handle.buy -> _place_order -> 전송 -> ExecutionReport -----
def test_bond_buy_wire():
    fake = FakeTransport()
    report = _client(fake).domestic.bond("KR2033022D33").buy(quantity=10, limit_price=10000)
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0001234567"
    assert fake.request_count == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _BUY
    assert call["tr_id"] == "TTTC0952U"
    assert call["body"]["PDNO"] == "KR2033022D33"
    assert call["body"]["ORD_QTY2"] == "10"
    assert call["body"]["BOND_ORD_UNPR"] == "10000"
    assert call["body"]["SAMT_MKET_PTCI_YN"] == "N"
    assert call["body"]["BOND_RTL_MKET_YN"] == "N"
    assert call["body"]["ORD_SVR_DVSN_CD"] == "0"
    assert call["body"]["CTAC_TLNO"] == ""


def test_bond_buy_routes_to_bond_builder_not_stock_tr():
    fake = FakeTransport()
    _client(fake).domestic.bond("KR2033022D33").buy(quantity=1, limit_price=9800)
    # 주식 현금주문 TR(TTTC0012U)이 아니라 채권 매수 TR 로 나가야 한다.
    assert fake.calls[0]["tr_id"] == "TTTC0952U"
    assert fake.calls[0]["path"] == _BUY


def test_bond_buy_dedup_idempotent():
    store = OrderStore()
    fake = FakeTransport()
    cid = "20260819-bondbuy00000001"
    handle = _client(fake, store=store).domestic.bond("KR2033022D33")
    r1 = handle.buy(quantity=10, limit_price=10000, client_order_id=cid)
    r2 = handle.buy(quantity=10, limit_price=10000, client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert fake.request_count == 1              # 공유 안전 코어가 재전송을 막는다


def test_bond_buy_paper_fails_closed():
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.bond("KR2033022D33").buy(
            quantity=10, limit_price=10000)
    assert fake.request_count == 0              # 모의 세션은 와이어 미접촉


def test_bond_buy_rejects_risk_gate():
    from kis_trader.risk import RiskLimits
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(KISUsageError):
        client.domestic.bond("KR2033022D33").buy(quantity=10, limit_price=10000)
    assert fake.request_count == 0


# --- reconcile fail-closed (미확인 채권 주문은 국내주식 일별체결조회로 오조회하지 않는다) ---
def test_bond_reconcile_fails_closed_not_silent_none():
    # in-flight 채권 지문의 재조회는 엉뚱한 테이블을 훑어 None 을 돌려주는 대신, 미지원임을
    # 명시하며 fail-closed 해야 한다(파생·해외 fail-closed 분기와 대칭).
    store = OrderStore()
    order = _bond_order(client_order_id="bond-inflight")
    store.try_claim("bond-inflight", order.fingerprint)
    client = _client(FakeTransport(), store=store)
    with pytest.raises(KISUsageError, match="재조회"):
        client.orders.reconcile("bond-inflight")

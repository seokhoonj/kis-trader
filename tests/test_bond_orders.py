"""국내 장내채권 매수 발주 -- kis.domestic.bond(code).buy(...) (buy TTTC0952U, 실전 전용).

현금주문과 같은 안전 코어(place)를 공유하되 와이어 조립기만 채권용이다. 이중체결 방지·재시도
금지·dedup 지문(exchange 슬롯의 "BOND" 값으로 구분)을 네트워크 없이 가짜 전송으로 검증한다.
채권은 실전 전용(모의 미지원)이라 라이브 검증은 별도이며, 여기선 모의 전송으로만 확인한다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader._internal._datetime import _KST
from kis_trader.domestic._engine import bond_orders as bond
from kis_trader.errors import KISUsageError, OrderError
from kis_trader.order import ImmediateOrderFingerprint
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.transport import RawResponse

_BUY = "/uapi/domestic-bond/v1/trading/buy"
_SELL = "/uapi/domestic-bond/v1/trading/sell"
_CHANGE = "/uapi/domestic-bond/v1/trading/order-rvsecncl"
_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0001234567", "ORD_TMD": "101530"}},
)
_CHANGE_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="정정 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0009999999"}},
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


def test_make_order_request_sell_body():
    # 매도는 sell 엔드포인트/TR 로 라우팅하고 매수 lot(BUY_DT/BUY_SEQ)을 지문 재사용 슬롯에서 읽는다.
    order = _bond_order(side="sell", bond_buy_date="20240215", bond_buy_seq="1")
    req = bond.make_order_request(order, cano="81012345", product_code="03", environment="real")
    assert req.method == "POST"
    assert req.path == bond._SELL_PATH
    assert req.tr_id == "TTTC0958U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "03", "ORD_DVSN": "01", "PDNO": "KR2033022D33",
        "ORD_QTY2": "10", "BOND_ORD_UNPR": "10000", "SPRX_YN": "N",
        "BUY_DT": "20240215", "BUY_SEQ": "1",
        "SAMT_MKET_PTCI_YN": "N", "SLL_AGCO_OPPS_SLL_YN": "N", "BOND_RTL_MKET_YN": "N",
        "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0", "CTAC_TLNO": "",
    }


def test_make_order_request_sell_requires_lot():
    # 매수 lot 지목(BUY_DT/BUY_SEQ)이 없는 매도는 fail-closed -- 엉뚱한 lot 을 팔지 않는다.
    order = _bond_order(side="sell")
    with pytest.raises(KISUsageError, match="lot"):
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


# --- end-to-end: handle.sell (매수 lot 지목, 실전 전용) ---------------------
def test_bond_sell_wire():
    fake = FakeTransport()
    report = _client(fake).domestic.bond("KR2033022D33").sell(
        quantity=10, limit_price=10000, buy_date="20240215", buy_seq="1")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0001234567"
    assert fake.request_count == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _SELL
    assert call["tr_id"] == "TTTC0958U"
    assert call["body"]["ORD_DVSN"] == "01"
    assert call["body"]["PDNO"] == "KR2033022D33"
    assert call["body"]["ORD_QTY2"] == "10"
    assert call["body"]["BOND_ORD_UNPR"] == "10000"
    assert call["body"]["BUY_DT"] == "20240215"
    assert call["body"]["BUY_SEQ"] == "1"
    assert call["body"]["SPRX_YN"] == "N"
    assert call["body"]["ORD_SVR_DVSN_CD"] == "0"


def test_bond_sell_lot_in_fingerprint():
    # 같은 종목/수량/가격이라도 서로 다른 lot 의 매도는 서로 다른 주문이라 지문이 달라야 하고,
    # dedup 장벽이 둘째 매도를 오차단하지 않고 둘 다 나가야 한다.
    store = OrderStore()
    fake = FakeTransport()
    handle = _client(fake, store=store).domestic.bond("KR2033022D33")
    r1 = handle.sell(quantity=10, limit_price=10000, buy_date="20240215", buy_seq="1")
    r2 = handle.sell(quantity=10, limit_price=10000, buy_date="20240216", buy_seq="1")
    r3 = handle.sell(quantity=10, limit_price=10000, buy_date="20240215", buy_seq="2")
    assert fake.request_count == 3              # 세 lot 모두 서로 다른 주문 -> 전부 전송
    assert r1.order_id == r2.order_id == r3.order_id  # 같은 고정 응답(구분은 지문에서)


def test_bond_sell_same_lot_dedups():
    # 같은 lot 의 같은 id 재발주는 공유 안전 코어가 재전송을 막는다(1회만 나감).
    store = OrderStore()
    fake = FakeTransport()
    cid = "20260819-bondsell0000001"
    handle = _client(fake, store=store).domestic.bond("KR2033022D33")
    r1 = handle.sell(quantity=10, limit_price=10000, buy_date="20240215", buy_seq="1",
                     client_order_id=cid)
    r2 = handle.sell(quantity=10, limit_price=10000, buy_date="20240215", buy_seq="1",
                     client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert fake.request_count == 1


def test_bond_sell_requires_lot():
    # 빈 buy_date/buy_seq 로는 매도할 수 없다 -- 와이어에 닿기 전에 fail-closed.
    fake = FakeTransport()
    handle = _client(fake).domestic.bond("KR2033022D33")
    with pytest.raises(KISUsageError):
        handle.sell(quantity=10, limit_price=10000, buy_date="", buy_seq="1")
    with pytest.raises(KISUsageError):
        handle.sell(quantity=10, limit_price=10000, buy_date="20240215", buy_seq="")
    assert fake.request_count == 0


def test_bond_sell_paper_fails_closed():
    # 채권 매도도 실전 전용 -- 모의 세션은 claim 전에 거부하고 와이어에 닿지 않는다.
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.bond("KR2033022D33").sell(
            quantity=10, limit_price=10000, buy_date="20240215", buy_seq="1")
    assert fake.request_count == 0


def test_bond_sell_lot_slots_are_bond_only():
    # 매수 lot 슬롯(bond_buy_date/seq)은 채권 주문 전용 -- 다른 거래소에 실리면 생성 시점에 거부.
    with pytest.raises(KISUsageError):
        Order(symbol="005930", side="sell", order_type="limit", quantity=Decimal(1),
              limit_price=Decimal(70000), exchange="XKRX", bond_buy_date="20240215",
              bond_buy_seq="1", client_order_id="c")


def test_bond_lot_slots_rejected_on_bond_buy():
    # lot 지목은 채권 **매도** 전용 -- 채권 매수에 lot 을 실으면 생성 시점에 거부.
    with pytest.raises(KISUsageError, match="매도"):
        _bond_order(side="buy", bond_buy_date="20240215", bond_buy_seq="1")


@pytest.mark.parametrize(
    "kw",
    [
        {"bond_buy_date": "20240215", "bond_buy_seq": ""},   # 순번 없음
        {"bond_buy_date": "", "bond_buy_seq": "1"},          # 매수일 없음
    ],
)
def test_bond_sell_requires_both_lot_fields(kw):
    # 채권 매도는 매수 lot 의 두 필드(BUY_DT/BUY_SEQ)가 모두 있어야 -- 하나만 주면 생성 시점 fail-closed.
    with pytest.raises(KISUsageError):
        _bond_order(side="sell", **kw)


def test_bond_forbids_stray_derivative_item():
    # BOND 지문은 derivative_item 슬롯을 BUY_SEQ 로 재사용한다 -- 독립 derivative_item 이 실리면
    # 서로 다른 lot 의 매도가 한 지문으로 붕괴하는 collision 이 나므로 생성 시점에 거부한다.
    with pytest.raises(KISUsageError, match="derivative_item"):
        _bond_order(side="sell", bond_buy_date="20240215", bond_buy_seq="1",
                    derivative_item="02")


def test_fingerprint_byte_compat_unchanged_for_existing_orders():
    # 채권 매수·주식·파생 주문은 lot 슬롯을 "" 로 두므로 온-디스크 16-슬롯 인코딩이 종전과 바이트
    # 동일해야 한다(loan_date 슬롯 idx9, derivative_item 슬롯 idx13 이 "").
    from kis_trader.order import encode_fingerprint
    bond_buy = _bond_order()                    # side="buy", lot 슬롯 미설정
    enc = encode_fingerprint(bond_buy.fingerprint)
    assert len(enc) == 16
    assert enc == ["KR2033022D33", "buy", "limit", "10", "10000", "", "day", "BOND",
                   "", "", "regular", "", "KRX", "", "", "HKD"]
    # 매도는 lot 슬롯에 BUY_DT/BUY_SEQ 를 실어 매수와 구분된다(idx9=BUY_DT, idx13=BUY_SEQ).
    sell = _bond_order(side="sell", bond_buy_date="20240215", bond_buy_seq="1")
    enc_sell = encode_fingerprint(sell.fingerprint)
    assert enc_sell[9] == "20240215"
    assert enc_sell[13] == "1"
    assert len(enc_sell) == 16


# --- 정정·취소 와이어 빌더(TTTC0953U, 실전 전용) ------------------------------
def _change_report(*, order_id: str = "0001234567", symbol: str = "KR2033022D33") -> ExecutionReport:
    return ExecutionReport(
        client_order_id="c", order_id=order_id, symbol=symbol, side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.now(_KST),
    )


def _change_fp(*, symbol: str = "KR2033022D33", limit_price: str = "10000",
               quantity: str = "10") -> ImmediateOrderFingerprint:
    return ImmediateOrderFingerprint(
        symbol=symbol, side="buy", order_type="limit", quantity=quantity,
        limit_price=limit_price, stop_price="", time_in_force="day", exchange="BOND",
    )


def test_make_change_request_cancel_body():
    # 취소는 RVSE_CNCL_DVSN_CD="02", 원단가(원지문의 채권단가)를 유지한다.
    req = bond.make_change_request(
        original_report=_change_report(), original_fingerprint=_change_fp(),
        action="cancel", quantity=Decimal(10), limit_price=None,
        cano="81012345", product_code="03", environment="real",
    )
    assert req.method == "POST"
    assert req.path == _CHANGE
    assert req.tr_id == "TTTC0953U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "03", "PDNO": "KR2033022D33",
        "ORGN_ODNO": "0001234567", "ORD_QTY2": "10", "BOND_ORD_UNPR": "10000",
        "RVSE_CNCL_DVSN_CD": "02", "QTY_ALL_ORD_YN": "N",
        "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0", "CTAC_TLNO": "",
    }


def test_make_change_request_modify_body():
    # 정정은 RVSE_CNCL_DVSN_CD="01", 새 채권단가를 싣는다.
    req = bond.make_change_request(
        original_report=_change_report(), original_fingerprint=_change_fp(),
        action="modify", quantity=Decimal(10), limit_price=Decimal(10100),
        cano="81012345", product_code="03", environment="real",
    )
    assert req.tr_id == "TTTC0953U"
    assert req.body["RVSE_CNCL_DVSN_CD"] == "01"
    assert req.body["BOND_ORD_UNPR"] == "10100"
    assert req.body["ORGN_ODNO"] == "0001234567"
    assert req.body["ORD_QTY2"] == "10"


def test_make_change_request_modify_requires_price():
    # 채권 정정은 새 지정가(채권단가)가 필수 -- 없으면 공유 가격형상 헬퍼가 fail-closed.
    with pytest.raises(KISUsageError, match="limit_price"):
        bond.make_change_request(
            original_report=_change_report(), original_fingerprint=_change_fp(),
            action="modify", quantity=Decimal(10), limit_price=None,
            cano="8", product_code="03", environment="real",
        )


def test_make_change_request_modify_rejects_nonpositive_price():
    # 0/음수 채권단가 정정은 와이어에 닿기 전에 거부한다(공유 reject_bad_change_price_shape).
    with pytest.raises(KISUsageError, match="limit_price"):
        bond.make_change_request(
            original_report=_change_report(), original_fingerprint=_change_fp(),
            action="modify", quantity=Decimal(10), limit_price=Decimal(0),
            cano="8", product_code="03", environment="real",
        )


def test_make_change_request_cancel_rejects_price():
    # 취소에 채권단가를 주는 것은 형상 위반 -- 공유 헬퍼가 거부한다.
    with pytest.raises(KISUsageError, match="취소"):
        bond.make_change_request(
            original_report=_change_report(), original_fingerprint=_change_fp(),
            action="cancel", quantity=Decimal(10), limit_price=Decimal(10000),
            cano="8", product_code="03", environment="real",
        )


def test_make_change_request_paper_rejected():
    with pytest.raises(KISUsageError, match="모의"):
        bond.make_change_request(
            original_report=_change_report(), original_fingerprint=_change_fp(),
            action="cancel", quantity=Decimal(10), limit_price=None,
            cano="8", product_code="03", environment="paper",
        )


def test_make_change_request_partial_quantity_allowed():
    # 채권은 부분 정정·취소를 지원(ORD_QTY2) -- 잔량 미만 수량도 그대로 실린다.
    req = bond.make_change_request(
        original_report=_change_report(), original_fingerprint=_change_fp(),
        action="cancel", quantity=Decimal(4), limit_price=None,
        cano="8", product_code="03", environment="real",
    )
    assert req.body["ORD_QTY2"] == "4"


# --- end-to-end: place -> kis.orders.cancel/modify -> 전송 ------------------
def test_bond_cancel_wire():
    store = OrderStore()
    cid = "20260819-bondchg000000001"
    _client(FakeTransport(), store=store).domestic.bond("KR2033022D33").buy(
        quantity=10, limit_price=10000, client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    _client(change_t, store=store).orders.cancel(cid)
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "TTTC0953U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert call["body"]["ORGN_ODNO"] == "0001234567"       # 발주 응답의 ODNO
    assert call["body"]["BOND_ORD_UNPR"] == "10000"        # 원단가 유지


def test_bond_modify_wire():
    store = OrderStore()
    cid = "20260819-bondchg000000002"
    _client(FakeTransport(), store=store).domestic.bond("KR2033022D33").buy(
        quantity=10, limit_price=10000, client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    _client(change_t, store=store).orders.modify(cid, limit_price=10100)
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "TTTC0953U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert call["body"]["BOND_ORD_UNPR"] == "10100"


def test_bond_change_paper_fails_closed():
    # 모의 세션의 채권 정정·취소는 와이어에 닿기 전에 거부한다.
    store = OrderStore()
    order = _bond_order(client_order_id="bond-paper")
    report = _change_report(order_id="0001234567")
    store.record(report, order.fingerprint)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(change_t, environment="paper", store=store).orders.cancel("bond-paper")
    assert change_t.request_count == 0


def test_bond_change_idempotent():
    store = OrderStore()
    cid = "20260819-bondchg000000003"
    _client(FakeTransport(), store=store).domestic.bond("KR2033022D33").buy(
        quantity=10, limit_price=10000, client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    client = _client(change_t, store=store)
    rid = "20260819-bondchgreq00000001"
    client.orders.cancel(cid, request_id=rid)
    client.orders.cancel(cid, request_id=rid)
    assert change_t.request_count == 1          # 공유 안전 코어가 재전송을 막는다


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

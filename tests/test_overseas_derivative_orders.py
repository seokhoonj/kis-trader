"""해외선물옵션(08) 발주 -- kis.overseas.futures(srs_cd).buy/sell (OTFM3001U, 실전 전용).

국내주식과 같은 안전 코어(place)를 공유하되 와이어 조립기와 엄격 output 파서만 해외선물옵션용이다.
이중체결 방지·재시도 금지·dedup 지문(exchange 슬롯의 "OSFO" 값으로 구분)을 네트워크 없이 가짜
전송으로 검증한다. 해외선물옵션은 실전 전용(모의 미지원)이고 실주문이라 라이브 검증은 불가하며,
여기선 목킹 전송으로만 확인한다. 응답 output(``{ORD_DT, ODNO}``)에서 ODNO->order_id,
ORD_DT->receipt_date 가 리포트에 실려 이후 정정·취소(원주문일자 지목)가 가능해진다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader._internal._datetime import _KST
from kis_trader.errors import KISUsageError, OrderError
from kis_trader.order import ImmediateOrderFingerprint
from kis_trader.overseas._engine import derivative_orders as osfo
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.transport import RawResponse

_PLACE = "/uapi/overseas-futureoption/v1/trading/order"
_CHANGE = "/uapi/overseas-futureoption/v1/trading/order-rvsecncl"
_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"ORD_DT": "20260819", "ODNO": "0000007045"}},
)
_CHANGE_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="정정 전송 완료",
    body={"output": {"ODNO": "0000009999"}},
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


def _order(**kw):
    base = {
        "symbol": "6BZ22", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
        "limit_price": Decimal("1.17"), "exchange": "OSFO", "client_order_id": "c",
    }
    base.update(kw)
    return Order(**base)


# --- exchange marker ------------------------------------------------------
def test_is_overseas_fo_exchange():
    assert osfo.is_overseas_fo_exchange("OSFO") is True
    assert osfo.is_overseas_fo_exchange("XKFE") is False
    assert osfo.is_overseas_fo_exchange("BOND") is False


# --- 와이어 골든바디 ------------------------------------------------------
def test_make_order_request_limit_buy_body():
    req = osfo.make_order_request(_order(), cano="81012345", product_code="08",
                                  environment="real")
    assert req.method == "POST"
    assert req.path == osfo._PLACE_PATH
    assert req.tr_id == "OTFM3001U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "08", "OVRS_FUTR_FX_PDNO": "6BZ22",
        "SLL_BUY_DVSN_CD": "02",
        "FM_LQD_USTL_CCLD_DT": "", "FM_LQD_USTL_CCNO": "",
        "PRIC_DVSN_CD": "1", "FM_LIMIT_ORD_PRIC": "1.17", "FM_STOP_ORD_PRIC": "",
        "FM_ORD_QTY": "1", "FM_LQD_LMT_ORD_PRIC": "", "FM_LQD_STOP_ORD_PRIC": "",
        "CCLD_CNDT_CD": "6", "CPLX_ORD_DVSN_CD": "0", "ECIS_RSVN_ORD_YN": "N",
        "FM_HDGE_ORD_SCRN_YN": "N",
    }


def test_make_order_request_market_uses_price_division_2():
    req = osfo.make_order_request(_order(order_type="market", limit_price=None),
                                  cano="8", product_code="08", environment="real")
    assert req.body["PRIC_DVSN_CD"] == "2"
    assert req.body["CCLD_CNDT_CD"] == "2"        # 시장가 체결조건은 "2"(지정가/STOP 은 "6")
    assert req.body["FM_LIMIT_ORD_PRIC"] == ""
    assert req.body["FM_STOP_ORD_PRIC"] == ""


def test_make_order_request_stop_uses_price_division_3():
    order = _order(order_type="stop", limit_price=None, stop_price=Decimal("1.20"))
    req = osfo.make_order_request(order, cano="8", product_code="08", environment="real")
    assert req.body["PRIC_DVSN_CD"] == "3"
    assert req.body["FM_STOP_ORD_PRIC"] == "1.20"
    assert req.body["FM_LIMIT_ORD_PRIC"] == ""


def test_make_order_request_sell_side():
    req = osfo.make_order_request(_order(side="sell"), cano="8", product_code="08",
                                  environment="real")
    assert req.body["SLL_BUY_DVSN_CD"] == "01"


def test_make_order_request_rejects_non_osfo():
    order = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
                  limit_price=Decimal(70000), exchange="XKRX", client_order_id="c")
    with pytest.raises(OrderError):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


def test_make_order_request_paper_rejected():
    with pytest.raises(KISUsageError, match="모의"):
        osfo.make_order_request(_order(), cano="8", product_code="08", environment="paper")


def test_make_order_request_rejects_stop_limit():
    # 스탑지정가는 해외선물옵션 v1 미지원 -- 조용히 지정가로 나가지 않게 거부.
    order = _order(order_type="stop_limit", stop_price=Decimal("1.10"))
    with pytest.raises(KISUsageError, match="지정가/시장가/STOP"):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


def test_make_order_request_rejects_fractional_quantity():
    order = _order(quantity=Decimal("1.5"))
    with pytest.raises(KISUsageError, match="계약 단위 정수"):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


# --- 엄격 output 파서 -----------------------------------------------------
def test_extract_output_rejects_missing_output_key():
    with pytest.raises(OrderError):
        osfo.extract_output({"rt_cd": "0", "ODNO": "top-level", "ORD_DT": "20260819"})


def test_extract_output_rejects_non_mapping():
    with pytest.raises(OrderError):
        osfo.extract_output({"output": ["not", "a", "mapping"]})


def test_extract_output_accepts_mapping():
    # ODNO 는 그대로 노출하고, ORD_DT 는 시장 중립 접수-일자 키로 정규화해 place 가 receipt_date 로 읽는다.
    assert osfo.extract_output({"output": {"ODNO": "0000007045", "ORD_DT": "20260819"}}) == {
        "ODNO": "0000007045", "ORD_DT": "20260819", "_receipt_date": "20260819",
    }


def test_extract_output_omits_receipt_key_when_no_ord_dt():
    # ORD_DT 가 없으면 정규화 키를 넣지 않아 receipt_date 는 None 으로 남는다(다른 자산과 동일).
    assert osfo.extract_output({"output": {"ODNO": "0000007045"}}) == {"ODNO": "0000007045"}


# --- end-to-end: handle.buy -> _place_order -> 전송 -> ExecutionReport -----
def test_os_fo_buy_wire():
    fake = FakeTransport()
    report = _client(fake).overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    assert report.receipt_date == "20260819"      # ORD_DT 가 receipt_date 로 영속(정정·취소 대비)
    assert fake.request_count == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _PLACE
    assert call["tr_id"] == "OTFM3001U"
    assert call["body"]["OVRS_FUTR_FX_PDNO"] == "6BZ22"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "02"
    assert call["body"]["PRIC_DVSN_CD"] == "1"
    assert call["body"]["FM_LIMIT_ORD_PRIC"] == "1.17"
    assert call["body"]["FM_ORD_QTY"] == "1"


def test_os_fo_sell_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").sell(quantity=2, limit_price="1.20")
    assert fake.calls[0]["body"]["SLL_BUY_DVSN_CD"] == "01"
    assert fake.calls[0]["body"]["FM_ORD_QTY"] == "2"


def test_os_fo_market_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1)
    assert fake.calls[0]["body"]["PRIC_DVSN_CD"] == "2"
    assert fake.calls[0]["body"]["FM_LIMIT_ORD_PRIC"] == ""


def test_os_fo_stop_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1, stop_price="1.30")
    assert fake.calls[0]["body"]["PRIC_DVSN_CD"] == "3"
    assert fake.calls[0]["body"]["FM_STOP_ORD_PRIC"] == "1.30"


def test_os_fo_routes_to_overseas_fo_builder_not_stock_tr():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert fake.calls[0]["tr_id"] == "OTFM3001U"
    assert fake.calls[0]["path"] == _PLACE


def test_os_fo_buy_dedup_idempotent():
    store = OrderStore()
    fake = FakeTransport()
    cid = "20260819-osfobuy000000001"
    handle = _client(fake, store=store).overseas.futures("6BZ22")
    r1 = handle.buy(quantity=1, limit_price="1.17", client_order_id=cid)
    r2 = handle.buy(quantity=1, limit_price="1.17", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert fake.request_count == 1              # 공유 안전 코어가 재전송을 막는다


def test_os_fo_buy_paper_fails_closed():
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").overseas.futures("6BZ22").buy(
            quantity=1, limit_price="1.17")
    assert fake.request_count == 0              # 모의 세션은 와이어 미접촉


def test_os_fo_buy_rejects_risk_gate():
    from kis_trader.risk import RiskLimits
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(KISUsageError):
        client.overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert fake.request_count == 0


# --- 정정·취소 와이어 빌더(OTFM3002U 정정 / OTFM3003U 취소, 실전 전용) --------
def _change_report(*, order_id: str = "0000007045", symbol: str = "6BZ22",
                   receipt_date: str | None = "20260819") -> ExecutionReport:
    return ExecutionReport(
        client_order_id="c", order_id=order_id, symbol=symbol, side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.now(_KST), receipt_date=receipt_date,
    )


def _change_fp(*, symbol: str = "6BZ22", limit_price: str = "1.17",
               quantity: str = "1") -> ImmediateOrderFingerprint:
    return ImmediateOrderFingerprint(
        symbol=symbol, side="buy", order_type="limit", quantity=quantity,
        limit_price=limit_price, stop_price="", time_in_force="day", exchange="OSFO",
    )


def test_make_change_request_cancel_body():
    # 취소는 OTFM3003U, ORGN_ORD_DT=원주문일자(receipt_date), 가격 슬롯은 공란.
    req = osfo.make_change_request(
        original_report=_change_report(), original_fingerprint=_change_fp(),
        action="cancel", quantity=Decimal(1), limit_price=None,
        cano="81012345", product_code="08", environment="real",
    )
    assert req.method == "POST"
    assert req.path == _CHANGE
    assert req.tr_id == "OTFM3003U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "08",
        "ORGN_ORD_DT": "20260819", "ORGN_ODNO": "0000007045",
        "FM_LIMIT_ORD_PRIC": "", "FM_STOP_ORD_PRIC": "",
        "FM_LQD_LMT_ORD_PRIC": "", "FM_LQD_STOP_ORD_PRIC": "",
        "FM_HDGE_ORD_SCRN_YN": "N", "FM_MKPR_CVSN_YN": "N",
    }


def test_make_change_request_modify_body():
    # 정정은 OTFM3002U, FM_LIMIT_ORD_PRIC 에 새 지정가.
    req = osfo.make_change_request(
        original_report=_change_report(), original_fingerprint=_change_fp(),
        action="modify", quantity=Decimal(1), limit_price=Decimal("1.20"),
        cano="81012345", product_code="08", environment="real",
    )
    assert req.tr_id == "OTFM3002U"
    assert req.body["FM_LIMIT_ORD_PRIC"] == "1.20"
    assert req.body["ORGN_ORD_DT"] == "20260819"
    assert req.body["ORGN_ODNO"] == "0000007045"
    # FM_MKPR_CVSN_YN 은 취소(OTFM3003U) 전용 필드 -- 정정 바디엔 실리지 않는다.
    assert "FM_MKPR_CVSN_YN" not in req.body


def test_make_change_request_fails_closed_when_receipt_date_missing():
    # 원주문일자(receipt_date) 미영속이면 대상 특정 불가 -- fail-closed.
    with pytest.raises(KISUsageError, match="원주문일자"):
        osfo.make_change_request(
            original_report=_change_report(receipt_date=None),
            original_fingerprint=_change_fp(), action="cancel",
            quantity=Decimal(1), limit_price=None,
            cano="8", product_code="08", environment="real",
        )


def test_make_change_request_paper_rejected():
    with pytest.raises(KISUsageError, match="모의"):
        osfo.make_change_request(
            original_report=_change_report(), original_fingerprint=_change_fp(),
            action="cancel", quantity=Decimal(1), limit_price=None,
            cano="8", product_code="08", environment="paper",
        )


# --- end-to-end: place -> kis.orders.cancel/modify -> 전송 ------------------
def test_os_fo_cancel_wire():
    store = OrderStore()
    cid = "20260819-osfochg000000001"
    _client(FakeTransport(), store=store).overseas.futures("6BZ22").buy(
        quantity=1, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    _client(change_t, store=store).orders.cancel(cid)
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "OTFM3003U"
    assert call["body"]["ORGN_ORD_DT"] == "20260819"       # 발주 응답의 ORD_DT
    assert call["body"]["ORGN_ODNO"] == "0000007045"


def test_os_fo_modify_wire():
    store = OrderStore()
    cid = "20260819-osfochg000000002"
    _client(FakeTransport(), store=store).overseas.futures("6BZ22").buy(
        quantity=1, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    _client(change_t, store=store).orders.modify(cid, limit_price="1.20")
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "OTFM3002U"
    assert call["body"]["FM_LIMIT_ORD_PRIC"] == "1.20"
    assert call["body"]["ORGN_ORD_DT"] == "20260819"


def test_os_fo_modify_then_cancel_carries_receipt_date():
    # 정정 응답엔 ORD_DT 가 없어 재바인딩 리포트의 receipt_date 가 None 이 되면, 이후 취소가
    # 원주문일자(ORGN_ORD_DT) 부재로 fail-closed 되어 영영 취소 불가가 된다. 원리포트의 receipt_date
    # 를 재바인딩에 이어붙여, 정정 후에도 취소가 원주문일자로 와이어에 닿아야 한다.
    store = OrderStore()
    cid = "20260819-osfomodcxl00001"
    _client(FakeTransport(), store=store).overseas.futures("6BZ22").buy(
        quantity=1, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    client = _client(change_t, store=store)
    client.orders.modify(cid, limit_price="1.20")
    client.orders.cancel(cid)
    assert change_t.request_count == 2                    # 정정·취소 둘 다 와이어에 닿음
    cancel_call = change_t.calls[-1]
    assert cancel_call["tr_id"] == "OTFM3003U"
    assert cancel_call["body"]["ORGN_ORD_DT"] == "20260819"  # 원 ORD_DT 가 정정 후에도 유지
    assert cancel_call["body"]["ORGN_ODNO"] == "0000009999"  # 정정으로 채번된 새 ODNO


def test_os_fo_change_paper_fails_closed():
    store = OrderStore()
    order = _order(client_order_id="osfo-paper")
    store.record(_change_report(), order.fingerprint)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    with pytest.raises(KISUsageError):
        _client(change_t, environment="paper", store=store).orders.cancel("osfo-paper")
    assert change_t.request_count == 0


def test_os_fo_change_partial_quantity_rejected():
    # 해외선물옵션 정정·취소는 전량만 -- 잔량 미만 수량 지정은 와이어 전에 거부.
    store = OrderStore()
    cid = "20260819-osfochg000000003"
    _client(FakeTransport(), store=store).overseas.futures("6BZ22").buy(
        quantity=5, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    with pytest.raises(KISUsageError, match="부분"):
        _client(change_t, store=store).orders.cancel(cid, quantity=2)
    assert change_t.request_count == 0


def test_os_fo_change_fails_closed_when_receipt_date_missing():
    # 발주 응답에 ORD_DT 가 없어 receipt_date 가 미영속이면, 정정·취소가 fail-closed.
    store = OrderStore()
    cid = "20260819-osfochg000000004"
    place_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0000007045"}}))
    _client(place_t, store=store).overseas.futures("6BZ22").buy(
        quantity=1, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    with pytest.raises(KISUsageError, match="원주문일자"):
        _client(change_t, store=store).orders.cancel(cid)
    assert change_t.request_count == 0


def test_os_fo_change_idempotent():
    store = OrderStore()
    cid = "20260819-osfochg000000005"
    _client(FakeTransport(), store=store).overseas.futures("6BZ22").buy(
        quantity=1, limit_price="1.17", client_order_id=cid)
    change_t = FakeTransport(response=_CHANGE_ACCEPTED)
    client = _client(change_t, store=store)
    rid = "20260819-osfochgreq00000001"
    client.orders.cancel(cid, request_id=rid)
    client.orders.cancel(cid, request_id=rid)
    assert change_t.request_count == 1          # 공유 안전 코어가 재전송을 막는다


# --- reconcile fail-closed (미확인 OSFO 주문은 국내주식 일별체결조회로 오조회하지 않는다) ---
def test_os_fo_reconcile_fails_closed_not_silent_none():
    # in-flight 해외선물옵션 지문의 재조회는 엉뚱한 테이블을 훑어 None 을 돌려주는 대신, 미지원임을
    # 명시하며 fail-closed 해야 한다(채권 fail-closed 분기와 대칭).
    store = OrderStore()
    order = _order(client_order_id="osfo-inflight")
    store.try_claim("osfo-inflight", order.fingerprint)
    client = _client(FakeTransport(), store=store)
    with pytest.raises(KISUsageError, match="재조회"):
        client.orders.reconcile("osfo-inflight")

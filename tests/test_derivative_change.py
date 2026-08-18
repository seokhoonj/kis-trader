"""국내 선물·옵션 주간(DAY) 정정·취소 와이어 빌더 + client._change_order 의 XKFE 라우팅.

order-rvsecncl TTTO1103U/VTTO1103U. 파생은 원주문 지목에 ``ORGN_ODNO`` 만 쓴다(국내주식과 달리
``KRX_FWDG_ORD_ORGNO`` 조직번호 계약을 재사용하지 않는다). 취소는 확정 고정값, 지정가 정정은
원지문에서 코드 3필드를 산출하고 새 단가를 싣는다. 야간(STTN1103U)은 Task 7. 네트워크 없이
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader._internal._datetime import _KST
from kis_trader.domestic._engine import derivative_orders as fo
from kis_trader.errors import KISUsageError
from kis_trader.order import ImmediateOrderFingerprint
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.transport import RawResponse

_CHANGE = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"


def _report(*, order_id: str, symbol: str = "101S03") -> ExecutionReport:
    return ExecutionReport(
        client_order_id="c", order_id=order_id, symbol=symbol, side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.now(_KST),
    )


def _fp(*, order_type: str = "limit", division: str = "", symbol: str = "101S03",
        session: str = "regular") -> ImmediateOrderFingerprint:
    return ImmediateOrderFingerprint(
        symbol=symbol, side="buy", order_type=order_type, quantity="1",
        limit_price="400.00", stop_price="", time_in_force="day", exchange="XKFE",
        session=session, division=division, derivative_item="01",
    )


# --- Step 1: 취소 확정값 + ORGN_ODNO(조직번호 없음) ------------------------
def test_cancel_body_uses_confirmed_fixed_values():
    req = fo.make_change_request(
        original_report=_report(order_id="0000005605"),
        original_fingerprint=_fp(order_type="limit"), action="cancel",
        quantity=Decimal(1), limit_price=None, cano="8", product_code="03",
        environment="real",
    )
    assert req.method == "POST"
    assert req.path == _CHANGE
    assert req.tr_id == "TTTO1103U"
    assert req.body["RVSE_CNCL_DVSN_CD"] == "02"
    assert req.body["ORD_DVSN_CD"] == "01"
    assert req.body["KRX_NMPR_CNDT_CD"] == "0"
    assert req.body["UNIT_PRICE"] == "0"
    assert req.body["RMN_QTY_YN"] == "Y"
    assert req.body["NMPR_TYPE_CD"] == "01"
    assert req.body["ORGN_ODNO"] == "0000005605"
    assert req.body["ORD_QTY"] == "0"                 # 주간 전량취소 = 0
    assert req.body["FUOP_ITEM_DVSN_CD"] == ""        # 주간은 공란(야간은 Task 7)
    assert "KRX_FWDG_ORD_ORGNO" not in req.body       # 파생엔 조직번호 없음


def test_cancel_paper_uses_paper_tr():
    req = fo.make_change_request(
        original_report=_report(order_id="0000005605"),
        original_fingerprint=_fp(), action="cancel", quantity=Decimal(1),
        limit_price=None, cano="8", product_code="03", environment="paper",
    )
    assert req.tr_id == "VTTO1103U"


# --- Step 2: 지정가 정정 -- 원지문에서 코드 산출 + 새 단가 -----------------
def test_limit_modify_resolves_codes_from_original_and_sets_new_price():
    req = fo.make_change_request(
        original_report=_report(order_id="0000005605"),
        original_fingerprint=_fp(order_type="limit"), action="modify",
        quantity=Decimal(1), limit_price=Decimal("401.00"), cano="8",
        product_code="03", environment="real",
    )
    assert req.body["RVSE_CNCL_DVSN_CD"] == "01"
    assert req.body["UNIT_PRICE"] == "401.00"
    assert req.body["ORGN_ODNO"] == "0000005605"
    assert req.body["ORD_QTY"] == "1"
    # 원지문 (limit, day) -> (ORD_DVSN_CD, NMPR_TYPE_CD, KRX_NMPR_CNDT_CD) = (01, 01, 0)
    assert req.body["ORD_DVSN_CD"] == "01"
    assert req.body["NMPR_TYPE_CD"] == "01"
    assert req.body["KRX_NMPR_CNDT_CD"] == "0"
    assert req.body["RMN_QTY_YN"] == "N"              # 일부(지정 수량)
    assert "KRX_FWDG_ORD_ORGNO" not in req.body


def test_conditional_limit_modify_resolves_from_division():
    req = fo.make_change_request(
        original_report=_report(order_id="0000005605"),
        original_fingerprint=_fp(order_type="limit", division="conditional_limit"),
        action="modify", quantity=Decimal(1), limit_price=Decimal("401.00"),
        cano="8", product_code="03", environment="real",
    )
    # (conditional_limit, day) -> (03, 03, 0)
    assert req.body["ORD_DVSN_CD"] == "03"
    assert req.body["NMPR_TYPE_CD"] == "03"


def test_market_original_modify_is_rejected():
    with pytest.raises(KISUsageError, match="시장가/최유리 정정은 미지원"):
        fo.make_change_request(
            original_report=_report(order_id="0000005605"),
            original_fingerprint=_fp(order_type="market"), action="modify",
            quantity=Decimal(1), limit_price=Decimal("401.00"), cano="8",
            product_code="03", environment="real",
        )


def test_immediate_limit_original_modify_is_rejected():
    with pytest.raises(KISUsageError, match="시장가/최유리 정정은 미지원"):
        fo.make_change_request(
            original_report=_report(order_id="0000005605"),
            original_fingerprint=_fp(order_type="market", division="immediate_limit"),
            action="modify", quantity=Decimal(1), limit_price=Decimal("401.00"),
            cano="8", product_code="03", environment="real",
        )


def test_limit_modify_requires_positive_price():
    with pytest.raises(KISUsageError):
        fo.make_change_request(
            original_report=_report(order_id="0000005605"),
            original_fingerprint=_fp(order_type="limit"), action="modify",
            quantity=Decimal(1), limit_price=None, cano="8", product_code="03",
            environment="real",
        )


def test_night_change_is_seamed_for_task7():
    with pytest.raises(KISUsageError):
        fo.make_change_request(
            original_report=_report(order_id="0000005605"),
            original_fingerprint=_fp(session="night"), action="cancel",
            quantity=Decimal(1), limit_price=None, cano="8", product_code="03",
            environment="real",
        )


# --- Step 3: client._change_order XKFE 라우팅(e2e) ------------------------
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                        body={"output": {"ODNO": "0000005605"}})


class FakeTransport:
    def __init__(self, *, response=_ACCEPTED, by_path=None):
        self.response = response
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        return self.by_path.get(path, self.response)


def _order(**kw):
    base = {
        "symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
        "limit_price": Decimal("400.00"), "exchange": "XKFE", "session": "regular",
        "derivative_item": "01", "client_order_id": "c",
    }
    base.update(kw)
    return Order(**base)


def _client(transport, *, store=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=transport, store=store)


def test_cancel_routes_xkfe_through_derivative_builder():
    store = OrderStore()
    place_t = FakeTransport()
    _client(place_t, store=store)._place_order(_order())
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0000005605"}}))
    _client(change_t, store=store).orders.cancel("c")
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "TTTO1103U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert call["body"]["ORGN_ODNO"] == "0000005605"
    assert call["body"]["ORD_QTY"] == "0"
    assert "KRX_FWDG_ORD_ORGNO" not in call["body"]


def test_modify_routes_xkfe_through_derivative_builder():
    store = OrderStore()
    place_t = FakeTransport()
    _client(place_t, store=store)._place_order(_order(quantity=Decimal(2)))
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0000005699"}}))
    _client(change_t, store=store).orders.modify("c", limit_price="401.00")
    call = change_t.calls[0]
    assert call["path"] == _CHANGE
    assert call["tr_id"] == "TTTO1103U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert call["body"]["UNIT_PRICE"] == "401.00"

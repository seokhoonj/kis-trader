"""해외 주문 -- kis.ticker(symbol, exchange=...).buy()/sell().

해외 주문이 공유 안전 코어(이중체결 방지·재시도 금지)를 거쳐 해외 와이어(TR/EXCD)로 나가는지,
지정가 필수, 해외 timeout 재조회 게이트, risk 세션 거부, 접수 응답(ODNO) 처리를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import ExecutionReport, KisClient, RiskLimits
from kis_openapi.errors import KisUsageError, OrderTimeoutError
from kis_openapi.transport import RawResponse, TransportTimeout

_ORDER = "/uapi/overseas-stock/v1/trading/order"


class FakeTransport:
    def __init__(self, *, response=None, raises=None, on_post=None, on_get=None):
        self.response = response
        self.raises = raises
        self.on_post = on_post      # 주문(POST) 전용 응답/예외
        self.on_get = on_get        # 재조회(GET) 전용 응답
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        if method == "POST" and self.on_post is not None:
            outcome = self.on_post
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if method == "GET" and self.on_get is not None:
            return self.on_get
        if self.raises is not None:
            raise self.raises
        return self.response


def _ccnl(rows):
    return RawResponse(rt_cd="0", msg_cd="0", msg1="정상", body={"output": rows})


def _ccnl_row(*, pdno="AAPL", side="02", qty="1", ord_unpr="150.00", ccld_qty="1",
              ccld_unpr="150.10", odno="0000123456", rjct=""):
    return {"pdno": pdno, "sll_buy_dvsn_cd": side, "ft_ord_qty": qty, "ft_ord_unpr3": ord_unpr,
            "ft_ccld_qty": ccld_qty, "ft_ccld_unpr3": ccld_unpr, "odno": odno, "rjct_rson": rjct}


def _ack(odno="0000123456"):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": odno,
                                        "ORD_TMD": "093015"}})


def _client(transport, **kw):
    return KisClient(app_key="k", app_secret="s", account="12345678-01", transport=transport, **kw)


def test_overseas_buy_routes_to_overseas_wire():
    fake = FakeTransport(response=_ack())
    report = _client(fake).ticker("AAPL", exchange="NAS").buy(
        quantity=3, price="150.25", client_order_id="oid-1"
    )
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000123456"             # ODNO 처리(도메스틱과 동일)
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER
    assert call["tr_id"] == "TTTT1002U"                # 미국 매수 실전
    assert call["body"]["OVRS_EXCG_CD"] == "NASD"
    assert call["body"]["ORD_QTY"] == "3"
    assert call["body"]["OVRS_ORD_UNPR"] == "150.25"
    assert "SLL_TYPE" not in call["body"]


def test_overseas_sell_sets_sll_type_and_tr():
    fake = FakeTransport(response=_ack())
    _client(fake).ticker("AAPL", exchange="NAS").sell(
        quantity=1, price="151.00", client_order_id="oid-2"
    )
    assert fake.calls[0]["tr_id"] == "TTTT1006U"       # 미국 매도 실전
    assert fake.calls[0]["body"]["SLL_TYPE"] == "00"


def test_overseas_market_order_rejected():
    fake = FakeTransport(response=_ack())
    with pytest.raises(KisUsageError, match="지정가"):
        _client(fake).ticker("AAPL", exchange="NAS").buy(quantity=1)  # price 없음


def test_overseas_order_dedup_replays_report():
    # 같은 client_order_id 재전송은 와이어에 다시 안 나가고 이전 리포트를 돌려준다(이중체결 방지).
    fake = FakeTransport(response=_ack())
    handle = _client(fake).ticker("AAPL", exchange="NAS")
    first = handle.buy(quantity=1, price="150.00", client_order_id="dup")
    second = handle.buy(quantity=1, price="150.00", client_order_id="dup")
    assert first.order_id == second.order_id
    assert len(fake.calls) == 1                         # 두 번째는 와이어 미접촉


def test_overseas_order_timeout_then_reconcile_confirms():
    # 주문 POST 는 timeout, 재조회 GET 은 지문과 맞는 체결 1건 -> 확정.
    fake = FakeTransport(on_post=TransportTimeout("timeout"), on_get=_ccnl([_ccnl_row()]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.ticker("AAPL", exchange="NAS").buy(quantity=1, price="150.00", client_order_id="to")
    report = client.reconcile("to")                    # 해외 체결내역으로 확정
    assert report is not None
    assert report.order_id == "0000123456"
    assert report.filled_quantity == Decimal(1)
    assert fake.calls[-1]["path"] == "/uapi/overseas-stock/v1/trading/inquire-ccnl"
    assert fake.calls[-1]["tr_id"] == "TTTS3035R"


def test_overseas_reconcile_zero_matches_stays_none():
    # 지문과 맞는 체결이 없으면 미접수로 단정하지 않고 None(재전송 금지 유지).
    fake = FakeTransport(on_post=TransportTimeout("timeout"),
                         on_get=_ccnl([_ccnl_row(pdno="MSFT")]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.ticker("AAPL", exchange="NAS").buy(quantity=1, price="150.00", client_order_id="z")
    assert client.reconcile("z") is None


def test_overseas_order_with_risk_session_rejected():
    fake = FakeTransport(response=_ack())
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(KisUsageError, match="리스크"):
        client.ticker("AAPL", exchange="NAS").buy(quantity=1, price="150.00", client_order_id="r1")
    assert len(fake.calls) == 0                         # 거부는 와이어 전

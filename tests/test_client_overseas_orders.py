"""해외 주문 -- kis.ticker(symbol, exchange=...).buy()/sell().

해외 주문이 공유 안전 코어(이중체결 방지·재시도 금지)를 거쳐 해외 와이어(TR/EXCD)로 나가는지,
지정가 필수, 해외 timeout 재조회 게이트, risk 세션 거부, 접수 응답(ODNO) 처리를 검증한다.
"""

from __future__ import annotations

import threading

import pytest

from kis_openapi import ExecutionReport, KisClient, RiskLimits
from kis_openapi.errors import KisUsageError, OrderTimeoutError
from kis_openapi.transport import RawResponse, TransportTimeout

_ORDER = "/uapi/overseas-stock/v1/trading/order"


class FakeTransport:
    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        if self.raises is not None:
            raise self.raises
        return self.response


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


def test_overseas_order_timeout_then_reconcile_gated():
    client = _client(FakeTransport(raises=TransportTimeout("timeout")))
    with pytest.raises(OrderTimeoutError):
        client.ticker("AAPL", exchange="NAS").buy(quantity=1, price="150.00", client_order_id="to")
    # 해외 timeout 재조회는 아직 미구현 -> 명확히 거부(재전송 금지 유지, 수동 확인 안내).
    with pytest.raises(KisUsageError, match="해외 주문.*재조회"):
        client.reconcile("to")


def test_overseas_order_with_risk_session_rejected():
    fake = FakeTransport(response=_ack())
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(KisUsageError, match="리스크"):
        client.ticker("AAPL", exchange="NAS").buy(quantity=1, price="150.00", client_order_id="r1")
    assert len(fake.calls) == 0                         # 거부는 와이어 전

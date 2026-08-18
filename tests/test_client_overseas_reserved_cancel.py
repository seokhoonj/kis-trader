"""미국 해외예약주문 취소 -- kis.overseas.account.cancel_reserved_order (order-resv-ccnl TTTT3017U).

예약번호 대상 멱등 연산이라 dedup 스토어는 안 거치되 무재시도는 유지한다. 네트워크 없이 가짜
전송으로 검증한다. 취소는 (RSVN_ORD_RCIT_DT + OVRS_RSVN_ODNO)로 지목, 성공 = rt_cd 0.
"""

from __future__ import annotations

import threading

import pytest

from kis_trader import KISClient
from kis_trader.errors import (
    KISError,
    KISUsageError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_trader.transport import RawResponse, TransportTimeout

_CANCEL = "/uapi/overseas-stock/v1/trading/order-resv-ccnl"

_OK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상",
                  body={"output": {"OVRS_RSVN_ODNO": "0031111234"}})
_REJECTED = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="취소 불가", body={})


class FakeTransport:
    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "body": body, "idempotent": idempotent})
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_cancel_overseas_reserved_wire():
    fake = FakeTransport(response=_OK)
    _client(fake).overseas.account.cancel_reserved_order("0031111234", receipt_date="20250523")
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _CANCEL
    assert call["idempotent"] is False              # 무재시도
    assert call["tr_id"] == "TTTT3017U"
    assert call["body"]["OVRS_RSVN_ODNO"] == "0031111234"
    assert call["body"]["RSVN_ORD_RCIT_DT"] == "20250523"


def test_cancel_overseas_reserved_needs_id():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.cancel_reserved_order("", receipt_date="20250523")
    assert fake.calls == []


def test_cancel_overseas_reserved_needs_receipt_date():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.cancel_reserved_order("0031111234", receipt_date="")
    assert fake.calls == []


def test_cancel_overseas_reserved_bad_date_rejected_before_io():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.cancel_reserved_order("0031111234", receipt_date="20250230")
    assert fake.calls == []


def test_cancel_overseas_reserved_paper_allowed_uses_v_tr():
    # 원장상 미국 예약취소는 모의(VTTT3017U)를 지원한다 -- paper 에서 막지 않고 V TR 로 나간다.
    fake = FakeTransport(response=_OK)
    _client(fake, environment="paper").overseas.account.cancel_reserved_order(
        "0031111234", receipt_date="20250523")
    assert fake.calls[0]["tr_id"] == "VTTT3017U"


def test_cancel_overseas_reserved_rejected_raises():
    with pytest.raises(OrderRejectedError):
        _client(FakeTransport(response=_REJECTED)).overseas.account.cancel_reserved_order(
            "0031111234", receipt_date="20250523")


def test_cancel_overseas_reserved_timeout_no_retry():
    fake = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(fake).overseas.account.cancel_reserved_order("0031111234", receipt_date="20250523")
    assert len(fake.calls) == 1                      # 재전송 없음


@pytest.mark.parametrize("body", [
    {},                                           # output 없음
    {"output": {}},                               # 확인번호 없음
    {"output": {"OVRS_RSVN_ODNO": ""}},           # 빈 확인번호
    {"output": {"OVRS_RSVN_ODNO": "9999999999"}}, # 요청과 불일치
])
def test_cancel_overseas_reserved_bad_echo_fails_closed(body):
    # rt_cd=0 이어도 에코된 OVRS_RSVN_ODNO 가 요청과 다르거나 부재면 취소 확인 불가 -> KISError
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="", body=body)
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.cancel_reserved_order(
            "0031111234", receipt_date="20250523")


def test_cancel_overseas_reserved_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_OK), account=None).overseas.account.cancel_reserved_order(
            "0031111234", receipt_date="20250523")

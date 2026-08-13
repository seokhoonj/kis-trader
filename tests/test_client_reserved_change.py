"""예약주문 정정/취소 -- kis.domestic.account.cancel_reserved_order / kis.domestic.account.modify_reserved_order.

order-resv-rvsecncl 취소 CTSC0009U / 정정 CTSC0013U. 순번 대상 멱등 연산이라 dedup 스토어는 안 거치되
무재시도는 유지한다. 네트워크 없이 가짜 전송으로 검증한다. 응답은 원장상 output.nrml_prcs_yn.
"""

from __future__ import annotations

import threading

import pytest

from kis_openapi import KISClient
from kis_openapi.errors import (
    KISError,
    KISUsageError,
    OrderRejectedError,
    OrderTimeoutError,
)
from kis_openapi.transport import RawResponse, TransportTimeout

_CHANGE = "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"

_OK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상", body={"output": {"nrml_prcs_yn": "Y"}})
_NOT_PROCESSED = RawResponse(rt_cd="0", msg_cd="APBK0000", msg1="처리안됨",
                             body={"output": {"nrml_prcs_yn": "N"}})
_REJECTED = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="정정취소 불가", body={})


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


# --- 취소 ------------------------------------------------------------------
def test_cancel_reserved_wire():
    fake = FakeTransport(response=_OK)
    _client(fake).domestic.account.cancel_reserved_order("42401", order_date="20240603")
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _CHANGE
    assert call["idempotent"] is False              # 무재시도
    assert call["tr_id"] == "CTSC0009U"             # 취소
    assert call["body"]["RSVN_ORD_SEQ"] == "42401"
    assert call["body"]["RSVN_ORD_ORD_DT"] == "20240603"


def test_cancel_reserved_needs_sequence():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.cancel_reserved_order("")
    assert fake.calls == []


def test_cancel_reserved_demo_rejected():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.account.cancel_reserved_order("42401")
    assert fake.calls == []


def test_cancel_reserved_not_processed_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_NOT_PROCESSED)).domestic.account.cancel_reserved_order("42401")


def test_cancel_reserved_rejected_raises():
    with pytest.raises(OrderRejectedError):
        _client(FakeTransport(response=_REJECTED)).domestic.account.cancel_reserved_order("42401")


def test_cancel_reserved_timeout_no_retry():
    fake = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(fake).domestic.account.cancel_reserved_order("42401")
    assert len(fake.calls) == 1                      # 재전송 없음


def test_cancel_reserved_bad_order_date_rejected_before_io():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.cancel_reserved_order("42401", order_date="20240631")   # 6월 31일 없음
    assert fake.calls == []


# --- 정정 ------------------------------------------------------------------
def test_modify_reserved_wire():
    fake = FakeTransport(response=_OK)
    _client(fake).domestic.account.modify_reserved_order("42401", symbol="005930", side="buy", quantity=2,
                                        limit_price=71000, end_date="20240610", order_date="20240603")
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["tr_id"] == "CTSC0013U"             # 정정
    assert call["path"] == _CHANGE
    assert call["idempotent"] is False
    body = call["body"]
    assert body["CANO"] == "12345678"
    assert body["ACNT_PRDT_CD"] == "01"
    assert body["RSVN_ORD_SEQ"] == "42401"
    assert body["PDNO"] == "005930"
    assert body["SLL_BUY_DVSN_CD"] == "02"          # buy
    assert body["ORD_QTY"] == "2"
    assert body["ORD_DVSN_CD"] == "00"              # 지정가
    assert body["ORD_UNPR"] == "71000"
    assert body["RSVN_ORD_END_DT"] == "20240610"
    assert body["ORD_OBJT_CBLC_DVSN_CD"] == "10"
    assert body["LOAN_DT"] == "" and body["CTAC_TLNO"] == ""
    assert body["RSVN_ORD_ORD_DT"] == "20240603"
    assert body["RSVN_ORD_ORGNO"] == ""


def test_modify_reserved_rejected_raises():
    with pytest.raises(OrderRejectedError):
        _client(FakeTransport(response=_REJECTED)).domestic.account.modify_reserved_order(
            "42401", symbol="005930", side="buy", quantity=1, limit_price=1)


def test_modify_reserved_demo_rejected_before_io():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.account.modify_reserved_order(
            "42401", symbol="005930", side="buy", quantity=1, limit_price=1)
    assert fake.calls == []


@pytest.mark.parametrize("kwargs", [
    {"end_date": "20240631"}, {"end_date": "2024-06-10"}, {"order_date": "20240631"},
])
def test_modify_reserved_bad_dates_rejected_before_io(kwargs):
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.modify_reserved_order("42401", symbol="005930", side="buy", quantity=1,
                                            limit_price=1, **kwargs)
    assert fake.calls == []


def test_modify_reserved_market_price():
    fake = FakeTransport(response=_OK)
    _client(fake).domestic.account.modify_reserved_order("42401", symbol="005930", side="sell", quantity=1)
    call = fake.calls[0]
    assert call["body"]["SLL_BUY_DVSN_CD"] == "01"  # sell
    assert call["body"]["ORD_DVSN_CD"] == "01"      # 시장가
    assert call["body"]["ORD_UNPR"] == "0"


@pytest.mark.parametrize("bad_qty", ["0", "-1", "1.5"])
def test_modify_reserved_bad_quantity_rejected_before_io(bad_qty):
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.modify_reserved_order("42401", symbol="005930", side="buy", quantity=bad_qty,
                                            limit_price=1)
    assert fake.calls == []


def test_modify_reserved_needs_sequence():
    fake = FakeTransport(response=_OK)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.account.modify_reserved_order("", symbol="005930", side="buy", quantity=1, limit_price=1)
    assert fake.calls == []


def test_modify_reserved_timeout_no_retry():
    fake = FakeTransport(raises=TransportTimeout("t"))
    with pytest.raises(OrderTimeoutError):
        _client(fake).domestic.account.modify_reserved_order("42401", symbol="005930", side="buy", quantity=1, limit_price=1)
    assert len(fake.calls) == 1


def test_modify_reserved_not_processed_raises():
    with pytest.raises(KISError):
        _client(FakeTransport(response=_NOT_PROCESSED)).domestic.account.modify_reserved_order(
            "42401", symbol="005930", side="buy", quantity=1, limit_price=1)


def test_reserved_change_array_output_success():
    # output 이 배열 형태로 와도 정상처리 판독
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": [{"nrml_prcs_yn": "Y"}]})
    _client(FakeTransport(response=resp)).domestic.account.cancel_reserved_order("42401")  # 예외 없이 성공


def test_reserved_change_top_level_nrml_prcs_yn_success():
    # nrml_prcs_yn 이 본문 최상위로 와도 판독(layout=output 하위지만 예시 미확정 -> 양쪽 확인)
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"nrml_prcs_yn": "Y"})
    _client(FakeTransport(response=resp)).domestic.account.cancel_reserved_order("42401")


@pytest.mark.parametrize("body", [
    {},                                   # output 없음
    {"output": None},                     # non-mapping
    {"output": []},                       # 빈 배열
    {"output": {}},                       # nrml_prcs_yn 없음
    {"output": {"nrml_prcs_yn": ""}},     # 빈 값
])
def test_reserved_change_malformed_output_fails_closed(body):
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="", body=body)
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.account.cancel_reserved_order("42401")


def test_reserved_change_multi_row_output_fails_closed():
    # output 이 다건이면 어느 행이 이 요청의 확인인지 특정 불가 -- output[0] 을 맹신하지 않고 fail-closed.
    resp = RawResponse(rt_cd="0", msg_cd="A", msg1="",
                       body={"output": [{"nrml_prcs_yn": "Y"}, {"nrml_prcs_yn": "N"}]})
    with pytest.raises(KISError, match="다건"):
        _client(FakeTransport(response=resp)).domestic.account.cancel_reserved_order("42401")


def test_reserved_change_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_OK), account=None).domestic.account.cancel_reserved_order("42401")

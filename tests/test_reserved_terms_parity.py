"""A-38: 예약주문 발주/정정의 공유 항목-정규화 -- 두 경로가 ONE 경로를 쓴다는 회귀 방지.

발주(kis.domestic.stock(...).reserve_buy)와 정정(kis.domestic.account.modify_reserved_order)은
방향/수량/주문구분/단가/종료일 정규화와 공통 바디 매핑을 공유한다. 공통 부분의 와이어가 바이트
동일하고, 종료일 검증이 두 경로에서 일관됨(place 만 있던 폭-사전검사 drift 제거)을 검증한다.
"""

from __future__ import annotations

import threading

import pytest

from kis_openapi import KISClient
from kis_openapi._domestic.reserved_orders import (
    _coerce_reserved_order_terms,
    _make_reserved_order_fields,
)
from kis_openapi.errors import KISUsageError
from kis_openapi.transport import RawResponse

_PLACE = "/uapi/domestic-stock/v1/trading/order-resv"
_CHANGE = "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"

# 발주는 output.rsvn_ord_seq, 정정은 output.nrml_prcs_yn 로 성공을 확인한다.
_PLACE_OK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="접수",
                        body={"output": [{"rsvn_ord_seq": "42401"}]})
_MODIFY_OK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="정상",
                         body={"output": {"nrml_prcs_yn": "Y"}})

#: 공통(발주/정정 양쪽 바디에 함께 존재하는) 키 -- A-38 이 단일 헬퍼로 조립하는 부분.
_SHARED_KEYS = (
    "CANO", "ACNT_PRDT_CD", "PDNO", "ORD_QTY", "ORD_UNPR", "SLL_BUY_DVSN_CD",
    "ORD_DVSN_CD", "ORD_OBJT_CBLC_DVSN_CD", "LOAN_DT", "RSVN_ORD_END_DT",
)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "body": body})
        return self.response


def _client(transport, *, environment="real"):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     environment=environment, transport=transport)


def _place_body(**over):
    args = {"quantity": 2, "limit_price": 71000, "end_date": "20240610"}
    args.update(over)
    fake = FakeTransport(_PLACE_OK)
    _client(fake).domestic.stock("005930").reserve_buy(**args)
    return next(c["body"] for c in fake.calls if c["path"] == _PLACE)


def _modify_body(**over):
    args = {"symbol": "005930", "side": "buy", "quantity": 2, "limit_price": 71000, "end_date": "20240610"}
    args.update(over)
    fake = FakeTransport(_MODIFY_OK)
    _client(fake).domestic.account.modify_reserved_order("42401", **args)
    return next(c["body"] for c in fake.calls if c["path"] == _CHANGE)


def test_place_and_modify_share_byte_identical_common_body():
    """발주와 정정의 공통 바디 필드가 같은 입력에 대해 바이트 동일하다(단일 정규화 경로)."""
    place = _place_body()
    modify = _modify_body()
    for key in _SHARED_KEYS:
        assert place[key] == modify[key], key
    # 정정만의 대상-지정 키는 공통 바디 뒤에 온다(삽입 순서 보존).
    assert list(place.keys()) == list(_SHARED_KEYS)
    assert list(modify.keys())[: len(_SHARED_KEYS)] == list(_SHARED_KEYS)


def test_place_and_modify_market_price_share_common_body():
    """limit_price 생략(시장가)도 두 경로가 동일 정규화(ORD_DVSN_CD=01, ORD_UNPR=0)."""
    place = _place_body(limit_price=None, end_date=None)
    modify = _modify_body(limit_price=None, end_date=None)
    for key in _SHARED_KEYS:
        assert place[key] == modify[key], key
    assert place["ORD_DVSN_CD"] == "01" and place["ORD_UNPR"] == "0"


@pytest.mark.parametrize("bad_end_date", ["2024-06-10", "2026139", "20240631", "2024061"])
def test_end_date_validation_consistent_across_place_and_modify(bad_end_date):
    """A-38: 종료일 검증이 두 경로에서 일관 -- 같은 malformed end_date 를 둘 다 와이어 전에 거부한다
    (예전엔 place 만 별도 폭-사전검사를 가졌다)."""
    place_fake = FakeTransport(_PLACE_OK)
    with pytest.raises(KISUsageError):
        _client(place_fake).domestic.stock("005930").reserve_buy(quantity=1, limit_price=1, end_date=bad_end_date)
    assert place_fake.calls == []

    modify_fake = FakeTransport(_MODIFY_OK)
    with pytest.raises(KISUsageError):
        _client(modify_fake).domestic.account.modify_reserved_order(
            "42401", symbol="005930", side="buy", quantity=1, limit_price=1, end_date=bad_end_date)
    assert modify_fake.calls == []


def test_coerce_reserved_order_terms_shared_helper():
    """공유 정규화 헬퍼 단위 검증: 지정가/시장가, 정수 수량, 유한·양수 단가."""
    limit = _coerce_reserved_order_terms(side="buy", quantity=2, limit_price=71000, end_date="20240610")
    assert (limit.order_type, str(limit.quantity), str(limit.limit_price)) == ("limit", "2", "71000")
    market = _coerce_reserved_order_terms(side="sell", quantity=3, limit_price=None, end_date=None)
    assert market.order_type == "market" and market.limit_price is None
    for bad in ({"quantity": "1.5"}, {"quantity": 0}, {"limit_price": 0}, {"limit_price": "Infinity"}):
        args = {"side": "buy", "quantity": 1, "limit_price": 1, "end_date": None}
        args.update(bad)
        with pytest.raises(KISUsageError):
            _coerce_reserved_order_terms(**args)


def test_make_reserved_order_fields_common_shape():
    """공유 바디 헬퍼는 정확히 공통 키만, 지정 순서대로 낸다."""
    terms = _coerce_reserved_order_terms(side="buy", quantity=2, limit_price=71000, end_date="20240610")
    body = _make_reserved_order_fields(
        cano="12345678", product_code="01", symbol="005930", side="buy",
        terms=terms, end_date="20240610",
    )
    assert list(body.keys()) == list(_SHARED_KEYS)
    assert body["SLL_BUY_DVSN_CD"] == "02" and body["ORD_DVSN_CD"] == "00"

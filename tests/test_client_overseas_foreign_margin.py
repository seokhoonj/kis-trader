"""통화별 해외증거금 -- kis.overseas.account.foreign_margin() (TTTC2101R).

통화별 외화 예수금·증거금·주문가능금액을 Money(통화 포함)로 검증한다. 픽스처는 원장
응답예시(foreign-margin) 실값을 쓴다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Money, OverseasForeignMargin
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/overseas-stock/v1/trading/foreign-margin"

# 원장 응답예시(foreign-margin) 실값 2건.
_USD = {
    "natn_name": "미국", "crcy_cd": "USD", "frcr_dncl_amt1": "698.190000",
    "ustl_buy_amt": "0.00", "ustl_sll_amt": "0.00", "frcr_rcvb_amt": "0.00",
    "frcr_mgn_amt": "0.000000", "frcr_gnrl_ord_psbl_amt": "694.37",
    "frcr_ord_psbl_amt1": "0.000000", "itgr_ord_psbl_amt": "1094.52",
    "bass_exrt": "1349.40000000",
}
_HKD = {
    "natn_name": "홍콩", "crcy_cd": "HKD", "frcr_dncl_amt1": "0.000000",
    "ustl_buy_amt": "0.00", "ustl_sll_amt": "0.00", "frcr_rcvb_amt": "0.00",
    "frcr_mgn_amt": "0.000000", "frcr_gnrl_ord_psbl_amt": "0.00",
    "frcr_ord_psbl_amt1": "0.000000", "itgr_ord_psbl_amt": "8247.35",
    "bass_exrt": "172.97000000",
}


def _resp(rows=None):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": rows if rows is not None else [_USD, _HKD]}, tr_cont="")


class FakeTransport:
    def __init__(self, *, response=None):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        assert self.response is not None
        return self.response


def _client(transport, *, profile="main", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     profile=profile, transport=transport)


def test_foreign_margin_parses_per_currency():
    margins = _client(FakeTransport(response=_resp())).overseas.account.foreign_margin()
    assert len(margins) == 2
    usd = margins[0]
    assert isinstance(usd, OverseasForeignMargin)
    assert usd.country_name == "미국"
    assert usd.currency == "USD"
    assert usd.deposit == Money(Decimal("698.190000"), "USD")
    assert usd.general_orderable_amount == Money(Decimal("694.37"), "USD")
    assert usd.integrated_orderable_amount == Money(Decimal("1094.52"), "USD")
    assert usd.exchange_rate == Decimal("1349.40000000")
    assert margins[1].currency == "HKD"
    assert margins[1].integrated_orderable_amount == Money(Decimal("8247.35"), "HKD")


def test_foreign_margin_tr_method_params():
    fake = FakeTransport(response=_resp())
    _client(fake).overseas.account.foreign_margin()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC2101R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"] == {"CANO": "12345678", "ACNT_PRDT_CD": "01"}


def test_foreign_margin_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, profile="paper").overseas.account.foreign_margin()
    assert fake.calls == []


def test_foreign_margin_empty_is_ok():
    assert _client(FakeTransport(response=_resp([]))).overseas.account.foreign_margin() == []


def test_foreign_margin_skips_padding_row():
    margins = _client(FakeTransport(response=_resp([dict(_USD, crcy_cd=""), _USD]))).overseas.account.foreign_margin()
    assert len(margins) == 1


def test_foreign_margin_non_list_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {"crcy_cd": "USD"}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.foreign_margin()


def test_foreign_margin_garbage_amount_fails_closed():
    bad = dict(_USD, frcr_dncl_amt1="oops")
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp([bad]))).overseas.account.foreign_margin()


def test_foreign_margin_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.foreign_margin()


def test_foreign_margin_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).overseas.account.foreign_margin()

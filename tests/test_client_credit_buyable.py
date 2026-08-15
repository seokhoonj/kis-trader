"""신용매수가능조회 -- kis.domestic.stock(symbol).credit_buyable() (TTTC8909R).

현금 매수가능과 output 형상이 같아 BuyableAmount 를 공유한다. 네트워크 없이 FakeTransport 로
검증하며, 픽스처는 원장 응답예시(inquire-credit-psamount) 실값을 쓴다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import BuyableAmount, KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/inquire-credit-psamount"

# 원장 응답예시(inquire-credit-psamount) 실값.
_OUTPUT = {
    "ord_psbl_cash": "99965177664", "ord_psbl_sbst": "156772560", "ruse_psbl_amt": "0",
    "fund_rpch_chgs": "0", "psbl_qty_calc_unpr": "69200", "nrcvb_buy_amt": "0",
    "nrcvb_buy_qty": "0", "max_buy_amt": "0", "max_buy_qty": "0", "cma_evlu_amt": "0",
    "ovrs_re_use_amt_wcrc": "0", "ord_psbl_frcr_amt_wcrc": "157998704172856",
}


def _resp(output=None):
    body = {"output": _OUTPUT if output is None else output}
    return RawResponse(rt_cd="0", msg_cd="KIOK0510", msg1="조회 완료", body=body, tr_cont="")


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


def test_credit_buyable_parses_shared_fields():
    result = _client(FakeTransport(response=_resp())).domestic.stock("005930").credit_buyable(limit_price="55000")
    assert isinstance(result, BuyableAmount)
    assert result.symbol == "005930"
    assert result.currency == "KRW"
    assert result.orderable_cash == Decimal(99965177664)
    assert result.reusable_cash == Decimal(0)
    assert result.max_buyable_quantity == Decimal(0)
    # 신용 전용 필드는 _raw 로
    assert result._raw["ord_psbl_sbst"] == "156772560"


def test_credit_buyable_tr_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).domestic.stock("005930").credit_buyable(limit_price="55000")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC8909R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["PDNO"] == "005930"
    assert call["params"]["ORD_UNPR"] == "55000"
    assert call["params"]["ORD_DVSN"] == "00"       # 지정가
    assert call["params"]["CRDT_TYPE"] == "21"       # 기본 자기융자신규


def test_credit_buyable_market_price_sends_zero():
    fake = FakeTransport(response=_resp())
    _client(fake).domestic.stock("005930").credit_buyable()   # limit_price 없음 -> 시장가
    call = fake.calls[0]
    assert call["params"]["ORD_DVSN"] == "01"         # 시장가
    assert call["params"]["ORD_UNPR"] == "0"          # 공란 대신 "0"


def test_credit_buyable_custom_credit_type():
    fake = FakeTransport(response=_resp())
    _client(fake).domestic.stock("005930").credit_buyable(credit_type="23", limit_price="1")
    assert fake.calls[0]["params"]["CRDT_TYPE"] == "23"


def test_credit_buyable_unknown_credit_type_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").credit_buyable(credit_type="99", limit_price="1")
    assert fake.calls == []


def test_credit_buyable_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, profile="paper").domestic.stock("005930").credit_buyable(limit_price="1")
    assert fake.calls == []


@pytest.mark.parametrize("bad", ["0", "-1", "nan"])
def test_credit_buyable_bad_price_rejected_before_io(bad):
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930").credit_buyable(limit_price=bad)
    assert fake.calls == []


def test_credit_buyable_missing_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.stock("005930").credit_buyable(limit_price="1")


def test_credit_buyable_absent_on_overseas_stock():
    # credit_buyable 은 국내 전용 -- 해외 핸들엔 아예 없다(자산군 분리로 구조적 보장).
    handle = _client(FakeTransport(response=_resp())).overseas.stock("AAPL", exchange="NAS")
    assert not hasattr(handle, "credit_buyable")


def test_credit_buyable_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).domestic.stock("005930").credit_buyable(limit_price="1")

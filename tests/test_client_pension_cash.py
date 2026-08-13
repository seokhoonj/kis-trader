"""퇴직연금 예수금·매수가능 -- kis.pension.deposit() / kis.pension.buyable() (TTTC0506R/TTTC0503R).

네트워크 없이 FakeTransport 로 검증한다. 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, PensionBuyableAmount, PensionDeposit
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_DEPOSIT_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-deposit"
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/pension/inquire-psbl-order"

_DEPOSIT_OUT = {
    "dnca_tota": "57622382", "nxdy_excc_amt": "11054042",
    "nxdy_sttl_amt": "0", "nx2_day_sttl_amt": "0",
}
_BUYABLE_OUT = {
    "ord_psbl_cash": "11054042", "ruse_psbl_amt": "0", "psbl_qty_calc_unpr": "55000",
    "max_buy_amt": "11054042", "max_buy_qty": "200",
}


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="KIOK0510", msg1="조회", body={"output": output}, tr_cont="")


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


def _client(transport, *, environment="real", account="12345678-29"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


# --- 예수금 ---------------------------------------------------------------
def test_pension_deposit_parses():
    dep = _client(FakeTransport(response=_resp(_DEPOSIT_OUT))).pension.deposit()
    assert isinstance(dep, PensionDeposit)
    assert dep.total_deposit == Decimal(57622382)
    assert dep.next_day_estimated_settlement_amount == Decimal(11054042)
    assert dep.next_day_settlement_amount == Decimal(0)
    assert dep.second_day_settlement_amount == Decimal(0)


def test_pension_deposit_tr_and_params():
    fake = FakeTransport(response=_resp(_DEPOSIT_OUT))
    _client(fake).pension.deposit()
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC0506R"
    assert call["method"] == "GET"
    assert call["path"] == _DEPOSIT_PATH
    assert call["idempotent"] is True
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "29"
    assert call["params"]["ACCA_DVSN_CD"] == "00"


def test_pension_deposit_demo_rejected_before_io():
    fake = FakeTransport(response=_resp(_DEPOSIT_OUT))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").pension.deposit()
    assert fake.calls == []


def test_pension_deposit_missing_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).pension.deposit()


def test_pension_deposit_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output": {}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).pension.deposit()


def test_pension_deposit_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp(_DEPOSIT_OUT)), account=None).pension.deposit()


# --- 매수가능 -------------------------------------------------------------
def test_pension_buyable_parses():
    buyable = _client(FakeTransport(response=_resp(_BUYABLE_OUT))).pension.buyable("005930", limit_price="55000")
    assert isinstance(buyable, PensionBuyableAmount)
    assert buyable.symbol == "005930"
    assert buyable.orderable_cash == Decimal(11054042)
    assert buyable.calc_unit_price == Decimal(55000)
    assert buyable.max_buyable_quantity == Decimal(200)


def test_pension_buyable_limit_price_params():
    fake = FakeTransport(response=_resp(_BUYABLE_OUT))
    _client(fake).pension.buyable("005930", limit_price="55000")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTC0503R"
    assert call["path"] == _BUYABLE_PATH
    assert call["params"]["PDNO"] == "005930"
    assert call["params"]["ORD_DVSN"] == "00"       # 지정가
    assert call["params"]["ORD_UNPR"] == "55000"
    assert call["params"]["ACCA_DVSN_CD"] == "00"


def test_pension_buyable_market_sends_zero():
    fake = FakeTransport(response=_resp(_BUYABLE_OUT))
    _client(fake).pension.buyable("005930")   # 시장가
    call = fake.calls[0]
    assert call["params"]["ORD_DVSN"] == "01"
    assert call["params"]["ORD_UNPR"] == "0"


@pytest.mark.parametrize("bad", ["0", "-1", "nan"])
def test_pension_buyable_bad_price_rejected_before_io(bad):
    fake = FakeTransport(response=_resp(_BUYABLE_OUT))
    with pytest.raises(KISUsageError):
        _client(fake).pension.buyable("005930", limit_price=bad)
    assert fake.calls == []


def test_pension_buyable_demo_rejected_before_io():
    fake = FakeTransport(response=_resp(_BUYABLE_OUT))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").pension.buyable("005930", limit_price="1")
    assert fake.calls == []

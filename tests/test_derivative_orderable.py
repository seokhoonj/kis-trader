"""파생 계약 주문가능조회 -- kis.domestic.futures(code).orderable(side) /
option(code).orderable(side).

주간 주문가능(TTTO5105R/VTTO5105R)과 야간 주문가능(STTN5105R, 실전 전용)을 네트워크 없이
FakeTransport 로 검증한다: 라우팅(TR/경로), 파라미터(PDNO/SLL_BUY_DVSN_CD/UNIT_PRICE/
ORD_DVSN_CD), 파싱, output 부재 fail-closed.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.domestic.entities.derivative_account import DerivativeOrderable
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_ORDERABLE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-psbl-order"
_NIGHT_ORDERABLE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-psbl-ngt-order"


class FakeTransport:
    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "tr_cont": tr_cont})
        if self.raises is not None:
            raise self.raises
        assert self.response is not None, "FakeTransport 에 응답을 줘야 한다"
        return self.response


def _orderable_output(*, ord_psbl="7", tot_psbl="10", lqd1="3", bass="410.25"):
    return {"tot_psbl_qty": tot_psbl, "lqd_psbl_qty1": lqd1,
            "ord_psbl_qty": ord_psbl, "bass_idx": bass}


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


def _client(transport, *, environment="paper", account="12345678-03"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_orderable_parses_and_routes_real():
    fake = FakeTransport(response=_resp(_orderable_output()))
    result = _client(fake, environment="real").domestic.futures("101W09").orderable(
        "buy", limit_price=Decimal("410.50"))
    assert isinstance(result, DerivativeOrderable)
    assert result.orderable_quantity == Decimal(7)
    assert result.total_quantity == Decimal(10)
    assert result.liquidatable_quantity == Decimal(3)
    assert result.base_index == Decimal("410.25")
    assert result._raw["ord_psbl_qty"] == "7"
    call = fake.calls[0]
    assert call["path"] == _ORDERABLE_PATH
    assert call["tr_id"] == "TTTO5105R"                 # 실전
    assert call["params"]["PDNO"] == "101W09"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "02"    # 매수
    assert call["params"]["UNIT_PRICE"] == "410.50"
    assert call["params"]["ORD_DVSN_CD"] == "01"        # 지정가


def test_orderable_routes_paper():
    fake = FakeTransport(response=_resp(_orderable_output()))
    _client(fake, environment="paper").domestic.futures("101W09").orderable("sell")
    call = fake.calls[0]
    assert call["tr_id"] == "VTTO5105R"                 # 모의
    assert call["params"]["SLL_BUY_DVSN_CD"] == "01"    # 매도
    assert call["params"]["UNIT_PRICE"] == "0"          # limit 없음 -> 0
    assert call["params"]["ORD_DVSN_CD"] == "02"        # 시장가


def test_orderable_liquidatable_falls_back():
    # lqd_psbl_qty1 부재면 lqd_psbl_qty 로 폴백.
    output = {"tot_psbl_qty": "10", "lqd_psbl_qty": "4",
              "ord_psbl_qty": "7", "bass_idx": "410.25"}
    fake = FakeTransport(response=_resp(output))
    result = _client(fake).domestic.option("201W09335").orderable("buy")
    assert result.liquidatable_quantity == Decimal(4)


def test_orderable_option_uses_pdno():
    fake = FakeTransport(response=_resp(_orderable_output()))
    _client(fake).domestic.option("201W09335").orderable("buy")
    assert fake.calls[0]["params"]["PDNO"] == "201W09335"


def test_orderable_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.futures("101W09").orderable("buy")


def test_orderable_requires_account():
    fake = FakeTransport(response=_resp(_orderable_output()))
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    with pytest.raises(KISUsageError):
        client.domestic.futures("101W09").orderable("buy")
    assert fake.calls == []


# --- 야간 주문가능(STTN5105R, 실전 전용) -----------------------------------
def _night_output(*, ord_psbl="5", tot_psbl="8", lqd="2", bass="410.25", max_ord="8"):
    return {"max_ord_psbl_qty": max_ord, "tot_psbl_qty": tot_psbl, "lqd_psbl_qty": lqd,
            "lqd_psbl_qty_1": lqd, "ord_psbl_qty": ord_psbl, "bass_idx": bass}


def test_night_orderable_parses_and_routes():
    fake = FakeTransport(response=_resp(_night_output()))
    result = _client(fake, environment="real").domestic.futures("101W09").night_orderable(
        "buy", limit_price=Decimal("410.50"))
    assert isinstance(result, DerivativeOrderable)
    assert result.orderable_quantity == Decimal(5)
    assert result.total_quantity == Decimal(8)
    assert result.liquidatable_quantity == Decimal(2)
    assert result.base_index == Decimal("410.25")
    assert result._raw["max_ord_psbl_qty"] == "8"       # 야간 전용 필드는 _raw 로
    call = fake.calls[0]
    assert call["path"] == _NIGHT_ORDERABLE_PATH
    assert call["tr_id"] == "STTN5105R"
    assert call["params"]["PDNO"] == "101W09"
    assert call["params"]["PRDT_TYPE_CD"] == "301"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "02"     # 매수
    assert call["params"]["UNIT_PRICE"] == "410.50"
    assert call["params"]["ORD_DVSN_CD"] == "01"         # 지정가


def test_night_orderable_paper_fails_closed_no_wire():
    fake = FakeTransport(response=_resp(_night_output()))
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.futures("101W09").night_orderable("buy")
    assert fake.calls == []                              # 모의는 와이어 미접촉


def test_night_orderable_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake, environment="real").domestic.futures("101W09").night_orderable("buy")


def test_night_orderable_market_uses_zero_price():
    fake = FakeTransport(response=_resp(_night_output()))
    _client(fake, environment="real").domestic.option("201W09335").night_orderable("sell")
    call = fake.calls[0]
    assert call["params"]["UNIT_PRICE"] == "0"
    assert call["params"]["ORD_DVSN_CD"] == "02"         # 시장가
    assert call["params"]["SLL_BUY_DVSN_CD"] == "01"     # 매도

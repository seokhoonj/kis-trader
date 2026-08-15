"""해외주식 매수가능금액 -- kis.overseas.account.buyable(symbol, exchange=, price=) (TTTS3007R).

EXCD->OVRS_EXCG_CD 매핑, 외화 금액을 Money(통화 포함)로, 실전/모의 TR, fail-closed 를
네트워크 없이 검증한다. 픽스처는 원장 응답예시(inquire-psamount) 구조를 따른다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Money, OverseasBuyableAmount
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/overseas-stock/v1/trading/inquire-psamount"

# 원장 응답예시 구조(마스킹된 예시 값을 온전한 수치로 대체).
_OUTPUT = {
    "echm_af_ord_psbl_amt": "0.00", "echm_af_ord_psbl_qty": "0",
    "exrt": "165.5400000000", "frcr_ord_psbl_amt1": "95500.12",
    "max_ord_psbl_qty": "744", "ord_psbl_frcr_amt": "99900.52",
    "ord_psbl_qty": "744", "ovrs_max_ord_psbl_qty": "717",
    "ovrs_ord_psbl_amt": "99200.35", "sll_ruse_psbl_amt": "0.00",
    "tr_crcy_cd": "HKD",
}


def _resp(output=None):
    body = {"output": _OUTPUT if output is None else output}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont="")


class FakeTransport:
    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent})
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


def _client(transport, *, profile="main", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     profile=profile, transport=transport)


def test_overseas_buyable_parses_ledger_output():
    result = _client(FakeTransport(response=_resp())).overseas.account.buyable(
        "00011", exchange="HKS", price="133.200"
    )
    assert isinstance(result, OverseasBuyableAmount)
    assert result.symbol == "00011"
    assert result.exchange == "SEHK"          # HKS -> SEHK
    assert result.currency == "HKD"
    assert result.orderable_amount == Money(Decimal("99200.35"), "HKD")
    assert result.max_quantity == Decimal(744)
    assert result.integrated_orderable_amount == Money(Decimal("95500.12"), "HKD")
    assert result.integrated_max_quantity == Decimal(717)
    assert result.orderable_foreign_cash == Money(Decimal("99900.52"), "HKD")
    assert result.reusable_sell_amount == Money(Decimal("0.00"), "HKD")
    assert result.exchange_rate == Decimal("165.5400000000")


def test_overseas_buyable_tr_method_params_and_excg_mapping():
    fake = FakeTransport(response=_resp())
    _client(fake).overseas.account.buyable("AAPL", exchange="NAS", price="150.25")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTS3007R"
    assert call["method"] == "GET"
    assert call["idempotent"] is True
    assert call["path"] == _PATH
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"    # NAS -> NASD
    assert call["params"]["ITEM_CD"] == "AAPL"
    assert call["params"]["OVRS_ORD_UNPR"] == "150.25"


def test_overseas_buyable_demo_tr():
    fake = FakeTransport(response=_resp())
    _client(fake, profile="paper").overseas.account.buyable("AAPL", exchange="NAS", price="1")
    assert fake.calls[0]["tr_id"] == "VTTS3007R"


def test_overseas_buyable_unknown_exchange_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.buyable("AAPL", exchange="XXX", price="1")
    assert fake.calls == []


@pytest.mark.parametrize("price", ["0", "-5", "nan", "abc"])
def test_overseas_buyable_bad_price_rejected_before_io(price):
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake).overseas.account.buyable("AAPL", exchange="NAS", price=price)
    assert fake.calls == []


def test_overseas_buyable_missing_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.buyable("AAPL", exchange="NAS", price="1")


def test_overseas_buyable_absent_amount_reads_zero():
    thin = {"tr_crcy_cd": "USD", "ovrs_ord_psbl_amt": "10.00"}
    result = _client(FakeTransport(response=_resp(thin))).overseas.account.buyable(
        "AAPL", exchange="NAS", price="1"
    )
    assert result.orderable_amount == Money(Decimal("10.00"), "USD")
    assert result.max_quantity == Decimal(0)           # 부재 -> 0
    assert result.reusable_sell_amount == Money(Decimal(0), "USD")


def test_overseas_buyable_garbage_amount_fails_closed():
    bad = dict(_OUTPUT, ovrs_ord_psbl_amt="oops")
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(bad))).overseas.account.buyable(
            "AAPL", exchange="NAS", price="1"
        )


def test_overseas_buyable_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="ERR", msg1="실패", body={"output": {}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.buyable("AAPL", exchange="NAS", price="1")


def test_overseas_buyable_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).overseas.account.buyable(
            "AAPL", exchange="NAS", price="1"
        )

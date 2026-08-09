"""멀티종목 시세 -- kis.quotes([...]) / kis.overseas.quotes([...]).

국내(intstock-multprice FHKST11300006)·해외(multprice HHDFS76220000)의 슬롯 매핑, 보드/거래소
혼합, Quote 매핑(국내 부호복원, 해외 base 로 등락 계산), 상한 초과 거부, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, Quote
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _dom_row(code, name, prpr, vrss, sign, ctrt):
    return {"inter_shrn_iscd": code, "inter_kor_isnm": name, "inter2_prpr": prpr,
            "inter2_oprc": "71000", "inter2_hgpr": "72000", "inter2_lwpr": "70500",
            "inter2_prdy_clpr": "71000", "inter2_prdy_vrss": vrss, "prdy_vrss_sign": sign,
            "prdy_ctrt": ctrt, "acml_vol": "1000000"}


def test_domestic_quotes_maps_and_slot_params():
    rows = [_dom_row("005930", "삼성전자", "71500", "500", "2", "0.70"),
            _dom_row("035720", "카카오", "48000", "300", "5", "-0.62")]
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                                              body={"output": rows}))
    quotes = _client(fake).domestic.quotes(["005930", "035720"])
    assert all(isinstance(q, Quote) for q in quotes)
    assert quotes[0].symbol == "005930"
    assert quotes[0].last == Decimal(71500)
    assert quotes[0].change == Decimal(500)              # sign 2 -> 양수
    assert quotes[1].change == Decimal(-300)             # sign 5 -> 음수
    assert quotes[0].market == "KRX"                     # 기본 보드
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/intstock-multprice"
    assert call["tr_id"] == "FHKST11300006"
    assert call["params"]["FID_INPUT_ISCD_1"] == "005930"
    assert call["params"]["FID_COND_MRKT_DIV_CODE_1"] == "J"    # KRX -> J
    assert call["params"]["FID_INPUT_ISCD_2"] == "035720"
    assert call["params"]["FID_INPUT_ISCD_30"] == ""           # 남는 슬롯 공백
    assert call["params"]["FID_COND_MRKT_DIV_CODE_30"] == ""


def test_domestic_quotes_mixed_boards_via_tuples():
    rows = [_dom_row("005930", "삼성전자", "71500", "500", "2", "0.70"),
            _dom_row("123456", "NXT종목", "10000", "0", "3", "0.00")]
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                                              body={"output": rows}))
    quotes = _client(fake).domestic.quotes(["005930", ("NXT", "123456")])
    by_symbol = {q.symbol: q for q in quotes}
    assert by_symbol["005930"].market == "KRX"
    assert by_symbol["123456"].market == "NXT"           # 응답 코드로 보드 되짚음
    call = fake.calls[0]
    assert call["params"]["FID_COND_MRKT_DIV_CODE_1"] == "J"    # KRX
    assert call["params"]["FID_COND_MRKT_DIV_CODE_2"] == "NX"   # NXT


def test_domestic_quotes_rejects_over_30():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": []}))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.quotes([f"{i:06d}" for i in range(31)])


def _ovs_row(excd, symb, last, base, curr="USD"):
    return {"rsym": f"D{excd}{symb}", "excd": excd, "symb": symb, "knam": symb, "last": last,
            "base": base, "open": "196.0", "high": "198.0", "low": "195.5", "tvol": "1000000",
            "curr": curr, "sign": "2", "diff": "1.0", "rate": "0.5",
            "h52p": "260.0", "l52p": "150.0"}


def test_overseas_quotes_maps_and_mixed_exchanges():
    rows = [_ovs_row("NAS", "AAPL", "197.0", "195.0"),
            _ovs_row("HKS", "00700", "300.0", "310.0", curr="HKD")]
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                                              body={"output2": rows}))
    quotes = _client(fake).overseas.quotes([("NAS", "AAPL"), ("HKS", "00700")])
    by_symbol = {q.symbol: q for q in quotes}
    aapl = by_symbol["AAPL"]
    assert aapl.market == "NAS"
    assert aapl.currency == "USD"
    assert aapl.last == Decimal("197.0")
    assert aapl.previous_close == Decimal("195.0")       # base
    assert aapl.change == Decimal("2.0")                 # last - base
    tencent = by_symbol["00700"]
    assert tencent.market == "HKS"
    assert tencent.currency == "HKD"
    assert tencent.change == Decimal("-10.0")            # 300 - 310
    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-price/v1/quotations/multprice"
    assert call["tr_id"] == "HHDFS76220000"
    assert call["params"]["NREC"] == "2"
    assert call["params"]["EXCD_01"] == "NAS"
    assert call["params"]["SYMB_01"] == "AAPL"
    assert call["params"]["EXCD_02"] == "HKS"
    assert call["params"]["EXCD_10"] == ""               # 남는 슬롯 공백


def test_overseas_quotes_rejects_over_10():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output2": []}))
    with pytest.raises(KISUsageError):
        _client(fake).overseas.quotes([("NAS", f"S{i}") for i in range(11)])


def test_multi_quotes_empty_returns_empty_without_call():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    assert _client(fake).domestic.quotes([]) == []
    assert _client(fake).overseas.quotes([]) == []
    assert fake.calls == []                              # 빈 요청은 와이어 접촉 안 함


def test_domestic_quotes_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.quotes(["005930"])

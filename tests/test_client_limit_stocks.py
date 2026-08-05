"""상하한가 포착 -- kis.market.limit_stocks(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, LimitStock
from kis_openapi.errors import KISError
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


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def test_limit_stocks_maps_ledger_values_upper():
    rows = [{"mksc_shrn_iscd": "012800", "hts_kor_isnm": "대창", "stck_prpr": "2080",
             "prdy_vrss_sign": "1", "prdy_vrss": "478", "prdy_ctrt": "29.84",
             "acml_vol": "39937550", "total_askp_rsqn": "0", "total_bidp_rsqn": "2648946",
             "stck_llam": "1122", "stck_mxpr": "2080", "prdy_vrss_vol_rate": "997.66"}]
    fake = FakeTransport(response=_resp(rows))
    stocks = _client(fake).market.limit_stocks()
    assert isinstance(stocks[0], LimitStock)
    s = stocks[0]
    assert s.symbol == "012800"
    assert s.price == Decimal(2080)
    assert s.change == Decimal(478)                      # sign 1 -> 상승(상한)
    assert s.upper_limit == Decimal(2080)
    assert s.lower_limit == Decimal(1122)
    assert s.total_bid_quantity == 2648946
    assert s.at_upper_limit is True                      # 현재가 == 상한가
    assert s._raw["prdy_vrss_vol_rate"] == "997.66"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/capture-uplowprice"
    assert call["tr_id"] == "FHKST130000C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11300"


def test_limit_stocks_lower_limit_not_at_upper():
    rows = [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "x", "stck_prpr": "1122",
             "prdy_vrss_sign": "5", "prdy_vrss": "478", "prdy_ctrt": "-29.84",
             "acml_vol": "100", "total_askp_rsqn": "500", "total_bidp_rsqn": "0",
             "stck_llam": "1122", "stck_mxpr": "2080"}]
    fake = FakeTransport(response=_resp(rows))
    s = _client(fake).market.limit_stocks()[0]
    assert s.change == Decimal(-478)                     # sign 5 -> 하락(하한)
    assert s.at_upper_limit is False
    assert s.price == s.lower_limit


def test_limit_stocks_skips_empty_and_missing_output():
    fake = FakeTransport(response=_resp([{"mksc_shrn_iscd": ""}]))
    assert _client(fake).market.limit_stocks() == []
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake2).market.limit_stocks()


def test_limit_stocks_bad_value_fails_closed():
    rows = [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "x", "stck_prpr": "n/a",
             "prdy_vrss_sign": "1", "prdy_vrss": "1", "prdy_ctrt": "1", "acml_vol": "1",
             "stck_llam": "1", "stck_mxpr": "2", "total_askp_rsqn": "0", "total_bidp_rsqn": "0"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).market.limit_stocks()

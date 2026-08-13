"""시황/공시 뉴스 -- kis.domestic.market.news(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading

import pytest

from kis_trader import KISClient, NewsHeadline
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


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


def test_news_maps_and_symbols():
    rows = [{"cntt_usiq_srno": "2024041217173779111", "data_dt": "20240412", "data_tm": "171737",
             "hts_pbnt_titl_cntt": "금융투자협회 라운드테이블", "news_lrdv_code": "10", "dorg": "뉴스핌",
             "iscd1": "005930", "iscd2": "000660", "iscd3": "", "iscd4": ""}]
    fake = FakeTransport(response=_resp(rows))
    items = _client(fake).domestic.market.news()
    assert isinstance(items[0], NewsHeadline)
    n = items[0]
    assert n.title == "금융투자협회 라운드테이블"
    assert n.source == "뉴스핌"
    assert n.symbols == ("005930", "000660")             # 비어있는 iscd 제외
    assert f"{n.timestamp:%Y%m%d %H%M%S}" == "20240412 171737"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/news-title"
    assert call["tr_id"] == "FHKST01011800"
    assert call["params"]["FID_INPUT_ISCD"] == ""


def test_news_symbol_and_date_filter():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.market.news(symbol="005930", date="20240412")
    call = fake.calls[0]
    assert call["params"]["FID_INPUT_ISCD"] == "005930"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240412"


def test_news_skips_empty_and_missing_output():
    fake = FakeTransport(response=_resp([{"data_dt": "", "data_tm": ""}]))
    assert _client(fake).domestic.market.news() == []
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake2).domestic.market.news()

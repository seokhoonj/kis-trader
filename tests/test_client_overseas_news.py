"""해외뉴스 종합과 해외속보 제목 조회."""

import pytest

from kis_trader import KISClient, NewsHeadline, OverseasNewsHeadline
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(
        self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
    ):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _client(*responses):
    return KISClient(app_key="k", app_secret="s", environment="real", transport=FakeTransport(responses))


def _news_response(*, tr_cont=""):
    row = {"info_gb": "01", "news_key": "key", "data_dt": "20240510", "data_tm": "093000",
           "class_cd": "10", "class_name": "기업", "source": "Reuters", "nation_cd": "US",
           "exchange_cd": "NAS", "symb": "AAPL", "symb_name": "Apple", "title": "Headline"}
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont,
                       body={"outblock1": [row]})


def test_overseas_news_maps_filters_and_paginates():
    client = _client(_news_response(tr_cont="M"), _news_response())
    headlines = client.overseas.news(country="US", symbol="AAPL", date_="20240510")
    assert isinstance(headlines[0], OverseasNewsHeadline)
    assert headlines[0].timestamp.hour == 9
    assert headlines[0].title == "Headline"
    assert client.transport.calls[0]["tr_id"] == "HHPSTH60100C1"
    assert client.transport.calls[0]["params"]["NATION_CD"] == "US"
    assert [call["tr_cont"] for call in client.transport.calls] == ["", "N"]


def test_overseas_news_blank_timestamp_raises_kis_error_not_value_error():
    # 게시시각이 공란/깨진 행은 bare ValueError 가 아니라 KISError(맥락 포함)로 올라야 한다.
    row = {"info_gb": "01", "news_key": "k", "data_dt": "", "data_tm": "",
           "class_cd": "10", "class_name": "기업", "source": "R", "nation_cd": "US",
           "exchange_cd": "NAS", "symb": "AAPL", "symb_name": "Apple", "title": "H"}
    client = _client(RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"outblock1": [row]}))
    with pytest.raises(KISError, match="게시시각"):
        client.overseas.news(country="US", symbol="AAPL", date_="20240510")


def test_overseas_breaking_news_reuses_news_item_and_routes():
    row = {"cntt_usiq_srno": "1", "data_dt": "20240510", "data_tm": "101500",
           "hts_pbnt_titl_cntt": "Breaking", "dorg": "Newswire", "news_lrdv_code": "A",
           "iscd1": "AAPL", "iscd2": "MSFT"}
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": [row]})
    client = _client(response)
    items = client.overseas.breaking_news(symbol="AAPL", title="Breaking")
    assert isinstance(items[0], NewsHeadline)
    assert items[0].symbols == ("AAPL", "MSFT")
    assert client.transport.calls[0]["tr_id"] == "FHKST01011801"
    assert client.transport.calls[0]["params"]["FID_COND_SCR_DIV_CODE"] == "11801"

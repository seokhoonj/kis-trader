"""해외주식 조건검색."""

from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasStockSearch
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(
        self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
    ):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _response(symbol, *, tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont, body={
        "output1": {"zdiv": "2", "stat": "Y", "crec": "1", "trec": "2", "nrec": "1"},
        "output2": [{"rsym": f"DNAS{symbol}", "excd": "NAS", "name": symbol,
                     "ename": symbol, "symb": symbol, "last": "160.5", "shar": "1000",
                     "valx": "160500", "plow": "159", "phigh": "162", "popen": "160",
                     "tvol": "10000", "rate": "1.25", "diff": "2", "sign": "2",
                     "avol": "1600", "eps": "6.5", "per": "24.7", "rank": "1",
                     "e_ordyn": "O"}],
    })


def test_overseas_search_maps_filters_and_paginates():
    fake = FakeTransport([_response("AAPL", tr_cont="M"), _response("MSFT")])
    client = KISClient(app_key="k", app_secret="s", transport=fake)
    result = client.overseas.search_stocks(
        "NAS", price=(160, 200), per=(10, 30), volume=(1000, 100000)
    )
    assert isinstance(result, OverseasStockSearch)
    assert len(result.matches) == 2
    assert result.matches[0].price == Decimal("160.5")
    assert result.matches[0].is_tradable
    params = fake.calls[0]["params"]
    assert fake.calls[0]["tr_id"] == "HHDFS76410000"
    assert (params["CO_YN_PRICECUR"], params["CO_ST_PRICECUR"], params["CO_EN_PRICECUR"]) == ("1", "160", "200")
    # 모든 필터가 전달되는지(price 만이 아니라 per/volume 도) -- **filters forward 회귀 가드.
    assert (params["CO_YN_PER"], params["CO_ST_PER"], params["CO_EN_PER"]) == ("1", "10", "30")
    assert (params["CO_YN_VOLUME"], params["CO_ST_VOLUME"], params["CO_EN_VOLUME"]) == ("1", "1000", "100000")
    assert params["CO_YN_RATE"] == ""          # 미지정 필터는 빈 값
    assert [call["tr_cont"] for call in fake.calls] == ["", "N"]


def test_overseas_search_rejects_blank_exchange():
    with pytest.raises(KISUsageError):
        KISClient(app_key="k", app_secret="s", transport=FakeTransport([])).overseas.search_stocks("")


def test_overseas_search_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        KISClient(app_key="k", app_secret="s", transport=FakeTransport([response])).overseas.search_stocks("NAS")

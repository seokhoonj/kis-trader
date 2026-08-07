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
    result = client.search_overseas_stocks(
        "NAS", price=(160, 200), per=(10, 30), volume=(1000, 100000)
    )
    assert isinstance(result, OverseasStockSearch)
    assert len(result.items) == 2
    assert result.items[0].price == Decimal("160.5")
    assert result.items[0].is_tradable
    assert fake.calls[0]["tr_id"] == "HHDFS76410000"
    assert fake.calls[0]["params"]["CO_YN_PRICECUR"] == "1"
    assert fake.calls[0]["params"]["CO_ST_PRICECUR"] == "160"
    assert fake.calls[0]["params"]["CO_YN_RATE"] == ""
    assert [call["tr_cont"] for call in fake.calls] == ["", "N"]


def test_overseas_search_rejects_blank_exchange():
    with pytest.raises(KISUsageError):
        KISClient(app_key="k", app_secret="s", transport=FakeTransport([])).search_overseas_stocks("")


def test_overseas_search_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        KISClient(app_key="k", app_secret="s", transport=FakeTransport([response])).search_overseas_stocks("NAS")

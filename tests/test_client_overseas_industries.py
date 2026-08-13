"""해외 거래소 업종 코드 목록 조회의 라우팅·파싱·실패 경계를 검증한다."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasIndustry, OverseasIndustryStock
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/overseas-price/v1/quotations/industry-price"


class FakeTransport:
    def __init__(self, *, responses):
        self.responses = iter(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append(
                {"method": method, "path": path, "tr_id": tr_id, "params": params}
            )
        return next(self.responses)


def _client(transport, *, environment="real"):
    return KISClient(
        app_key="k", app_secret="s", environment=environment, transport=transport
    )


def _response(output2):
    return RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": output2}
    )


def test_overseas_industries_routes_and_parses_single_call():
    fake = FakeTransport(
        responses=[
            _response(
                [
                    {"icod": "000", "name": "전체"},
                    {"icod": "010", "name": "에너지 및 관련 서비스"},
                ]
            )
        ]
    )
    industries = _client(fake).overseas.industries("NAS")

    assert [(item.code, item.name) for item in industries] == [
        ("000", "전체"),
        ("010", "에너지 및 관련 서비스"),
    ]
    assert all(isinstance(item, OverseasIndustry) for item in industries)
    assert fake.calls == [
        {
            "method": "GET",
            "path": _PATH,
            "tr_id": "HHDFS76370100",
            "params": {"AUTH": "", "EXCD": "NAS"},
        }
    ]


def test_overseas_industries_rejects_demo_environment():
    fake = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas.industries("NAS")
    assert fake.calls == []


@pytest.mark.parametrize("output2", [None, {}, "not-an-array"])
def test_overseas_industries_missing_or_non_list_output2_fails_closed(output2):
    body = {} if output2 is None else {"output2": output2}
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(responses=[response])
    with pytest.raises(KISError):
        _client(fake).overseas.industries("NAS")


def test_overseas_industries_non_mapping_item_fails_closed():
    fake = FakeTransport(responses=[_response([{"icod": "000", "name": "전체"}, "bad"])])
    with pytest.raises(KISError):
        _client(fake).overseas.industries("NAS")


def _industry_stock_response(output2):
    return RawResponse(
        rt_cd="0",
        msg_cd="MCA00000",
        msg1="정상",
        body={"output1": {"crec": "1", "trec": "1"}, "output2": output2},
    )


def test_overseas_industry_stocks_maps_quote_and_volume_filter():
    row = {
        "excd": "NAS",
        "symb": "XOM",
        "name": "엑슨 모빌",
        "ename": "Exxon Mobil",
        "last": "112.50",
        "sign": "5",
        "diff": "1.25",
        "rate": "1.10",
        "tvol": "1234567",
        "vask": "300",
        "pask": "112.55",
        "pbid": "112.45",
        "vbid": "250",
        "seqn": "1",
        "e_ordyn": "Y",
    }
    fake = FakeTransport(responses=[_industry_stock_response([row])])
    stocks = _client(fake).overseas.industry_stocks(
        "NAS", "010", min_volume=1_000_000
    )

    assert len(stocks) == 1
    stock = stocks[0]
    assert isinstance(stock, OverseasIndustryStock)
    assert (stock.exchange, stock.symbol, stock.english_name) == (
        "NAS",
        "XOM",
        "Exxon Mobil",
    )
    assert stock.current_price == Decimal("112.50")
    assert stock.change == Decimal("-1.25")
    assert stock.change_percent == Decimal("-1.10")
    assert stock.volume == 1234567
    assert stock.ask_price == Decimal("112.55")
    assert stock.bid_quantity == 250
    assert stock.rank == 1
    assert stock.is_tradable is True
    assert fake.calls[0] == {
        "method": "GET",
        "path": "/uapi/overseas-price/v1/quotations/industry-theme",
        "tr_id": "HHDFS76370000",
        "params": {
            "KEYB": "",
            "AUTH": "",
            "EXCD": "NAS",
            "ICOD": "010",
            "VOL_RANG": "5",
        },
    }


def test_overseas_industry_stocks_validates_environment_and_volume():
    demo = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(demo, environment="demo").overseas.industry_stocks("NAS", "010")
    assert demo.calls == []

    fake = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(fake).overseas.industry_stocks("NAS", "010", min_volume=500)
    assert fake.calls == []


@pytest.mark.parametrize(
    "body",
    [{}, {"output1": {}}, {"output1": {}, "output2": {}}],
)
def test_overseas_industry_stocks_requires_both_blocks(body):
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body=body)
    with pytest.raises(KISError):
        _client(FakeTransport(responses=[response])).overseas.industry_stocks(
            "NAS", "010"
        )

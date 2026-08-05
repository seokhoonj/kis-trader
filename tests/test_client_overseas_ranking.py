"""해외주식 순위 -- kis.overseas_ranking.by_volume(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, OverseasRankingQueries, RankedOverseasStock
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
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}, "output2": rows})


def test_overseas_ranking_accessor():
    assert isinstance(_client(FakeTransport(response=_resp([]))).overseas_ranking,
                      OverseasRankingQueries)


def test_by_volume_maps_and_params():
    rows = [{"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "테슬라", "ename": "TESLA",
             "last": "250.5", "sign": "2", "diff": "5.5", "rate": "2.24", "tvol": "120000000",
             "tamt": "30000000000", "a_tvol": "90000000"}]
    fake = FakeTransport(response=_resp(rows))
    ranked = _client(fake).overseas_ranking.by_volume(exchange="NAS")
    assert isinstance(ranked[0], RankedOverseasStock)
    r = ranked[0]
    assert r.rank == 1
    assert r.exchange == "NAS"
    assert r.symbol == "TSLA"
    assert r.last == Decimal("250.5")
    assert r.change == Decimal("5.5")                    # sign 2 -> 상승
    assert r.volume == 120000000
    assert r._raw["a_tvol"] == "90000000"                # 평균거래량은 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-stock/v1/ranking/trade-vol"
    assert call["tr_id"] == "HHDFS76310010"
    assert call["params"]["EXCD"] == "NAS"


def test_by_volume_down_sign_and_missing_output():
    rows = [{"rank": "2", "excd": "NAS", "symb": "X", "name": "x", "ename": "X", "last": "10",
             "sign": "5", "diff": "1.0", "rate": "9.1", "tvol": "1", "tamt": "10"}]
    fake = FakeTransport(response=_resp(rows))
    assert _client(fake).overseas_ranking.by_volume(exchange="NAS")[0].change == Decimal("-1.0")
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}}))
    with pytest.raises(KISError):
        _client(fake2).overseas_ranking.by_volume(exchange="NAS")


def test_by_volume_bad_value_fails_closed():
    rows = [{"rank": "1", "excd": "NAS", "symb": "X", "name": "x", "ename": "X", "last": "n/a",
             "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).overseas_ranking.by_volume(exchange="NAS")


def _one(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}, "output2": rows})


def test_by_amount_growth_market_cap_route_correctly():
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    cases = [
        ("by_amount", "/uapi/overseas-stock/v1/ranking/trade-pbmn", "HHDFS76320010"),
        ("by_trade_growth", "/uapi/overseas-stock/v1/ranking/trade-growth", "HHDFS76330000"),
        ("by_market_cap", "/uapi/overseas-stock/v1/ranking/market-cap", "HHDFS76350100"),
    ]
    for verb, path, tr in cases:
        fake = FakeTransport(response=_one(row))
        result = getattr(_client(fake).overseas_ranking, verb)(exchange="NAS")
        assert result[0].symbol == "T"
        assert fake.calls[0]["path"] == path
        assert fake.calls[0]["tr_id"] == tr
        assert fake.calls[0]["params"]["EXCD"] == "NAS"


def test_by_change_gubn_and_bad_top():
    import pytest as _pytest

    from kis_openapi.errors import KISUsageError as _U
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas_ranking.by_change(exchange="NAS", top="gainers")
    assert fake.calls[0]["path"] == "/uapi/overseas-stock/v1/ranking/updown-rate"
    assert fake.calls[0]["params"]["GUBN"] == "1"                # gainers=1(상승율)
    fake2 = FakeTransport(response=_one(row))
    _client(fake2).overseas_ranking.by_change(exchange="NAS", top="losers")
    assert fake2.calls[0]["params"]["GUBN"] == "0"               # losers=0(하락율)
    with _pytest.raises(_U):
        _client(fake2).overseas_ranking.by_change(exchange="NAS", top="nope")

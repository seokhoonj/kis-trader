"""외국계 가집계 -- kis.domestic.market.foreign_broker_trades(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import ForeignBrokerFlow, KISClient
from kis_trader.errors import KISError, KISUsageError
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


def test_foreign_broker_maps_ledger_values():
    rows = [{"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "glob_ntsl_qty": "3870530",
             "stck_prpr": "81300", "prdy_vrss": "3700", "prdy_vrss_sign": "2", "prdy_ctrt": "4.77",
             "acml_vol": "24892595", "glob_total_seln_qty": "547879",
             "glob_total_shnu_qty": "4418409"}]
    fake = FakeTransport(response=_resp(rows))
    flows = _client(fake).domestic.market.foreign_broker_trades(sort="amount")
    assert isinstance(flows[0], ForeignBrokerFlow)
    f = flows[0]
    assert f.rank == 1
    assert f.symbol == "005930"
    assert f.estimated_net == 3870530                    # 매수-매도
    assert f.estimated_buy == 4418409
    assert f.estimated_sell == 547879
    assert f.estimated_buy - f.estimated_sell == f.estimated_net
    assert f.change == Decimal(3700)                     # sign 2 -> 상승
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate"
    assert call["tr_id"] == "FHKST644100C0"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # amount


def test_foreign_broker_volume_sort_and_bad_sort():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.market.foreign_broker_trades(sort="volume")
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "1"
    with pytest.raises(KISUsageError):
        _client(fake).domestic.market.foreign_broker_trades(sort="nope")


def test_foreign_broker_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.market.foreign_broker_trades()

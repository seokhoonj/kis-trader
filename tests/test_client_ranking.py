"""시장 전체 순위 -- kis.ranking.by_change / by_volume / by_market_cap.

행위중심 네임스페이스(섹션 미러링 아님), 원장 검증 정렬코드(gainers=0/losers=1), 종목코드 필드
차이 흡수(stck_shrn_iscd/mksc_shrn_iscd), 전일대비 부호 복원, fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KisClient, RankedStock
from kis_openapi.errors import KisError, KisUsageError
from kis_openapi.transport import RawResponse

_FLUCTUATION = "/uapi/domestic-stock/v1/ranking/fluctuation"
_VOLUME = "/uapi/domestic-stock/v1/quotations/volume-rank"
_MARKET_CAP = "/uapi/domestic-stock/v1/ranking/market-cap"


def _row(*, rank="1", symbol_field="mksc_shrn_iscd", symbol="005930", name="삼성전자",
         price="72700", change="400", sign="2", change_percent="0.55", volume="3686661", **extra):
    row = {symbol_field: symbol, "data_rank": rank, "hts_kor_isnm": name, "stck_prpr": price,
           "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": change_percent,
           "acml_vol": volume}
    row.update(extra)
    return row


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KisClient(app_key="k", app_secret="s", transport=transport)


def test_by_change_gainers_uses_rise_sort_code():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd")]))
    ranked = _client(fake).ranking.by_change(top="gainers")
    assert len(ranked) == 1
    first = ranked[0]
    assert isinstance(first, RankedStock)
    assert first.rank == 1
    assert first.symbol == "005930"
    assert first.name == "삼성전자"
    assert first.price == Decimal(72700)
    assert first.change == Decimal(400)
    assert first.change_percent == Decimal("0.55")
    assert first.volume == 3686661
    call = fake.calls[0]
    assert call["path"] == _FLUCTUATION
    assert call["tr_id"] == "FHPST01700000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20170"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # 상승율순


def test_by_change_losers_uses_fall_sort_code():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd", sign="5")]))
    ranked = _client(fake).ranking.by_change(top="losers")
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "1"   # 하락율순
    assert ranked[0].change == Decimal(-400)                     # 하락 -> 음수
    assert ranked[0].change_percent == Decimal("-0.55")


def test_by_change_rejects_bad_top():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ranking.by_change(top="up")


def test_by_volume_uses_volume_endpoint():
    fake = FakeTransport(response=_resp([_row(rank="1"), _row(rank="2", symbol="000660")]))
    ranked = _client(fake).ranking.by_volume()
    assert [r.symbol for r in ranked] == ["005930", "000660"]
    call = fake.calls[0]
    assert call["path"] == _VOLUME
    assert call["tr_id"] == "FHPST01710000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20171"
    assert call["params"]["FID_BLNG_CLS_CODE"] == "0"           # 평균거래량


def test_by_market_cap_exposes_market_cap_in_raw():
    fake = FakeTransport(response=_resp([_row(stck_avls="4340032", mrkt_whol_avls_rlim="15.77")]))
    ranked = _client(fake).ranking.by_market_cap()
    call = fake.calls[0]
    assert call["path"] == _MARKET_CAP
    assert call["tr_id"] == "FHPST01740000"
    assert ranked[0]._raw["stck_avls"] == "4340032"            # 헤드라인 지표는 _raw
    assert ranked[0].price == Decimal(72700)


def test_ranking_skips_empty_rows():
    fake = FakeTransport(response=_resp([_row(), {"data_rank": "", "mksc_shrn_iscd": ""}]))
    assert len(_client(fake).ranking.by_volume()) == 1


def test_ranking_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KisError):
        _client(fake).ranking.by_volume()


def test_ranking_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KisError):
        _client(fake).ranking.by_volume()


def test_ranking_bad_price_fails_closed():
    fake = FakeTransport(response=_resp([_row(price="n/a")]))
    with pytest.raises(KisError):
        _client(fake).ranking.by_volume()

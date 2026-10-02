"""ELW 시장 순위 -- kis.domestic.elw_ranking.*.

5종(거래량/등락률/민감도/투자지표/당일급변) 각각의 TR·URL·시장구분 W·정렬/필터 파라미터,
공통 행(RankedELW) 파싱(1-베이스 순위·부호 복원·지표는 _raw), sort 검증, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import ELWRankingQueries, KISClient, RankedELW
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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _row(code="57JS54", name="한국JS54KOSPI200콜", price="135", vrss="100", sign="2",
         ctrt="42.55", vol="44020240", **extra):
    row = {"elw_shrn_iscd": code, "elw_kor_isnm": name, "elw_prpr": price,
           "prdy_vrss": vrss, "prdy_vrss_sign": sign, "prdy_ctrt": ctrt, "acml_vol": vol}
    row.update(extra)
    return row


def test_elw_ranking_accessor():
    assert isinstance(_client(FakeTransport(response=_resp([]))).domestic.elw_ranking, ELWRankingQueries)


def test_by_volume_maps_rows_and_params():
    fake = FakeTransport(response=_resp([_row(vol_tnrt="440.20"), _row(code="57JS55")]))
    rows = _client(fake).domestic.elw_ranking.by_volume(underlying="005930", right="call")
    assert all(isinstance(r, RankedELW) for r in rows)
    first = rows[0]
    assert first.rank == 1
    assert first.symbol == "57JS54"
    assert first.name == "한국JS54KOSPI200콜"
    assert first.price == Decimal(135)
    assert first.change == Decimal(100)                   # sign 2(상승) -> 양수
    assert first.change_percent == Decimal("42.55")
    assert first.volume == 44020240
    assert first._raw["vol_tnrt"] == "440.20"             # 회전율 등 지표는 _raw
    assert rows[1].rank == 2
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/ranking/volume-rank"
    assert call["tr_id"] == "FHPEW02780000"
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "W"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20278"
    assert call["params"]["FID_UNAS_INPUT_ISCD"] == "005930"       # 기초자산 삼성전자
    assert call["params"]["FID_DIV_CLS_CODE"] == "1"               # 콜
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"         # volume


def test_by_change_sort_and_negative():
    fake = FakeTransport(response=_resp([_row(vrss="50", sign="5", ctrt="18.18")]))
    rows = _client(fake).domestic.elw_ranking.by_change(sort="losers", right="put")
    assert rows[0].change == Decimal(-50)                 # sign 5(하락) -> 음수
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/ranking/updown-rate"
    assert call["tr_id"] == "FHPEW02770000"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"         # losers
    assert call["params"]["FID_DIV_CLS_CODE"] == "2"               # 풋


def test_by_sensitivity_sort_map_and_greeks_in_raw():
    fake = FakeTransport(response=_resp([_row(delta_val="1.000000", gama="0.0", vega="0.0")]))
    rows = _client(fake).domestic.elw_ranking.by_sensitivity(sort="delta")
    assert rows[0]._raw["delta_val"] == "1.000000"        # 그릭스는 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/ranking/sensitivity"
    assert call["tr_id"] == "FHPEW02850000"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"         # delta


def test_by_indicator_sort_map():
    fake = FakeTransport(response=_resp([_row(lvrg_val="35.05")]))
    _client(fake).domestic.elw_ranking.by_indicator(sort="leverage")
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/ranking/indicator"
    assert call["tr_id"] == "FHPEW02790000"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"         # leverage


def test_quick_change_window_and_no_right():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.elw_ranking.by_quick_change(sort="volume_surge", window="minute")
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/ranking/quick-change"
    assert call["tr_id"] == "FHPEW02870000"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "A"
    assert call["params"]["FID_HOUR_CLS_CODE"] == "1"             # minute
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "3"         # volume_surge
    assert "FID_DIV_CLS_CODE" not in call["params"]               # 당일급변은 콜풋 필터 없음


def test_ranking_skips_empty_rows_and_ranks_sequentially():
    fake = FakeTransport(response=_resp([_row(), {"elw_shrn_iscd": ""}, _row(code="57JS55")]))
    rows = _client(fake).domestic.elw_ranking.by_volume()
    assert [r.rank for r in rows] == [1, 2]               # 빈 행 제외, 순위 연속


def test_ranking_rejects_bad_sort():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.elw_ranking.by_volume(sort="nope")


def test_ranking_rejects_bad_right():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.elw_ranking.by_volume(right="both")


def test_ranking_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.elw_ranking.by_volume()


def test_ranking_bad_value_fails_closed():
    fake = FakeTransport(response=_resp([_row(price="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.elw_ranking.by_volume()

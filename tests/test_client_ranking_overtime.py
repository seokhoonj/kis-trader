"""마무리 순위 6종 -- 예상체결/시간외/조회상위.

by_expected_conclusion(RankedStock) / by_overtime_change·volume·expected_change(OvertimeRanking,
output2 vs output) / by_after_hour_balance(전용) / most_viewed(전용). 각 TR·URL·시장구분·정렬,
전용 필드 매핑(시간외 가격/거래량, 잔량, 조회상위 코드+시장), fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    AfterHourBalanceRanking,
    KISClient,
    OvertimeRanking,
    RankedStock,
    TopViewedStock,
)
from kis_openapi.errors import KISError, KISUsageError
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


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _resp(body):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)


# --- exp-trans-updown -> RankedStock (예상체결량=volume) ---------------------
def test_expected_conclusion_maps_and_sort():
    rows = [{"stck_shrn_iscd": "199800", "hts_kor_isnm": "툴젠", "stck_prpr": "76100",
             "prdy_vrss": "17500", "prdy_vrss_sign": "1", "prdy_ctrt": "29.86",
             "cntg_vol": "51683", "antc_tr_pbmn": "3933076300"}]
    fake = FakeTransport(response=_resp({"output": rows}))
    ranked = _client(fake).domestic.ranking.by_expected_conclusion(top="up")
    assert isinstance(ranked[0], RankedStock)
    assert ranked[0].rank == 1
    assert ranked[0].symbol == "199800"
    assert ranked[0].price == Decimal(76100)
    assert ranked[0].change == Decimal(17500)             # sign 1 -> 상승
    assert ranked[0].volume == 51683                      # 예상체결량
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ranking/exp-trans-updown"
    assert call["tr_id"] == "FHPST01820000"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # up
    assert call["params"]["FID_COND_MRKT_DIV_CODE"] == "J"


# --- overtime change / volume -> OvertimeRanking (output2) -------------------
def _ovtm_row(**over):
    row = {"mksc_shrn_iscd": "025950", "hts_kor_isnm": "동신건설", "ovtm_untp_prpr": "21000",
           "ovtm_untp_prdy_vrss": "1890", "ovtm_untp_prdy_vrss_sign": "1",
           "ovtm_untp_prdy_ctrt": "9.89", "ovtm_untp_vol": "46834", "stck_prpr": "19110",
           "acml_vol": "1000"}
    row.update(over)
    return row


def test_overtime_change_maps_overtime_fields():
    fake = FakeTransport(response=_resp({"output1": {}, "output2": [_ovtm_row()]}))
    ranked = _client(fake).domestic.ranking.by_overtime_change(top="down")
    assert isinstance(ranked[0], OvertimeRanking)
    assert ranked[0].symbol == "025950"
    assert ranked[0].overtime_price == Decimal(21000)     # 시간외 가격
    assert ranked[0].overtime_change == Decimal(1890)     # sign 1 -> 상승
    assert ranked[0].overtime_volume == 46834             # 시간외 거래량
    assert ranked[0]._raw["stck_prpr"] == "19110"         # 정규장 가격은 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ranking/overtime-fluctuation"
    assert call["tr_id"] == "FHPST02340000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20234"
    assert call["params"]["FID_DIV_CLS_CODE"] == "3"             # down


def test_overtime_volume_uses_stck_shrn_iscd_fallback():
    row = _ovtm_row(mksc_shrn_iscd=None, stck_shrn_iscd="024840")
    del row["mksc_shrn_iscd"]
    fake = FakeTransport(response=_resp({"output1": {}, "output2": [row]}))
    ranked = _client(fake).domestic.ranking.by_overtime_volume()
    assert ranked[0].symbol == "024840"                   # stck_shrn_iscd 폴백
    assert fake.calls[0]["tr_id"] == "FHPST02350000"
    assert fake.calls[0]["params"]["FID_COND_SCR_DIV_CODE"] == "20235"


def test_overtime_expected_change_uses_output_and_antc_fields():
    row = {"stck_shrn_iscd": "025820", "hts_kor_isnm": "이구산업",
           "ovtm_untp_antc_cnpr": "6270", "ovtm_untp_antc_cntg_vrss": "570",
           "ovtm_untp_antc_cntg_vrss_sign": "1", "ovtm_untp_antc_cntg_ctrt": "10.00",
           "ovtm_untp_antc_cnqn": "253267", "stck_prpr": "5700"}
    fake = FakeTransport(response=_resp({"output": [row]}))
    ranked = _client(fake).domestic.ranking.by_overtime_expected_change(top="up")
    assert ranked[0].overtime_price == Decimal(6270)      # 예상체결가
    assert ranked[0].overtime_change == Decimal(570)
    assert ranked[0].overtime_volume == 253267            # 예상체결량
    assert fake.calls[0]["path"] == "/uapi/domestic-stock/v1/ranking/overtime-exp-trans-fluct"
    assert fake.calls[0]["tr_id"] == "FHKST11860000"


# --- after-hour-balance -> 전용 -------------------------------------------
def test_after_hour_balance_maps_residual_and_volumes():
    rows = [{"stck_shrn_iscd": "252670", "data_rank": "1", "hts_kor_isnm": "KODEX ...",
             "stck_prpr": "2170", "prdy_vrss": "10", "prdy_vrss_sign": "2", "prdy_ctrt": "0.46",
             "ovtm_total_askp_rsqn": "500", "ovtm_total_bidp_rsqn": "700",
             "mkob_otcp_vol": "451685", "mkfa_otcp_vol": "0"}]
    fake = FakeTransport(response=_resp({"output": rows}))
    ranked = _client(fake).domestic.ranking.by_after_hour_balance(top="bid")
    assert isinstance(ranked[0], AfterHourBalanceRanking)
    assert ranked[0].overtime_ask_residual == 500
    assert ranked[0].overtime_bid_residual == 700
    assert ranked[0].pre_market_volume == 451685
    assert ranked[0].post_market_volume == 0
    assert ranked[0].change == Decimal(10)                # sign 2 -> 상승
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ranking/after-hour-balance"
    assert call["tr_id"] == "FHPST01760000"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "2"       # bid


# --- hts-top-view -> 전용 (코드+시장만, 파라미터 없음) ---------------------
def test_most_viewed_maps_symbol_and_market():
    rows = [{"mrkt_div_cls_code": "J", "mksc_shrn_iscd": "005930"},
            {"mrkt_div_cls_code": "Q", "mksc_shrn_iscd": "458650"}]
    fake = FakeTransport(response=_resp({"output1": rows}))
    ranked = _client(fake).domestic.ranking.most_viewed()
    assert all(isinstance(x, TopViewedStock) for x in ranked)
    assert ranked[0].symbol == "005930"
    assert ranked[0].market == "J"
    assert ranked[1].market == "Q"
    assert [x.rank for x in ranked] == [1, 2]
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ranking/hts-top-view"
    assert call["tr_id"] == "HHMCM000100C0"
    assert call["params"] == {}                           # 파라미터 없음


# --- 공통: 검증/fail-closed -------------------------------------------------
def test_expected_conclusion_rejects_bad_top():
    fake = FakeTransport(response=_resp({"output": []}))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_expected_conclusion(top="nope")


def test_expected_close_maps_rows_and_filters():
    rows = [
        {
            "stck_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "stck_prpr": "73000",
            "prdy_vrss": "1200",
            "prdy_vrss_sign": "2",
            "prdy_ctrt": "1.67",
            "sdpr_vrss_prpr": "1500",
            "sdpr_vrss_prpr_rate": "2.10",
            "cntg_vol": "35000",
        }
    ]
    fake = FakeTransport(response=_resp({"output1": rows}))
    ranked = _client(fake).domestic.ranking.by_expected_close(
        filter="upper_limit", market="KOSPI", extended_range=True
    )

    assert ranked[0].rank == 1
    assert ranked[0].symbol == "005930"
    assert ranked[0].price == Decimal(73000)
    assert ranked[0].change == Decimal(1200)
    assert ranked[0].volume == 35000
    assert ranked[0]._raw["sdpr_vrss_prpr"] == "1500"
    assert fake.calls[0] == {
        "path": "/uapi/domestic-stock/v1/quotations/exp-closing-price",
        "tr_id": "FHKST117300C0",
        "params": {
            "FID_RANK_SORT_CLS_CODE": "1",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "11173",
            "FID_INPUT_ISCD": "0001",
            "FID_BLNG_CLS_CODE": "1",
        },
    }


@pytest.mark.parametrize("kwargs", [{"filter": "bad"}, {"market": "NXT"}])
def test_expected_close_rejects_bad_options(kwargs):
    fake = FakeTransport(response=_resp({"output1": []}))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_expected_close(**kwargs)
    assert fake.calls == []


def test_expected_close_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        _client(FakeTransport(response=response)).domestic.ranking.by_expected_close()


def test_overtime_change_missing_output2_fails_closed():
    fake = FakeTransport(response=_resp({"output1": {}}))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_overtime_change()


def test_after_hour_balance_bad_value_fails_closed():
    rows = [{"stck_shrn_iscd": "x", "hts_kor_isnm": "y", "stck_prpr": "1", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0", "ovtm_total_askp_rsqn": "n/a",
             "ovtm_total_bidp_rsqn": "0", "mkob_otcp_vol": "0", "mkfa_otcp_vol": "0"}]
    fake = FakeTransport(response=_resp({"output": rows}))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_after_hour_balance()

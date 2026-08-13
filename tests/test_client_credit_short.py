"""per-ticker 일별 시세분석 -- credit_balance_trend / short_sale_trend."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import (
    CreditBalancePoint,
    ForeignNetBuyPoint,
    KISClient,
    ShortSalePoint,
)
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
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def test_credit_balance_trend_maps():
    rows = [{"deal_date": "20240102", "stck_prpr": "70000", "prdy_vrss": "500",
             "prdy_vrss_sign": "2", "prdy_ctrt": "0.72", "acml_vol": "1000",
             "whol_loan_rmnd_stcn": "50000", "whol_loan_rmnd_amt": "3500000000",
             "whol_loan_rmnd_rate": "0.5", "whol_stln_rmnd_stcn": "2000",
             "whol_stln_rmnd_amt": "140000000", "whol_stln_rmnd_rate": "0.02"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).domestic.stock("005930").credit_balance_trend(as_of="20240102")
    assert isinstance(pts[0], CreditBalancePoint)
    assert pts[0].margin_loan_shares == 50000
    assert pts[0].margin_loan_amount == Decimal(3500000000)
    assert pts[0].stock_loan_shares == 2000
    assert f"{pts[0].timestamp:%Y%m%d}" == "20240102"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
    assert call["tr_id"] == "FHPST04760000"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240102"


def test_short_sale_trend_maps():
    rows = [{"stck_bsop_date": "20240102", "stck_clpr": "70000", "prdy_vrss": "500",
             "prdy_vrss_sign": "5", "prdy_ctrt": "0.72", "acml_vol": "10000",
             "ssts_cntg_qty": "1500", "ssts_vol_rlim": "15.0", "ssts_tr_pbmn": "105000000",
             "avrg_prc": "70050"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).domestic.stock("005930").short_sale_trend(start="20240101", end="20240102")
    assert isinstance(pts[0], ShortSalePoint)
    assert pts[0].short_volume == 1500
    assert pts[0].short_volume_ratio == Decimal("15.0")
    assert pts[0].change == Decimal(-500)                 # sign 5 -> 하락
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-short-sale"
    assert call["tr_id"] == "FHPST04830000"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240102"


def test_credit_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").credit_balance_trend()


def test_short_bad_value_fails_closed():
    rows = [{"stck_bsop_date": "20240102", "stck_clpr": "70000", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0", "acml_vol": "1", "ssts_cntg_qty": "n/a",
             "ssts_tr_pbmn": "0"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").short_sale_trend()


def test_foreign_net_buy_trend_maps_ledger_values_and_preserves_order():
    rows = [
        {
            "bsop_hour": "153106", "stck_prpr": "81300", "prdy_vrss_sign": "2",
            "prdy_ctrt": "1.50", "prdy_vrss": "1200", "acml_vol": "15432100",
            "frgn_seln_vol": "4312000", "frgn_shnu_vol": "8182530",
            "glob_ntby_qty": "3870530", "frgn_ntby_qty_icdc": "194396",
        },
        {
            "bsop_hour": "153006", "stck_prpr": "80100", "prdy_vrss_sign": "5",
            "prdy_ctrt": "0.25", "prdy_vrss": "200", "acml_vol": "15000000",
            "frgn_seln_vol": "4300000", "frgn_shnu_vol": "7976134",
            "glob_ntby_qty": "3676134", "frgn_ntby_qty_icdc": "100",
        },
    ]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).domestic.stock("005930").foreign_net_buy_trend()
    assert isinstance(pts[0], ForeignNetBuyPoint)
    assert pts[0].timestamp.strftime("%H%M%S") == "153106"
    assert pts[0].price == Decimal(81300)
    assert pts[0].change == Decimal(1200)
    assert pts[0].change_percent == Decimal("1.50")
    assert pts[0].foreign_net_buy == 3870530
    assert pts[0].foreign_net_buy_change == 194396
    assert pts[1].timestamp.strftime("%H%M%S") == "153006"
    assert pts[1].change == Decimal(-200)
    assert pts[1].change_percent == Decimal("-0.25")
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend"
    assert call["tr_id"] == "FHKST644400C0"
    assert call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
        "FID_INPUT_ISCD_2": "99999",
    }


def test_foreign_net_buy_trend_non_list_output_fails_closed():
    fake = FakeTransport(response=_resp({"bsop_hour": "153106"}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").foreign_net_buy_trend()


def test_foreign_net_buy_trend_bad_present_numeric_fails_closed():
    rows = [{
        "bsop_hour": "153106", "stck_prpr": "81300", "prdy_vrss_sign": "2",
        "prdy_ctrt": "1.50", "prdy_vrss": "1200", "acml_vol": "15432100",
        "frgn_seln_vol": "4312000", "frgn_shnu_vol": "8182530",
        "glob_ntby_qty": "not-a-number", "frgn_ntby_qty_icdc": "194396",
    }]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").foreign_net_buy_trend()


def test_loan_trend_maps():
    from kis_openapi import LoanPoint
    rows = [{"bsop_date": "20240102", "stck_prpr": "70000", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0", "acml_vol": "1000",
             "new_stcn": "5000", "rdmp_stcn": "2000", "rmnd_stcn": "100000",
             "rmnd_amt": "7000000000", "prdy_rmnd_vrss": "3000"}]
    fake = FakeTransport(response=_resp(rows))
    pts = _client(fake).domestic.stock("005930").loan_trend(start="20240101", end="20240102")
    assert isinstance(pts[0], LoanPoint)
    assert pts[0].new_shares == 5000
    assert pts[0].balance_shares == 100000
    assert pts[0].balance_amount == Decimal(7000000000)
    assert pts[0].balance_change == 3000
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
    assert call["tr_id"] == "HHPST074500C0"
    assert call["params"]["MKSC_SHRN_ISCD"] == "005930"
    assert call["params"]["START_DATE"] == "20240101"


def test_short_sale_default_window_is_lookback_not_single_day():
    # start 미지정이면 end 로부터 30일 전이 되어야 한다(하루로 붕괴하면 _trend 가 무의미).
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.stock("005930").short_sale_trend(end="20240131")
    call = fake.calls[0]
    assert call["params"]["FID_INPUT_DATE_2"] == "20240131"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"      # 31일 - 30일
    assert call["params"]["FID_INPUT_DATE_1"] != call["params"]["FID_INPUT_DATE_2"]


def test_loan_default_window_is_lookback():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.stock("005930").loan_trend(end="20240131")
    call = fake.calls[0]
    assert call["params"]["END_DATE"] == "20240131"
    assert call["params"]["START_DATE"] == "20240101"
    assert call["params"]["MRKT_DIV_CLS_CODE"] == "1"


def test_daily_trade_volume_maps_output2():
    # 원장 응답 예시값(output2, 20240126). output1(구간합계)은 무시.
    body = {"output1": {"shnu_cnqn_smtn": "4520816", "seln_cnqn_smtn": "5285722"},
            "output2": [{"stck_bsop_date": "20240126", "total_seln_qty": "5285722",
                         "total_shnu_qty": "4520816"},
                        {"stck_bsop_date": "20240125", "total_seln_qty": "5610781",
                         "total_shnu_qty": "4008095"}]}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(response=resp)
    from kis_openapi import DailyTradeVolumePoint
    pts = _client(fake).domestic.stock("005930").daily_trade_volume(start="20240120", end="20240126")
    assert isinstance(pts[0], DailyTradeVolumePoint)
    assert pts[0].buy_volume == 4520816
    assert pts[0].sell_volume == 5285722
    assert pts[1].timestamp.strftime("%Y%m%d") == "20240125"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-daily-trade-volume"
    assert call["tr_id"] == "FHKST03010800"
    assert call["params"]["FID_PERIOD_DIV_CODE"] == "D"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240120"


def test_daily_trade_volume_missing_output2_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}})
    fake = FakeTransport(response=resp)
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").daily_trade_volume(end="20240126")


def test_trade_amount_bands_maps():
    # 원장 응답 예시값(005930, 3백/5백 이하). 순매수 비율·건수 음수 보존.
    rows = [{"prpr_name": "3백 이하", "smtn_avrg_prpr": "78315", "acml_vol": "291426",
             "whol_ntby_qty_rate": "0.37", "ntby_cntg_csnu": "13297",
             "seln_cnqn_smtn": "126451", "whol_seln_vol_rate": "1.21", "seln_cntg_csnu": "16084",
             "shnu_cnqn_smtn": "164975", "whol_shun_vol_rate": "1.58", "shnu_cntg_csnu": "29381"},
            {"prpr_name": "5백 이하", "smtn_avrg_prpr": "78317", "acml_vol": "138138",
             "whol_ntby_qty_rate": "-0.13", "ntby_cntg_csnu": "-278",
             "seln_cnqn_smtn": "75634", "whol_seln_vol_rate": "0.73", "seln_cntg_csnu": "1525",
             "shnu_cnqn_smtn": "62504", "whol_shun_vol_rate": "0.60", "shnu_cntg_csnu": "1247"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_openapi import TradeAmountBand
    bands = _client(fake).domestic.stock("005930").trade_amount_bands()
    assert isinstance(bands[0], TradeAmountBand)
    assert bands[0].band_label == "3백 이하"
    assert bands[0].average_price == Decimal(78315)
    assert bands[0].buy_count == 29381
    # 두 번째 밴드: 순매수 음수 보존(pre-signed, apply_change_sign 안 탐)
    assert bands[1].net_buy_ratio == Decimal("-0.13")
    assert bands[1].net_buy_count == -278
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/tradprt-byamt"
    assert call["tr_id"] == "FHKST111900C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11119"


def test_expected_price_trend_maps_output2():
    # 원장 응답 예시값(output2, 20240318 090023). output1(스냅샷)은 무시.
    body = {"output1": {"antc_cnpr": "72600"},
            "output2": [{"stck_bsop_date": "20240318", "stck_cntg_hour": "090023",
                         "stck_prpr": "72600", "prdy_vrss_sign": "2", "prdy_vrss": "300",
                         "prdy_ctrt": "0.41", "acml_vol": "420303"}]}
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(response=resp)
    from kis_openapi import ExpectedPricePoint
    pts = _client(fake).domestic.stock("005930").expected_price_trend(exclude_zero_volume=True)
    assert isinstance(pts[0], ExpectedPricePoint)
    assert pts[0].expected_price == Decimal(72600)
    assert pts[0].change == Decimal(300)                 # sign 2 -> 양수
    assert pts[0].change_percent == Decimal("0.41")
    assert pts[0].timestamp.strftime("%Y%m%d%H%M%S") == "20240318090023"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/exp-price-trend"
    assert call["tr_id"] == "FHPST01810000"
    assert call["params"]["fid_mkop_cls_code"] == "4"    # exclude_zero_volume

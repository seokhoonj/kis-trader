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
_DISPARITY = "/uapi/domestic-stock/v1/ranking/disparity"
_QUOTE_BALANCE = "/uapi/domestic-stock/v1/ranking/quote-balance"
_VOLUME_POWER = "/uapi/domestic-stock/v1/ranking/volume-power"
_BULK_TRADES = "/uapi/domestic-stock/v1/ranking/bulk-trans-num"
_INTEREST = "/uapi/domestic-stock/v1/ranking/top-interest-stock"
_PREFERRED_DISPARITY = "/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio"
_FINANCE_RATIO = "/uapi/domestic-stock/v1/ranking/finance-ratio"
_VALUATION = "/uapi/domestic-stock/v1/ranking/market-value"
_PROFIT_ASSET = "/uapi/domestic-stock/v1/ranking/profit-asset-index"
_COMPANY_TRADES = "/uapi/domestic-stock/v1/ranking/traded-by-company"


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


def test_by_disparity_defaults_to_highest_and_20day():
    fake = FakeTransport(response=_resp([_row(d20_dsrt="103.42")]))
    ranked = _client(fake).ranking.by_disparity()
    call = fake.calls[0]
    assert call["path"] == _DISPARITY
    assert call["tr_id"] == "FHPST01780000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20178"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # highest
    assert call["params"]["FID_HOUR_CLS_CODE"] == "20"           # 기본 20일
    assert ranked[0]._raw["d20_dsrt"] == "103.42"                # 이격도는 _raw


def test_by_disparity_lowest_and_period():
    fake = FakeTransport(response=_resp([_row(d5_dsrt="97.1")]))
    _client(fake).ranking.by_disparity(top="lowest", period=5)
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"       # lowest
    assert call["params"]["FID_HOUR_CLS_CODE"] == "5"


def test_by_disparity_rejects_bad_period():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ranking.by_disparity(period=7)


def test_by_disparity_rejects_bad_top():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ranking.by_disparity(top="above")


def test_by_quote_balance_defaults_to_net_buy():
    fake = FakeTransport(response=_resp([_row(total_bidp_rsqn="12345")]))
    ranked = _client(fake).ranking.by_quote_balance()
    call = fake.calls[0]
    assert call["path"] == _QUOTE_BALANCE
    assert call["tr_id"] == "FHPST01720000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20172"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # net_buy
    assert ranked[0]._raw["total_bidp_rsqn"] == "12345"


def test_by_quote_balance_sort_variants():
    for top, code in [("net_sell", "1"), ("buy_ratio", "2"), ("sell_ratio", "3")]:
        fake = FakeTransport(response=_resp([_row()]))
        _client(fake).ranking.by_quote_balance(top=top)
        assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == code


def test_by_quote_balance_rejects_bad_top():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KisUsageError):
        _client(fake).ranking.by_quote_balance(top="bogus")


def test_by_volume_power_uses_endpoint_without_sort():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd", tday_rltv="128.5")]))
    ranked = _client(fake).ranking.by_volume_power()
    call = fake.calls[0]
    assert call["path"] == _VOLUME_POWER
    assert call["tr_id"] == "FHPST01680000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20168"
    assert "FID_RANK_SORT_CLS_CODE" not in call["params"]        # 정렬 축 없음
    assert ranked[0]._raw["tday_rltv"] == "128.5"


def test_by_bulk_trades_defaults_to_buy():
    fake = FakeTransport(response=_resp([_row(shnu_cntg_csnu="42")]))
    ranked = _client(fake).ranking.by_bulk_trades()
    call = fake.calls[0]
    assert call["path"] == _BULK_TRADES
    assert call["tr_id"] == "FHKST190900C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11909"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # buy
    assert ranked[0]._raw["shnu_cntg_csnu"] == "42"


def test_by_bulk_trades_sell_and_bad_top():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).ranking.by_bulk_trades(top="sell")
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "1"   # sell
    with pytest.raises(KisUsageError):
        _client(FakeTransport(response=_resp([]))).ranking.by_bulk_trades(top="both")


def test_by_interest_uses_endpoint_without_sort():
    fake = FakeTransport(response=_resp([_row(inter_issu_reg_csnu="1523")]))
    ranked = _client(fake).ranking.by_interest()
    call = fake.calls[0]
    assert call["path"] == _INTEREST
    assert call["tr_id"] == "FHPST01800000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20180"
    assert "FID_RANK_SORT_CLS_CODE" not in call["params"]
    assert ranked[0]._raw["inter_issu_reg_csnu"] == "1523"


def test_by_preferred_disparity_exposes_pair_in_raw():
    fake = FakeTransport(response=_resp([_row(prst_prpr="61000", dprt="12.34")]))
    ranked = _client(fake).ranking.by_preferred_disparity()
    call = fake.calls[0]
    assert call["path"] == _PREFERRED_DISPARITY
    assert call["tr_id"] == "FHPST01770000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20177"
    assert ranked[0].price == Decimal(72700)                     # 본주 현재가
    assert ranked[0]._raw["dprt"] == "12.34"                     # 괴리율은 _raw


def test_by_finance_ratio_sort_year_quarter():
    fake = FakeTransport(response=_resp([_row(cptl_op_prfi="12.3")]))
    ranked = _client(fake).ranking.by_finance_ratio(analysis="stability", year=2023, quarter="h1")
    call = fake.calls[0]
    assert call["path"] == _FINANCE_RATIO
    assert call["tr_id"] == "FHPST01750000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20175"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "11"      # stability
    assert call["params"]["FID_INPUT_OPTION_1"] == "2023"        # 회계연도
    assert call["params"]["FID_INPUT_OPTION_2"] == "1"           # h1 = 반기
    assert ranked[0]._raw["cptl_op_prfi"] == "12.3"


def test_by_finance_ratio_defaults_and_bad_analysis():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).ranking.by_finance_ratio(year=2024)
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "7"       # profitability 기본
    assert call["params"]["FID_INPUT_OPTION_2"] == "3"           # annual 기본
    with pytest.raises(KisUsageError):
        _client(FakeTransport(response=_resp([]))).ranking.by_finance_ratio(
            analysis="liquidity", year=2024
        )


def test_by_valuation_metric_maps_to_code():
    for metric, code in [("per", "23"), ("pbr", "24"), ("ev_ebitda", "30"), ("ebitda_ratio", "31")]:
        fake = FakeTransport(response=_resp([_row(per="8.1")]))
        _client(fake).ranking.by_valuation(metric=metric, year=2023)
        call = fake.calls[0]
        assert call["path"] == _VALUATION
        assert call["tr_id"] == "FHPST01790000"
        assert call["params"]["FID_RANK_SORT_CLS_CODE"] == code


def test_by_valuation_bad_quarter():
    with pytest.raises(KisUsageError):
        _client(FakeTransport(response=_resp([]))).ranking.by_valuation(year=2023, quarter="q2")


def test_by_profit_asset_metric_and_defaults():
    fake = FakeTransport(response=_resp([_row(total_aset="4200000")]))
    ranked = _client(fake).ranking.by_profit_asset(metric="total_assets", year=2023)
    call = fake.calls[0]
    assert call["path"] == _PROFIT_ASSET
    assert call["tr_id"] == "FHPST01730000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20173"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "4"       # total_assets
    assert ranked[0]._raw["total_aset"] == "4200000"


def test_by_profit_asset_default_metric_is_net_income():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).ranking.by_profit_asset(year=2023)
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "3"   # net_income


def test_by_company_trades_buy_with_date_range():
    fake = FakeTransport(response=_resp([_row(ntby_cnqn="9800")]))
    ranked = _client(fake).ranking.by_company_trades(
        top="buy", start="20240314", end="20240315"
    )
    call = fake.calls[0]
    assert call["path"] == _COMPANY_TRADES
    assert call["tr_id"] == "FHPST01860000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20186"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"       # buy = 매수상위
    assert call["params"]["FID_INPUT_DATE_1"] == "20240314"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240315"
    assert ranked[0]._raw["ntby_cnqn"] == "9800"


def test_by_company_trades_accepts_date_objects_and_sell():
    from datetime import date
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).ranking.by_company_trades(
        top="sell", start=date(2024, 3, 14), end=date(2024, 3, 15)
    )
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # sell = 매도상위
    assert call["params"]["FID_INPUT_DATE_1"] == "20240314"


def test_by_company_trades_bad_top():
    with pytest.raises(KisUsageError):
        _client(FakeTransport(response=_resp([]))).ranking.by_company_trades(
            top="net", start="20240314", end="20240315"
        )


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

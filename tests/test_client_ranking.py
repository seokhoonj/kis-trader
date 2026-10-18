"""시장 전체 순위 -- kis.domestic.ranking.by_change / by_volume / by_market_cap.

행위중심 네임스페이스(섹션 미러링 아님), 원장 검증 정렬코드(gainers=0/losers=1), 종목코드 필드
차이 흡수(stck_shrn_iscd/mksc_shrn_iscd), 전일대비 부호 복원, fail-closed 파싱을 가짜 전송으로 검증.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import (
    BulkTradeCountRanking,
    DisparityRanking,
    DomesticRankedStock,
    ExecutionStrengthRanking,
    HouseTradeVolumeRanking,
    KISClient,
    MarketCapRanking,
    PreferredShareDisparityRanking,
    QuoteBalanceRanking,
    VolumeRanking,
    WatchlistRegistrationRanking,
)
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

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


#: 전용 순위 엔티티(거래활동/체결강도/관심등록/대량체결/당사매매)가 요구하는 지표 필드 -- 라이브
#: 응답은 이 키들을 모두 포함하므로 기본 행에 realistic 값을 심어, 코어만 검증하는 테스트도 유효한
#: 전용 엔티티를 구성하도록 한다(개별 테스트는 **extra 로 덮어써 값 어서션).
_CLUSTER_METRICS = {
    "avrg_vol": "2500000", "vol_inrt": "152.3", "vol_tnrt": "4.21", "nday_vol_tnrt": "8.44",
    "avrg_tr_pbmn": "180000000000", "tr_pbmn_tnrt": "3.15", "nday_tr_pbmn_tnrt": "6.30",
    "tday_rltv": "128.5", "shnu_cnqn_smtn": "1850000", "seln_cnqn_smtn": "1620000",
    "inter_issu_reg_csnu": "1523", "shnu_cntg_csnu": "42", "seln_cntg_csnu": "37",
    "ntby_cnqn": "9800",
    # 시장구조 클러스터(시총/이격도/호가잔량) 전용 지표 -- 라이브 응답에 모두 포함.
    "stck_avls": "4340032", "lstn_stcn": "5969782550", "mrkt_whol_avls_rlim": "15.77",
    "d5_dsrt": "101.20", "d10_dsrt": "102.50", "d20_dsrt": "103.42",
    "d60_dsrt": "98.70", "d120_dsrt": "105.10",
    "total_askp_rsqn": "54321", "total_bidp_rsqn": "12345",
    "seln_rsqn_rate": "81.50", "shnu_rsqn_rate": "18.50", "total_ntsl_bidp_rsqn": "41976",
    # 우선주 괴리 클러스터 전용 지표(본주/우선주 짝) -- 라이브 응답에 모두 포함.
    "dprt": "12.34", "diff_prpr": "11700", "prst_iscd": "005935", "prst_kor_isnm": "삼성전자우",
    "prst_prpr": "61000", "prst_prdy_vrss": "300", "prst_prdy_vrss_sign": "5",
    "prst_prdy_ctrt": "0.49", "prst_acml_vol": "412300",
}


def _row(*, rank="1", symbol_field="mksc_shrn_iscd", symbol="005930", name="삼성전자",
         price="72700", change="400", sign="2", change_percent="0.55", volume="3686661", **extra):
    row = {symbol_field: symbol, "data_rank": rank, "hts_kor_isnm": name, "stck_prpr": price,
           "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": change_percent,
           "acml_vol": volume, **_CLUSTER_METRICS}
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
    return KISClient(app_key="k", app_secret="s", environment="real", transport=transport)


def test_by_change_gainers_uses_rise_sort_code():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd")]))
    ranked = _client(fake).domestic.ranking.by_change(direction="gainers")
    assert len(ranked) == 1
    first = ranked[0]
    assert isinstance(first, DomesticRankedStock)
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
    # 대비 기준은 전일대비("1") -- "0"(저가대비)이면 정렬이 표시 등락률과 어긋난다(실 API 검증).
    assert call["params"]["FID_PRC_CLS_CODE"] == "1"


def test_by_change_losers_uses_fall_sort_code():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd", sign="5")]))
    ranked = _client(fake).domestic.ranking.by_change(direction="losers")
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "1"   # 하락율순
    assert ranked[0].change == Decimal(-400)                     # 하락 -> 음수
    assert ranked[0].change_percent == Decimal("-0.55")


def test_by_change_rejects_bad_direction():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_change(direction="up")


def test_ranking_non_mapping_row_fails_closed():
    # output 배열에 매핑이 아닌 행(None 등)이 있으면 _parse_ranked 의 row.get 에서 AttributeError 가
    # 새지 않고 도메인 예외(KISError)로 fail-closed 한다(형제 _fetch_rows 와 같은 엄정함).
    fake = FakeTransport(response=_resp([None]))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_change(direction="gainers")


def test_by_volume_uses_volume_endpoint():
    fake = FakeTransport(response=_resp([_row(rank="1"), _row(rank="2", symbol="000660")]))
    ranked = _client(fake).domestic.ranking.by_volume()
    assert [r.symbol for r in ranked] == ["005930", "000660"]
    assert isinstance(ranked[0], VolumeRanking)
    call = fake.calls[0]
    assert call["path"] == _VOLUME
    assert call["tr_id"] == "FHPST01710000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20171"
    assert call["params"]["FID_BLNG_CLS_CODE"] == "3"           # 기본 = cumulative_trading_amount(거래대금)


def test_by_volume_types_activity_metrics():
    fake = FakeTransport(response=_resp([_row(
        avrg_vol="2500000", vol_inrt="152.3", vol_tnrt="4.21", nday_vol_tnrt="8.44",
        avrg_tr_pbmn="180000000000", tr_pbmn_tnrt="3.15", nday_tr_pbmn_tnrt="6.30",
    )]))
    first = _client(fake).domestic.ranking.by_volume(metric="average_volume")[0]
    assert first.average_volume == 2500000
    assert first.volume_growth_percent == Decimal("152.3")
    assert first.volume_turnover_ratio == Decimal("4.21")
    assert first.n_day_volume_turnover_ratio == Decimal("8.44")
    assert first.average_trading_amount == Decimal(180000000000)
    assert first.trading_amount_turnover_ratio == Decimal("3.15")
    assert first.n_day_trading_amount_turnover_ratio == Decimal("6.30")


@pytest.mark.parametrize("metric, blng", [
    ("average_volume", "0"), ("volume_growth", "1"), ("turnover", "2"), ("cumulative_trading_amount", "3"),
])
def test_by_volume_metric_maps_to_blng_code(metric, blng):
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.ranking.by_volume(metric=metric)
    assert fake.calls[0]["params"]["FID_BLNG_CLS_CODE"] == blng


def test_by_volume_reads_trading_value_from_acml_tr_pbmn():
    fake = FakeTransport(response=_resp([_row(acml_tr_pbmn="7489699281000")]))
    ranked = _client(fake).domestic.ranking.by_volume(metric="cumulative_trading_amount")
    assert ranked[0].cumulative_trading_amount == Decimal(7489699281000)


def test_by_volume_trading_value_is_none_when_absent():
    fake = FakeTransport(response=_resp([_row()]))  # acml_tr_pbmn 없음
    assert _client(fake).domestic.ranking.by_volume()[0].cumulative_trading_amount is None


def test_by_volume_period_metrics_none_for_newly_listed():
    # 신규/단기 상장주는 평균·N일·증가율 지표가 비어 온다 -- 한 행만 비어도 required 였을 땐
    # by_volume 전체가 KISError 로 터졌다. optional 이라 그 행은 None 으로 담기고 순위는 성립.
    fake = FakeTransport(response=_resp([_row(
        avrg_vol="", vol_inrt="", nday_vol_tnrt="", avrg_tr_pbmn="", nday_tr_pbmn_tnrt="",
    )]))
    first = _client(fake).domestic.ranking.by_volume()[0]
    assert first.average_volume is None
    assert first.volume_growth_percent is None
    assert first.n_day_volume_turnover_ratio is None
    assert first.average_trading_amount is None
    assert first.n_day_trading_amount_turnover_ratio is None
    # 당일 산출 회전율은 비어 오지 않으므로 required 로 남아 그대로 채워진다.
    assert first.volume_turnover_ratio == Decimal("4.21")
    assert first.trading_amount_turnover_ratio == Decimal("3.15")


def test_by_volume_rejects_bad_metric():
    fake = FakeTransport(response=_resp([_row()]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_volume(metric="bogus")  # type: ignore[arg-type]


def test_by_market_cap_types_market_cap_metrics():
    fake = FakeTransport(response=_resp([_row(
        stck_avls="4340032", lstn_stcn="5969782550", mrkt_whol_avls_rlim="15.77",
    )]))
    ranked = _client(fake).domestic.ranking.by_market_cap()
    call = fake.calls[0]
    assert call["path"] == _MARKET_CAP
    assert call["tr_id"] == "FHPST01740000"
    assert isinstance(ranked[0], MarketCapRanking)
    assert ranked[0].price == Decimal(72700)
    assert ranked[0].market_cap == Decimal(4340032)            # 시가총액(억원)
    assert ranked[0].listed_shares == 5969782550               # 상장주식수
    assert ranked[0].market_cap_weight_ratio == Decimal("15.77")


def test_by_disparity_defaults_to_highest_and_20day():
    fake = FakeTransport(response=_resp([_row(
        d5_dsrt="101.20", d10_dsrt="102.50", d20_dsrt="103.42", d60_dsrt="98.70", d120_dsrt="105.10",
    )]))
    ranked = _client(fake).domestic.ranking.by_disparity()
    call = fake.calls[0]
    assert call["path"] == _DISPARITY
    assert call["tr_id"] == "FHPST01780000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20178"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # highest
    assert call["params"]["FID_HOUR_CLS_CODE"] == "20"           # 기본 20일
    assert isinstance(ranked[0], DisparityRanking)
    # 정렬 기준이 20일이어도 5개 이격도를 모두 담는다.
    assert ranked[0].disparity_5 == Decimal("101.20")
    assert ranked[0].disparity_10 == Decimal("102.50")
    assert ranked[0].disparity_20 == Decimal("103.42")
    assert ranked[0].disparity_60 == Decimal("98.70")
    assert ranked[0].disparity_120 == Decimal("105.10")


def test_by_disparity_lowest_and_period():
    fake = FakeTransport(response=_resp([_row(d5_dsrt="97.1")]))
    _client(fake).domestic.ranking.by_disparity(extreme="lowest", period=5)
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"       # lowest
    assert call["params"]["FID_HOUR_CLS_CODE"] == "5"


def test_by_disparity_short_listed_periods_none():
    # 상장 일수가 N일 미만이면 그 기간 이격도는 미정의라 비어 온다(예: 상장 30일 -> d60/d120 공백).
    # required 였을 땐 그런 종목이 순위에 들면 KISError 로 전체가 터졌다.
    fake = FakeTransport(response=_resp([_row(d60_dsrt="", d120_dsrt="")]))
    first = _client(fake).domestic.ranking.by_disparity()[0]
    assert first.disparity_5 == Decimal("101.20")
    assert first.disparity_60 is None
    assert first.disparity_120 is None


def test_by_disparity_rejects_bad_extreme():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_disparity(extreme="above")


def test_by_quote_balance_defaults_to_net_buy():
    fake = FakeTransport(response=_resp([_row(
        total_askp_rsqn="54321", total_bidp_rsqn="12345",
        seln_rsqn_rate="81.50", shnu_rsqn_rate="18.50", total_ntsl_bidp_rsqn="41976",
    )]))
    ranked = _client(fake).domestic.ranking.by_quote_balance()
    call = fake.calls[0]
    assert call["path"] == _QUOTE_BALANCE
    assert call["tr_id"] == "FHPST01720000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20172"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # net_buy
    assert isinstance(ranked[0], QuoteBalanceRanking)
    assert ranked[0].total_ask_residual == 54321
    assert ranked[0].total_bid_residual == 12345
    assert ranked[0].sell_residual_ratio == Decimal("81.50")
    assert ranked[0].buy_residual_ratio == Decimal("18.50")
    # 의미 불확실한 total_ntsl_bidp_rsqn 은 명명하지 않고 _raw 에 남겨둔다.
    assert ranked[0]._raw["total_ntsl_bidp_rsqn"] == "41976"


def test_by_quote_balance_sort_variants():
    for metric, code in [("net_sell", "1"), ("buy_ratio", "2"), ("sell_ratio", "3")]:
        fake = FakeTransport(response=_resp([_row()]))
        _client(fake).domestic.ranking.by_quote_balance(metric=metric)
        assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == code


def test_by_quote_balance_rejects_bad_metric():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_quote_balance(metric="bogus")


def test_by_execution_strength_uses_endpoint_without_sort():
    fake = FakeTransport(response=_resp([_row(symbol_field="stck_shrn_iscd", tday_rltv="128.5")]))
    ranked = _client(fake).domestic.ranking.by_execution_strength()
    call = fake.calls[0]
    assert call["path"] == _VOLUME_POWER
    assert call["tr_id"] == "FHPST01680000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20168"
    assert "FID_RANK_SORT_CLS_CODE" not in call["params"]        # 정렬 축 없음
    assert isinstance(ranked[0], ExecutionStrengthRanking)
    assert ranked[0].execution_strength == Decimal("128.5")
    assert ranked[0].buy_execution_volume == 1850000
    assert ranked[0].sell_execution_volume == 1620000


def test_by_bulk_trade_count_defaults_to_buy():
    fake = FakeTransport(response=_resp([_row(shnu_cntg_csnu="42")]))
    ranked = _client(fake).domestic.ranking.by_bulk_trade_count()
    call = fake.calls[0]
    assert call["path"] == _BULK_TRADES
    assert call["tr_id"] == "FHKST190900C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11909"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # buy
    assert isinstance(ranked[0], BulkTradeCountRanking)
    assert ranked[0].buy_trade_count == 42
    assert ranked[0].sell_trade_count == 37
    # ntby_cnqn 의 cnqn(체결량)은 건수(csnu)가 아니라 수량 -> net_trade_volume(_volume) 로 노출.
    assert ranked[0].net_trade_volume == 9800


def test_by_bulk_trade_count_sell_and_bad_side():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.ranking.by_bulk_trade_count(side="sell")
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "1"   # sell
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_bulk_trade_count(side="both")


def test_by_interest_uses_endpoint_without_sort():
    fake = FakeTransport(response=_resp([_row(inter_issu_reg_csnu="1523")]))
    ranked = _client(fake).domestic.ranking.by_watchlist_registrations()
    call = fake.calls[0]
    assert call["path"] == _INTEREST
    assert call["tr_id"] == "FHPST01800000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20180"
    assert "FID_RANK_SORT_CLS_CODE" not in call["params"]
    assert isinstance(ranked[0], WatchlistRegistrationRanking)
    assert ranked[0].registration_count == 1523
    assert ranked[0].cumulative_trading_amount is None           # acml_tr_pbmn 없음 -> None


def test_by_preferred_share_disparity_types_main_and_preferred_legs():
    fake = FakeTransport(response=_resp([_row()]))
    ranked = _client(fake).domestic.ranking.by_preferred_share_disparity()
    call = fake.calls[0]
    assert call["path"] == _PREFERRED_DISPARITY
    assert call["tr_id"] == "FHPST01770000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20177"
    assert isinstance(ranked[0], PreferredShareDisparityRanking)
    # 코어는 본주 기준
    assert ranked[0].symbol == "005930"
    assert ranked[0].price == Decimal(72700)                     # 본주 현재가
    assert ranked[0].change == Decimal(400)                      # 본주 sign=2 -> 상승(+)
    # 괴리/가격차/우선주 레그
    assert ranked[0].disparity_rate == Decimal("12.34")
    assert ranked[0].price_difference == Decimal(11700)
    assert ranked[0].preferred_symbol == "005935"
    assert ranked[0].preferred_name == "삼성전자우"
    assert ranked[0].preferred_price == Decimal(61000)
    assert ranked[0].preferred_change == Decimal(-300)           # prst sign=5 -> 하락(-)
    assert ranked[0].preferred_change_percent == Decimal("-0.49")
    assert ranked[0].preferred_volume == 412300


def test_by_financial_ratio_sort_year_quarter():
    fake = FakeTransport(response=_resp([_row(cptl_op_prfi="12.3")]))
    ranked = _client(fake).domestic.ranking.by_financial_ratio(analysis="stability", year=2023, quarter="h1")
    call = fake.calls[0]
    assert call["path"] == _FINANCE_RATIO
    assert call["tr_id"] == "FHPST01750000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20175"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "11"      # stability
    assert call["params"]["FID_INPUT_OPTION_1"] == "2023"        # 회계연도
    assert call["params"]["FID_INPUT_OPTION_2"] == "1"           # h1 = 반기
    assert ranked[0]._raw["cptl_op_prfi"] == "12.3"


def test_by_financial_ratio_defaults_and_bad_analysis():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.ranking.by_financial_ratio(year=2024)
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "7"       # profitability 기본
    assert call["params"]["FID_INPUT_OPTION_2"] == "3"           # annual 기본
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_financial_ratio(
            analysis="liquidity", year=2024
        )


def test_by_valuation_metric_maps_to_code():
    for metric, code in [("per", "23"), ("pbr", "24"), ("ev_ebitda", "30"), ("ebitda_ratio", "31")]:
        fake = FakeTransport(response=_resp([_row(per="8.1")]))
        _client(fake).domestic.ranking.by_valuation(metric=metric, year=2023)
        call = fake.calls[0]
        assert call["path"] == _VALUATION
        assert call["tr_id"] == "FHPST01790000"
        assert call["params"]["FID_RANK_SORT_CLS_CODE"] == code


def test_by_valuation_bad_quarter():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_valuation(year=2023, quarter="q2")


def test_by_profit_asset_metric_and_defaults():
    fake = FakeTransport(response=_resp([_row(total_aset="4200000")]))
    ranked = _client(fake).domestic.ranking.by_profit_asset(metric="total_assets", year=2023)
    call = fake.calls[0]
    assert call["path"] == _PROFIT_ASSET
    assert call["tr_id"] == "FHPST01730000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20173"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "4"       # total_assets
    assert ranked[0]._raw["total_aset"] == "4200000"


def test_by_profit_asset_default_metric_is_net_income():
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.ranking.by_profit_asset(year=2023)
    assert fake.calls[0]["params"]["FID_RANK_SORT_CLS_CODE"] == "3"   # net_income


def test_by_house_trade_volume_buy_with_date_range():
    fake = FakeTransport(response=_resp([_row(
        shnu_cnqn_smtn="55000", seln_cnqn_smtn="41000", ntby_cnqn="9800",
        acml_tr_pbmn="7489699281000",
    )]))
    ranked = _client(fake).domestic.ranking.by_house_trade_volume(
        side="buy", start="20240314", end="20240315"
    )
    call = fake.calls[0]
    assert call["path"] == _COMPANY_TRADES
    assert call["tr_id"] == "FHPST01860000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20186"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "1"       # buy = 매수상위
    assert call["params"]["FID_INPUT_DATE_1"] == "20240314"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240315"
    assert isinstance(ranked[0], HouseTradeVolumeRanking)
    assert ranked[0].buy_volume == 55000
    assert ranked[0].sell_volume == 41000
    assert ranked[0].net_volume == 9800
    assert ranked[0].cumulative_trading_amount == Decimal(7489699281000)


def test_by_house_trade_volume_accepts_date_objects_and_sell():
    from datetime import date
    fake = FakeTransport(response=_resp([_row()]))
    _client(fake).domestic.ranking.by_house_trade_volume(
        side="sell", start=date(2024, 3, 14), end=date(2024, 3, 15)
    )
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # sell = 매도상위
    assert call["params"]["FID_INPUT_DATE_1"] == "20240314"


def test_by_house_trade_volume_bad_side():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_house_trade_volume(
            side="net", start="20240314", end="20240315"
        )


def test_by_house_trade_volume_rejects_inverted_range_before_transport():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_house_trade_volume(
            side="buy", start="20240315", end="20240314"
        )
    assert fake.calls == []                                       # I/O 전 거부


_DIVIDEND = "/uapi/domestic-stock/v1/ranking/dividend-rate"


def _dividend_row(rank="1", sht_cd="089600", name="나스미디어", record_date="20240403",
                  amount="750", rate="5.23", kind="현금"):
    return {"rank": rank, "sht_cd": sht_cd, "isin_name": name, "record_date": record_date,
            "per_sto_divi_amt": amount, "divi_rate": rate, "divi_kind": kind}


def test_by_dividend_maps_fields_and_params():
    from datetime import date as _date

    from kis_trader import DividendRanking
    fake = FakeTransport(response=_resp([_dividend_row()]))
    ranked = _client(fake).domestic.ranking.by_dividend(kind="cash", start="20200101", end="20240403")
    assert len(ranked) == 1
    item = ranked[0]
    assert isinstance(item, DividendRanking)
    assert item.rank == 1
    assert item.symbol == "089600"
    assert item.name == "나스미디어"
    assert item.record_date == _date(2024, 4, 3)
    assert item.dividend_per_share == Decimal(750)
    assert item.dividend_rate == Decimal("5.23")
    assert item.dividend_kind == "현금"
    call = fake.calls[0]
    assert call["path"] == _DIVIDEND
    assert call["tr_id"] == "HHKDB13470100"
    assert call["params"]["GB3"] == "2"                         # cash
    assert call["params"]["GB1"] == "0"                         # all markets
    assert call["params"]["GB4"] == "0"                         # settlement all
    assert call["params"]["F_DT"] == "20200101"
    assert call["params"]["T_DT"] == "20240403"


def test_by_dividend_stock_kind_and_market_settlement():
    fake = FakeTransport(response=_resp([_dividend_row()]))
    _client(fake).domestic.ranking.by_dividend(
        kind="stock", start="20230101", end="20231231", market="KOSDAQ", settlement="interim"
    )
    call = fake.calls[0]
    assert call["params"]["GB3"] == "1"                         # stock
    assert call["params"]["GB1"] == "3"                         # KOSDAQ
    assert call["params"]["GB4"] == "2"                         # interim


@pytest.mark.parametrize("market,expected", [("KOSPI", "1"), ("KOSPI200", "2"), ("KOSDAQ", "3")])
def test_by_dividend_market_codes(market, expected):
    fake = FakeTransport(response=_resp([_dividend_row()]))
    _client(fake).domestic.ranking.by_dividend(
        kind="cash", start="20240101", end="20240630", market=market
    )
    assert fake.calls[0]["params"]["GB1"] == expected


def test_by_dividend_rejects_bad_kind():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_dividend(
            kind="both", start="20230101", end="20231231"
        )


def test_by_dividend_bad_record_date_fails_closed():
    fake = FakeTransport(response=_resp([_dividend_row(record_date="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_dividend(kind="cash", start="20230101", end="20231231")


_SHORT_SALE = "/uapi/domestic-stock/v1/ranking/short-sale"


def _short_row(symbol="138930", name="BNK금융지주", price="7760", change="60", sign="2",
               pct="0.78", volume="1000000", qty="12000", vol_rlim="1.2",
               value="93000000", value_rlim="1.1", avg="7745"):
    return {"mksc_shrn_iscd": symbol, "hts_kor_isnm": name, "stck_prpr": price,
            "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct, "acml_vol": volume,
            "ssts_cntg_qty": qty, "ssts_vol_rlim": vol_rlim, "ssts_tr_pbmn": value,
            "ssts_tr_pbmn_rlim": value_rlim, "avrg_prc": avg}


def test_by_short_sale_maps_fields_and_synthesizes_rank():
    from kis_trader import ShortSaleRanking
    fake = FakeTransport(response=_resp([_short_row(), _short_row(symbol="000660")]))
    ranked = _client(fake).domestic.ranking.by_short_sale()
    assert [r.rank for r in ranked] == [1, 2]                   # 응답 순서로 순위
    first = ranked[0]
    assert isinstance(first, ShortSaleRanking)
    assert first.symbol == "138930"
    assert first.price == Decimal(7760)
    assert first.short_volume == 12000
    assert first.short_volume_ratio == Decimal("1.2")
    assert first.short_value == Decimal(93000000)
    assert first.average_price == Decimal(7745)
    call = fake.calls[0]
    assert call["path"] == _SHORT_SALE
    assert call["tr_id"] == "FHPST04820000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20482"
    assert call["params"]["FID_PERIOD_DIV_CODE"] == "D"         # 1d 기본
    assert call["params"]["FID_INPUT_CNT_1"] == "0"


def test_by_short_sale_monthly_window():
    fake = FakeTransport(response=_resp([_short_row()]))
    _client(fake).domestic.ranking.by_short_sale(window="3mo")
    call = fake.calls[0]
    assert call["params"]["FID_PERIOD_DIV_CODE"] == "M"
    assert call["params"]["FID_INPUT_CNT_1"] == "3"


def test_by_short_sale_negative_change_and_bad_window():
    fake = FakeTransport(response=_resp([_short_row(sign="5")]))
    ranked = _client(fake).domestic.ranking.by_short_sale(window="1w")
    assert ranked[0].change == Decimal(-60)                     # 하락 -> 음수
    assert fake.calls[0]["params"]["FID_INPUT_CNT_1"] == "4"    # 1w
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_short_sale(window="5d")


_CREDIT_BALANCE = "/uapi/domestic-stock/v1/ranking/credit-balance"
_NEAR_HIGH_LOW = "/uapi/domestic-stock/v1/ranking/near-new-highlow"


def _credit_row(symbol="005930", name="삼성전자", price="72700", change="400", sign="2",
                pct="0.55", volume="3686661", loan_stcn="1200000", loan_amt="87000000000",
                loan_rate="1.23", stln_stcn="5000", stln_amt="360000000", stln_rate="0.05"):
    return {"mksc_shrn_iscd": symbol, "hts_kor_isnm": name, "stck_prpr": price,
            "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct, "acml_vol": volume,
            "whol_loan_rmnd_stcn": loan_stcn, "whol_loan_rmnd_amt": loan_amt,
            "whol_loan_rmnd_rate": loan_rate, "whol_stln_rmnd_stcn": stln_stcn,
            "whol_stln_rmnd_amt": stln_amt, "whol_stln_rmnd_rate": stln_rate}


def _credit_resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [{"bstp_cls_code": "1001", "hts_kor_isnm": "종합"}],
                             "output2": rows})


def test_by_credit_balance_parses_output2_and_synthesizes_rank():
    from kis_trader import CreditBalanceRanking
    fake = FakeTransport(response=_credit_resp([_credit_row(), _credit_row(symbol="000660")]))
    ranked = _client(fake).domestic.ranking.by_credit_balance()
    assert [r.rank for r in ranked] == [1, 2]
    first = ranked[0]
    assert isinstance(first, CreditBalanceRanking)
    assert first.symbol == "005930"
    assert first.margin_loan_shares == 1200000
    assert first.margin_loan_ratio == Decimal("1.23")
    assert first.stock_loan_amount == Decimal(360000000)
    call = fake.calls[0]
    assert call["path"] == _CREDIT_BALANCE
    assert call["tr_id"] == "FHKST17010000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11701"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "0"       # margin_ratio 기본
    assert call["params"]["FID_OPTION"] == "2"                   # days 기본


def test_by_credit_balance_sort_and_days():
    fake = FakeTransport(response=_credit_resp([_credit_row()]))
    _client(fake).domestic.ranking.by_credit_balance(metric="loan_ratio_increase", days=30)
    call = fake.calls[0]
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "8"       # loan_ratio_increase
    assert call["params"]["FID_OPTION"] == "30"
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_credit_resp([]))).domestic.ranking.by_credit_balance(metric="x")


def test_by_credit_balance_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": [{"bstp_cls_code": "1001"}]}))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_credit_balance()


@pytest.mark.parametrize("days", [1, 0, -3, 1000, 5000])
def test_by_credit_balance_rejects_days_out_of_range_before_transport(days):
    fake = FakeTransport(response=_credit_resp([_credit_row()]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.ranking.by_credit_balance(days=days)
    assert fake.calls == []                                       # I/O 전 거부


@pytest.mark.parametrize("days", [2, 30, 999])
def test_by_credit_balance_accepts_in_range_days(days):
    fake = FakeTransport(response=_credit_resp([_credit_row()]))
    _client(fake).domestic.ranking.by_credit_balance(days=days)
    assert fake.calls[0]["params"]["FID_OPTION"] == str(days)


def _near_row(symbol="003560", name="IHQ", price="10760", change="-100", sign="5", pct="-0.92",
              volume="500000", new_hi="11000", hi_rate="97.8", new_lo="9000", lo_rate="119.6"):
    return {"mksc_shrn_iscd": symbol, "hts_kor_isnm": name, "stck_prpr": price,
            "prdy_vrss": change, "prdy_vrss_sign": sign, "prdy_ctrt": pct, "acml_vol": volume,
            "new_hgpr": new_hi, "hprc_near_rate": hi_rate, "new_lwpr": new_lo,
            "lwpr_near_rate": lo_rate}


def test_by_near_high_low_high_extreme():
    from kis_trader import NearHighLowRanking
    fake = FakeTransport(response=_resp([_near_row()]))
    ranked = _client(fake).domestic.ranking.by_near_high_low()
    first = ranked[0]
    assert isinstance(first, NearHighLowRanking)
    assert first.rank == 1
    assert first.new_high == Decimal(11000)
    assert first.high_near_rate == Decimal("97.8")
    assert first.change == Decimal(-100)                         # 하락 부호 복원
    call = fake.calls[0]
    assert call["path"] == _NEAR_HIGH_LOW
    assert call["tr_id"] == "FHPST01870000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "20187"
    assert call["params"]["FID_PRC_CLS_CODE"] == "0"             # high 기본


def test_by_near_high_low_low_extreme_and_bad_extreme():
    fake = FakeTransport(response=_resp([_near_row()]))
    _client(fake).domestic.ranking.by_near_high_low(extreme="low")
    assert fake.calls[0]["params"]["FID_PRC_CLS_CODE"] == "1"    # low
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp([]))).domestic.ranking.by_near_high_low(extreme="middle")


def test_by_near_high_low_opposite_extreme_none():
    # 신고가 조회 시 반대 극(신저가 계열)이 비어 올 수 있다 -- required 였을 땐 그 행이 KISError.
    fake = FakeTransport(response=_resp([_near_row(new_lo="", lo_rate="")]))
    first = _client(fake).domestic.ranking.by_near_high_low()[0]
    assert first.new_high == Decimal(11000)
    assert first.new_low is None
    assert first.low_near_rate is None


def test_ranking_skips_empty_rows():
    fake = FakeTransport(response=_resp([_row(), {"data_rank": "", "mksc_shrn_iscd": ""}]))
    assert len(_client(fake).domestic.ranking.by_volume()) == 1


def test_ranking_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_volume()


def test_ranking_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_volume()


def test_ranking_bad_price_fails_closed():
    fake = FakeTransport(response=_resp([_row(price="n/a")]))
    with pytest.raises(KISError):
        _client(fake).domestic.ranking.by_volume()

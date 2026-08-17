"""per-ticker 일별 시세분석 조회 (내부) -- 신용잔고/공매도 추이.

사용자면은 종목 핸들(:meth:`~kis_trader.domestic.stock.DomesticStock.credit_balance_trend` /
:meth:`~kis_trader.domestic.stock.DomesticStock.short_sale_trend`)이다. 둘 다 기준일에서 과거로 일별 추이를 준다.

KIS URL/TR-ID:
- 신용잔고 일별추이: ``GET .../quotations/daily-credit-balance`` ``FHPST04760000``
  (시장 J + 화면 20476 + 종목 + 기준일 FID_INPUT_DATE_1).
- 공매도 일별추이: ``GET .../quotations/daily-short-sale`` ``FHPST04830000``
  (시장 J + 종목 + 기간 FID_INPUT_DATE_1~FID_INPUT_DATE_2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..._bars import (
    _parse_bar_timestamp,
    _parse_minute_bar_timestamp,
)
from ..._internal._datetime import (
    _parse_intraday_timestamp,
    _parse_kst_date,
    _to_yyyymmdd,
    _today_kst,
)
from ..._internal._response import (
    _missing_block_error,
    _raise_if_error,
    _require_mapping_rows,
)
from ..._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    required_decimal,
    required_int,
)
from ...errors import KISUsageError
from ...transport import Transport
from ..entities.analysis import (
    AnalystOpinion,
    CreditBalancePoint,
    DailyTradeVolumePoint,
    EarningsEstimate,
    ExpectedPricePoint,
    ForeignNetBuyPoint,
    LoanPoint,
    ShortSalePoint,
    TradeAmountBand,
    VolumeAtPrice,
    VolumeProfile,
)

_CREDIT_PATH = "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
_CREDIT_TR = "FHPST04760000"
_EARNINGS_ESTIMATE_PATH = "/uapi/domestic-stock/v1/quotations/estimate-perform"
_EARNINGS_ESTIMATE_TR = "HHKST668300C0"

_ESTIMATE_INCOME_METRICS = (
    "revenue",
    "revenue_growth_percent",
    "operating_profit",
    "operating_profit_growth_percent",
    "net_income",
    "net_income_growth_percent",
)
_ESTIMATE_INDICATOR_METRICS = (
    "ebitda",
    "eps",
    "eps_growth_percent",
    "per",
    "ev_to_ebitda",
    "roe",
    "debt_ratio",
    "interest_coverage",
)


def fetch_earnings_estimate(transport: Transport, *, symbol: str) -> EarningsEstimate:
    """한 종목의 월간 추정 손익계산서·투자지표 스냅샷."""
    resp = transport.request(
        method="GET", path=_EARNINGS_ESTIMATE_PATH, tr_id=_EARNINGS_ESTIMATE_TR,
        params={"SHT_CD": symbol}, idempotent=True,
    )
    _raise_if_error(resp)
    header = resp.body.get("output1")
    income_rows = resp.body.get("output2")
    indicator_rows = resp.body.get("output3")
    period_rows = resp.body.get("output4")
    if not isinstance(header, Mapping):
        raise _missing_block_error("output1", resp)
    if not isinstance(income_rows, list) or len(income_rows) != len(_ESTIMATE_INCOME_METRICS):
        raise _missing_block_error("output2(6 rows)", resp)
    if not isinstance(indicator_rows, list) or len(indicator_rows) != len(
        _ESTIMATE_INDICATOR_METRICS
    ):
        raise _missing_block_error("output3(8 rows)", resp)
    if not isinstance(period_rows, list) or not 1 <= len(period_rows) <= 5:
        raise _missing_block_error("output4(1..5 rows)", resp)
    if not all(isinstance(row, Mapping) for row in [*income_rows, *indicator_rows, *period_rows]):
        raise _missing_block_error("estimate row", resp)
    periods = tuple(str(row.get("dt", "")).strip() for row in period_rows)
    if any(not period for period in periods):
        raise _missing_block_error("output4[].dt", resp)

    def metric_values(row: Mapping[str, Any]) -> tuple[Decimal | None, ...]:
        return tuple(
            optional_decimal(row.get(f"data{position}"), f"data{position}")
            for position in range(1, len(periods) + 1)
        )

    income_statement = {
        metric: metric_values(row)
        for metric, row in zip(_ESTIMATE_INCOME_METRICS, income_rows, strict=True)
    }
    indicators = {
        metric: metric_values(row)
        for metric, row in zip(_ESTIMATE_INDICATOR_METRICS, indicator_rows, strict=True)
    }
    date_text = str(header.get("estdate", "")).strip()
    return EarningsEstimate(
        symbol=symbol,
        security_name=str(header.get("item_kor_nm", "")).strip(),
        analyst_name=str(header.get("name1", "")).strip(),
        estimate_date=_parse_kst_date(date_text),
        recommendation=str(header.get("rcmd_name", "")).strip(),
        capital=optional_decimal(header.get("capital"), "capital"),
        foreign_limit_ratio=optional_decimal(
            header.get("forn_item_lmtrt"), "forn_item_lmtrt"
        ),
        periods=periods,
        income_statement=income_statement,
        indicators=indicators,
        _raw=resp.body,
    )
_SHORT_PATH = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
_SHORT_TR = "FHPST04830000"


#: 추이 조회에서 start 를 안 주면 잡는 기본 조회 구간(일). _trend 가 한 점만 주지 않도록.
_DEFAULT_TREND_DAYS = 30


def _default_start(end_yyyymmdd: str) -> str:
    """start 미지정 시 기본 시작일 = end 로부터 ``_DEFAULT_TREND_DAYS`` 일 전(YYYYMMDD)."""
    end_day = datetime.strptime(end_yyyymmdd, "%Y%m%d")  # noqa: DTZ007 -- 날짜 산술만
    return f"{end_day - timedelta(days=_DEFAULT_TREND_DAYS):%Y%m%d}"


def _resolve_date_range(
    start: str | date | None, end: str | date | None
) -> tuple[str, str]:
    """추이 조회의 [start, end] 기간을 정규화한다. end 미지정=오늘, start 미지정=기본 시작일.
    정규화 후 start 가 end 보다 늦으면 와이어 전 fail-closed(:class:`KISUsageError`)."""
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    if start_date > end_date:
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    return start_date, end_date


def _rows(transport: Transport, *, path: str, tr: str, params: Mapping[str, str],
          block: str = "output") -> Sequence[Mapping[str, Any]]:
    resp = transport.request(method="GET", path=path, tr_id=tr, params=dict(params),
                             idempotent=True)
    _raise_if_error(resp)
    rows = resp.body.get(block)
    if not isinstance(rows, list):
        raise _missing_block_error(block, resp)
    return rows


def fetch_credit_balance_trend(
    transport: Transport, *, symbol: str, as_of_date: str | date | None = None
) -> list[CreditBalancePoint]:
    """일별 신용잔고(융자/대주) 추이(기준일에서 과거로). ``as_of_date`` 없으면 오늘 기준."""
    base = _today_kst() if as_of_date is None else _to_yyyymmdd(as_of_date, "as_of_date")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": base,
    }
    points: list[CreditBalancePoint] = []
    for row in _rows(transport, path=_CREDIT_PATH, tr=_CREDIT_TR, params=params):
        day = str(row.get("deal_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            CreditBalancePoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                margin_loan_shares=required_int(
                    row.get("whol_loan_rmnd_stcn"), "whol_loan_rmnd_stcn"
                ),
                margin_loan_amount=required_decimal(
                    row.get("whol_loan_rmnd_amt"), "whol_loan_rmnd_amt"
                ),
                margin_loan_ratio=optional_decimal(
                    row.get("whol_loan_rmnd_rate"), "whol_loan_rmnd_rate"
                ),
                stock_loan_shares=required_int(
                    row.get("whol_stln_rmnd_stcn"), "whol_stln_rmnd_stcn"
                ),
                stock_loan_amount=required_decimal(
                    row.get("whol_stln_rmnd_amt"), "whol_stln_rmnd_amt"
                ),
                stock_loan_ratio=optional_decimal(
                    row.get("whol_stln_rmnd_rate"), "whol_stln_rmnd_rate"
                ),
                _raw=row,
            )
        )
    return points


def fetch_short_sale_trend(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[ShortSalePoint]:
    """일별 공매도 추이(기간 [start, end], 최근->과거). ``start`` 미지정이면 ``end`` 로부터 30일 전."""
    start_date, end_date = _resolve_date_range(start, end)
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    points: list[ShortSalePoint] = []
    for row in _rows(transport, path=_SHORT_PATH, tr=_SHORT_TR, params=params, block="output2"):
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            ShortSalePoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                close=required_decimal(row.get("stck_clpr"), "stck_clpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                short_volume=required_int(row.get("ssts_cntg_qty"), "ssts_cntg_qty"),
                short_volume_ratio=optional_decimal(row.get("ssts_vol_rlim"), "ssts_vol_rlim"),
                short_amount=required_decimal(row.get("ssts_tr_pbmn"), "ssts_tr_pbmn"),
                short_average_price=optional_decimal(row.get("avrg_prc"), "avrg_prc"),
                _raw=row,
            )
        )
    return points


_LOAN_PATH = "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
_LOAN_TR = "HHPST074500C0"


def fetch_loan_trend(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[LoanPoint]:
    """일별 대차거래(대여) 추이(기간 [start, end], 최근->과거). ``start`` 미지정이면 ``end`` 로부터 30일 전."""
    start_date, end_date = _resolve_date_range(start, end)
    params = {
        "MRKT_DIV_CLS_CODE": "1",
        "MKSC_SHRN_ISCD": symbol,
        "START_DATE": start_date,
        "END_DATE": end_date,
        "CTS": "",
    }
    points: list[LoanPoint] = []
    for row in _rows(transport, path=_LOAN_PATH, tr=_LOAN_TR, params=params, block="output1"):
        day = str(row.get("bsop_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            LoanPoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                new_shares=required_int(row.get("new_stcn"), "new_stcn"),
                redeemed_shares=required_int(row.get("rdmp_stcn"), "rdmp_stcn"),
                balance_shares=required_int(row.get("rmnd_stcn"), "rmnd_stcn"),
                balance_amount=required_decimal(row.get("rmnd_amt"), "rmnd_amt"),
                balance_change=required_int(row.get("prdy_rmnd_vrss"), "prdy_rmnd_vrss"),
                _raw=row,
            )
        )
    return points


_DAILY_TRADE_VOL_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-trade-volume"
_DAILY_TRADE_VOL_TR = "FHKST03010800"


def fetch_daily_trade_volume(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[DailyTradeVolumePoint]:
    """일별 매수/매도 체결량 추이(기간 [start, end], 최근->과거). ``start`` 미지정이면 ``end`` 로부터
    30일 전. 응답 배열은 ``output2`` (``output1`` 은 구간 합계)."""
    start_date, end_date = _resolve_date_range(start, end)
    params = {
        # 라이브 KIS 는 문서(접미사 없음)와 달리 이 조회의 시장/종목 필드에 _1 접미사를 요구한다
        # (둘 다 보내 문서·라이브 양쪽에 안전).
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_MRKT_DIV_CODE_1": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_ISCD_1": symbol,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
    }
    resp = transport.request(method="GET", path=_DAILY_TRADE_VOL_PATH, tr_id=_DAILY_TRADE_VOL_TR,
                             params=params, idempotent=True)
    _raise_if_error(resp)
    rows = _require_mapping_rows("output2", resp)
    points: list[DailyTradeVolumePoint] = []
    for row in rows:
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        points.append(
            DailyTradeVolumePoint(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                buy_volume=required_int(row.get("total_shnu_qty"), "total_shnu_qty"),
                sell_volume=required_int(row.get("total_seln_qty"), "total_seln_qty"),
                _raw=row,
            )
        )
    return points


_TRADE_BAND_PATH = "/uapi/domestic-stock/v1/quotations/tradprt-byamt"
_TRADE_BAND_TR = "FHKST111900C0"


def fetch_trade_amount_bands(
    transport: Transport, *, symbol: str
) -> list[TradeAmountBand]:
    """당일 체결금액대별 매매비중(금액대 리스트). 순매수 비율/건수는 음수 가능(pre-signed)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "11119",
        "FID_INPUT_ISCD": symbol,
    }
    bands: list[TradeAmountBand] = []
    for row in _rows(transport, path=_TRADE_BAND_PATH, tr=_TRADE_BAND_TR, params=params):
        label = str(row.get("prpr_name", "")).strip()
        if not label:
            continue
        bands.append(
            TradeAmountBand(
                symbol=symbol,
                band_label=label,
                average_price=required_decimal(row.get("smtn_avrg_prpr"), "smtn_avrg_prpr"),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                net_buy_ratio=optional_decimal(row.get("whol_ntby_qty_rate"),
                                               "whol_ntby_qty_rate"),
                net_buy_count=required_int(row.get("ntby_cntg_csnu"), "ntby_cntg_csnu"),
                sell_volume=required_int(row.get("seln_cnqn_smtn"), "seln_cnqn_smtn"),
                sell_volume_ratio=optional_decimal(row.get("whol_seln_vol_rate"),
                                                   "whol_seln_vol_rate"),
                sell_count=required_int(row.get("seln_cntg_csnu"), "seln_cntg_csnu"),
                buy_volume=required_int(row.get("shnu_cnqn_smtn"), "shnu_cnqn_smtn"),
                buy_volume_ratio=optional_decimal(row.get("whol_shun_vol_rate"),
                                                  "whol_shun_vol_rate"),
                buy_count=required_int(row.get("shnu_cntg_csnu"), "shnu_cntg_csnu"),
                _raw=row,
            )
        )
    return bands


_EXP_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/exp-price-trend"
_EXP_PRICE_TR = "FHPST01810000"


def fetch_expected_price_trend(
    transport: Transport, *, symbol: str, exclude_zero_volume: bool = False
) -> list[ExpectedPricePoint]:
    """동시호가 예상 체결가 추이(시각 리스트, 최근->과거). ``exclude_zero_volume`` 면 체결량 0 시각 제외.
    응답 배열은 ``output2`` (``output1`` 은 현재 예상체결 스냅샷)."""
    params = {
        "fid_mkop_cls_code": "4" if exclude_zero_volume else "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": symbol,
    }
    resp = transport.request(method="GET", path=_EXP_PRICE_PATH, tr_id=_EXP_PRICE_TR,
                             params=params, idempotent=True)
    _raise_if_error(resp)
    rows = _require_mapping_rows("output2", resp)
    points: list[ExpectedPricePoint] = []
    for row in rows:
        day = str(row.get("stck_bsop_date", "")).strip()
        moment = str(row.get("stck_cntg_hour", "")).strip()
        if not day or not moment:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            ExpectedPricePoint(
                symbol=symbol,
                timestamp=_parse_minute_bar_timestamp(date_text=day, time_text=moment),
                expected_price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                _raw=row,
            )
        )
    return points


_OPINION_PATH = "/uapi/domestic-stock/v1/quotations/invest-opinion"
_OPINION_TR = "FHKST663300C0"


def fetch_analyst_opinions(
    transport: Transport, *, symbol: str,
    start: str | date | None = None, end: str | date | None = None,
) -> list[AnalystOpinion]:
    """기간 [start, end] 의 애널리스트 투자의견·목표주가 시계열(최근->과거). ``start`` 미지정이면
    ``end`` 로부터 30일 전."""
    start_date, end_date = _resolve_date_range(start, end)
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "16633",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    opinions: list[AnalystOpinion] = []
    for row in _rows(transport, path=_OPINION_PATH, tr=_OPINION_TR, params=params):
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        opinions.append(
            AnalystOpinion(
                symbol=symbol,
                timestamp=_parse_bar_timestamp(day),
                opinion=str(row.get("invt_opnn", "")).strip(),
                previous_opinion=str(row.get("rgbf_invt_opnn", "")).strip(),
                target_price=optional_decimal(row.get("hts_goal_prc"), "hts_goal_prc"),
                previous_close=optional_decimal(row.get("stck_prdy_clpr"), "stck_prdy_clpr"),
                disparity_percent=optional_decimal(row.get("dprt"), "dprt"),
                _raw=row,
            )
        )
    return opinions


def fetch_foreign_net_buy_trend(
    transport: Transport, *, symbol: str
) -> list[ForeignNetBuyPoint]:
    """장중 외국계(외국인 회원사) 순매수 추이(시간대별, 응답 순서 유지).

    KIS 국내주식 외국계 매매종목 가집계 API를 조회한다.
    URL: ``GET /uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend``.
    TR-ID: ``FHKST644400C0``.
    ``tr_cont`` 미지원으로 단일 호출하며 ``output`` 배열을 반환한다.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_ISCD_2": "99999",
    }
    resp = transport.request(
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend",
        tr_id="FHKST644400C0",
        params=params,
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
    as_of = _parse_bar_timestamp(_today_kst())
    points: list[ForeignNetBuyPoint] = []
    for row in rows:
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        points.append(
            ForeignNetBuyPoint(
                symbol=symbol,
                timestamp=_parse_intraday_timestamp(str(row.get("bsop_hour", "")).strip(), as_of),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                foreign_sell_volume=required_int(row.get("frgn_seln_vol"), "frgn_seln_vol"),
                foreign_buy_volume=required_int(row.get("frgn_shnu_vol"), "frgn_shnu_vol"),
                foreign_net_buy=required_int(row.get("glob_ntby_qty"), "glob_ntby_qty"),
                foreign_net_buy_change=required_int(
                    row.get("frgn_ntby_qty_icdc"), "frgn_ntby_qty_icdc"
                ),
                _raw=row,
            )
        )
    return points


def fetch_volume_profile(transport: Transport, *, symbol: str) -> VolumeProfile:
    """종목의 가격대별 거래량 분포(매물대)와 시세 요약을 조회한다.

    KIS 국내주식 매물대/거래비중 API를 조회한다.
    URL: ``GET /uapi/domestic-stock/v1/quotations/pbar-tratio``.
    TR-ID: ``FHPST01130000``.
    ``tr_cont`` 미지원으로 단일 호출하며 ``output1`` 요약과 ``output2`` 가격대를 반환한다.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_COND_SCR_DIV_CODE": "20113",
        "FID_INPUT_HOUR_1": "",
    }
    resp = transport.request(
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/pbar-tratio",
        tr_id="FHPST01130000",
        params=params,
        idempotent=True,
    )
    _raise_if_error(resp)
    summary = resp.body.get("output1")
    if not isinstance(summary, Mapping):
        raise _missing_block_error("output1", resp)
    bands_raw = _require_mapping_rows("output2", resp)
    sign = str(summary.get("prdy_vrss_sign", "")).strip()
    bands = tuple(
        VolumeAtPrice(
            rank=required_int(row.get("data_rank"), "data_rank"),
            price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
            volume=required_int(row.get("cntg_vol"), "cntg_vol"),
            volume_share_percent=required_decimal(
                row.get("acml_vol_rlim"), "acml_vol_rlim"
            ),
            _raw=row,
        )
        for row in bands_raw
    )
    return VolumeProfile(
        symbol=symbol,
        market=str(summary.get("rprs_mrkt_kor_name", "")).strip(),
        name=str(summary.get("hts_kor_isnm", "")).strip(),
        price=required_decimal(summary.get("stck_prpr"), "stck_prpr"),
        change=_apply_change_sign(
            required_decimal(summary.get("prdy_vrss"), "prdy_vrss"), sign
        ),
        change_percent=_apply_change_sign(
            required_decimal(summary.get("prdy_ctrt"), "prdy_ctrt"), sign
        ),
        volume=required_int(summary.get("acml_vol"), "acml_vol"),
        weighted_average_price=required_decimal(
            summary.get("wghn_avrg_stck_prc"), "wghn_avrg_stck_prc"
        ),
        listed_shares=required_int(summary.get("lstn_stcn"), "lstn_stcn"),
        bands=bands,
        _raw=summary,
    )

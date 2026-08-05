"""시장 전체 분석 조회 (내부) -- 시장별 투자자매매동향 등.

사용자면은 시장 분석 네임스페이스(:class:`~kis_openapi.market.MarketQueries`, ``kis.market``)다.
종목이 아니라 시장(코스피/코스닥) 전체가 대상이라 종목 핸들이 아닌 세션 네임스페이스에 둔다.

KIS URL/TR-id:
- 시장별 투자자매매동향(일별): ``GET .../quotations/inquire-investor-daily-by-market``
  ``FHPTJ04040000`` (시장구분 U + 시장코드 + 기간). 시장코드: 코스피 0001/KSP, 코스닥 1001/KSQ.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .._wire import required_decimal, required_int
from ..errors import KISUsageError
from ..market_items import Market, MarketInvestorFlow, ProgramTradeSummary
from ..transport import Transport
from .market_data import (
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _raise_if_error,
    _to_yyyymmdd,
    _today_kst,
)

_INVESTOR_BY_MARKET_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
_INVESTOR_BY_MARKET_TR = "FHPTJ04040000"
#: 시장 -> (지수코드 FID_INPUT_ISCD, 시장약어 FID_INPUT_ISCD_1). 원장 예시 대조.
_MARKET_CODE = {"KOSPI": ("0001", "KSP"), "KOSDAQ": ("1001", "KSQ")}


def _default_start(end_yyyymmdd: str, days: int = 30) -> str:
    end_day = datetime.strptime(end_yyyymmdd, "%Y%m%d")  # noqa: DTZ007 -- 날짜 산술만
    return f"{end_day - timedelta(days=days):%Y%m%d}"


def fetch_market_investor_flows(
    transport: Transport, *, market: Market = "KOSPI", as_of: str | date | None = None,
) -> list[MarketInvestorFlow]:
    """시장(코스피/코스닥) 전체의 투자자 순매수 최근 히스토리(``as_of`` 기준일에서 과거로).

    이 엔드포인트는 기준일 하나(FID_INPUT_DATE_1)에서 뒤로 고정 히스토리를 주므로 기간이 아니라
    앵커 날짜를 받는다(원장: DATE_2 는 "DATE_1 과 동일날짜 입력"). ``as_of`` 없으면 오늘 기준."""
    try:
        index_code, market_abbr = _MARKET_CODE[market]
    except KeyError:
        raise KISUsageError(f"market 은 {sorted(_MARKET_CODE)} 중 하나: {market!r}") from None
    anchor = _today_kst() if as_of is None else _to_yyyymmdd(as_of, "as_of")
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": anchor,
        "FID_INPUT_ISCD_1": market_abbr,
        "FID_INPUT_DATE_2": anchor,     # 원장: DATE_1 과 동일날짜(앵커에서 백워드 히스토리)
        "FID_INPUT_ISCD_2": index_code,
    }
    resp = transport.request(
        method="GET", path=_INVESTOR_BY_MARKET_PATH, tr_id=_INVESTOR_BY_MARKET_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    flows: list[MarketInvestorFlow] = []
    for row in rows:
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        flows.append(
            MarketInvestorFlow(
                market=market,
                timestamp=_parse_bar_timestamp(day),
                index_value=required_decimal(row.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
                index_change=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
                ),
                index_change_percent=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_ctrt"), "bstp_nmix_prdy_ctrt"), sign
                ),
                foreign_net=required_int(row.get("frgn_ntby_qty"), "frgn_ntby_qty"),
                individual_net=required_int(row.get("prsn_ntby_qty"), "prsn_ntby_qty"),
                institutional_net=required_int(row.get("orgn_ntby_qty"), "orgn_ntby_qty"),
                _raw=row,
            )
        )
    return flows


_PROGRAM_SUMMARY_PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
_PROGRAM_SUMMARY_TR = "FHPPG04600001"
#: 시장 -> FID_MRKT_CLS_CODE.
_PROGRAM_MARKET = {"KOSPI": "K", "KOSDAQ": "Q"}


def fetch_program_trade_summary(
    transport: Transport, *, market: Market = "KOSPI",
    start: str | date | None = None, end: str | date | None = None,
) -> list[ProgramTradeSummary]:
    """시장(코스피/코스닥) 전체의 일별 프로그램매매 종합(차익/비차익 순매수; 최근->과거). ``start``
    미지정이면 ``end`` 로부터 30일 전."""
    try:
        market_code = _PROGRAM_MARKET[market]
    except KeyError:
        raise KISUsageError(f"market 은 {sorted(_PROGRAM_MARKET)} 중 하나: {market!r}") from None
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_MRKT_CLS_CODE": market_code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    resp = transport.request(
        method="GET", path=_PROGRAM_SUMMARY_PATH, tr_id=_PROGRAM_SUMMARY_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    summaries: list[ProgramTradeSummary] = []
    for row in rows:
        day = str(row.get("stck_bsop_date", "")).strip()
        if not day:
            continue
        summaries.append(
            ProgramTradeSummary(
                market=market,
                timestamp=_parse_bar_timestamp(day),
                # 값 필드는 모두 smtn(차익/비차익 합계). smtm 은 *비율* 필드(_rate)에만 붙는 오탈자다.
                arbitrage_net_volume=required_int(row.get("arbt_smtn_ntby_qty"),
                                                  "arbt_smtn_ntby_qty"),
                arbitrage_net_amount=required_decimal(row.get("arbt_smtn_ntby_tr_pbmn"),
                                                      "arbt_smtn_ntby_tr_pbmn"),
                nonarb_net_volume=required_int(row.get("nabt_smtn_ntby_qty"),
                                               "nabt_smtn_ntby_qty"),
                nonarb_net_amount=required_decimal(row.get("nabt_smtn_ntby_tr_pbmn"),
                                                   "nabt_smtn_ntby_tr_pbmn"),
                _raw=row,
            )
        )
    return summaries

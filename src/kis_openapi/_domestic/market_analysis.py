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
from ..market_items import MarketInvestorFlow
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
    transport: Transport, *, market: str = "KOSPI",
    start: str | date | None = None, end: str | date | None = None,
) -> list[MarketInvestorFlow]:
    """시장(코스피/코스닥) 전체의 일별 투자자 순매수(최근->과거). ``start`` 미지정이면 ``end`` 로부터 30일 전."""
    try:
        index_code, market_abbr = _MARKET_CODE[market]
    except KeyError:
        raise KISUsageError(f"market 은 {sorted(_MARKET_CODE)} 중 하나: {market!r}") from None
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_ISCD_1": market_abbr,
        "FID_INPUT_DATE_2": end_date,
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

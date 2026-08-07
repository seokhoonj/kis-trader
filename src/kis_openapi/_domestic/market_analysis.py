"""시장 전체 분석 조회 (내부) -- 시장별 투자자매매동향 등.

사용자면은 시장 분석 네임스페이스(:class:`~kis_openapi.market.MarketQueries`, ``kis.market``)다.
종목이 아니라 시장(코스피/코스닥) 전체가 대상이라 종목 핸들이 아닌 세션 네임스페이스에 둔다.

KIS URL/TR-id:
- 시장별 투자자매매동향(일별): ``GET .../quotations/inquire-investor-daily-by-market``
  ``FHPTJ04040000`` (시장구분 U + 시장코드 + 기간). 시장코드: 코스피 0001/KSP, 코스닥 1001/KSQ.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .._wire import optional_decimal, required_decimal, required_int
from ..errors import KISUsageError
from ..market_items import (
    ForeignBrokerFlow,
    LimitStock,
    Market,
    MarketFunds,
    MarketInvestorFlow,
    NewsItem,
    ProgramFlowPoint,
    ProgramTradeSummary,
    TradingDay,
    VIEvent,
)
from ..transport import Transport
from .market_data import (
    _KST,
    _apply_change_sign,
    _missing_block_error,
    _parse_bar_timestamp,
    _parse_intraday_timestamp,
    _parse_kst_date,
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


_VI_STATUS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-vi-status"
_VI_STATUS_TR = "FHPST01390000"


def _combine_date_time(date_yyyymmdd: str, time_hhmmss: str) -> datetime:
    """영업일(YYYYMMDD) + 시각(HHMMSS) -> KST-aware datetime."""
    stamp = datetime.strptime(date_yyyymmdd + time_hhmmss, "%Y%m%d%H%M%S")  # noqa: DTZ007
    return stamp.replace(tzinfo=_KST)


def fetch_vi_events(
    transport: Transport, *, as_of: str | date | None = None
) -> list[VIEvent]:
    """전 시장의 VI(변동성완화장치) 발동 이벤트 목록(``as_of`` 기준일; 미지정이면 오늘)."""
    anchor = _today_kst() if as_of is None else _to_yyyymmdd(as_of, "as_of")
    params = {
        "FID_DIV_CLS_CODE": "0",
        "FID_COND_SCR_DIV_CODE": "20139",
        "FID_MRKT_CLS_CODE": "0",          # 0: 전체 시장
        "FID_INPUT_ISCD": "",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_INPUT_DATE_1": anchor,
        "FID_TRGT_CLS_CODE": "",
        "FID_TRGT_EXLS_CLS_CODE": "",
    }
    resp = transport.request(
        method="GET", path=_VI_STATUS_PATH, tr_id=_VI_STATUS_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    events: list[VIEvent] = []
    for row in rows:
        code = str(row.get("mksc_shrn_iscd", "")).strip()
        day = str(row.get("bsop_date", "")).strip()
        triggered = str(row.get("cntg_vi_hour", "")).strip()
        if not code or not day or not triggered:
            continue
        released = str(row.get("vi_cncl_hour", "")).strip()
        events.append(
            VIEvent(
                symbol=code,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                triggered_at=_combine_date_time(day, triggered),
                released_at=_combine_date_time(day, released) if released.strip("0") else None,
                vi_class=str(row.get("vi_cls_code", "")).strip(),
                vi_kind=str(row.get("vi_kind_code", "")).strip(),
                trigger_price=required_decimal(row.get("vi_prc"), "vi_prc"),
                base_price=optional_decimal(row.get("vi_stnd_prc"), "vi_stnd_prc"),
                disparity_percent=optional_decimal(row.get("vi_dprt"), "vi_dprt"),
                count=required_int(row.get("vi_count"), "vi_count"),
                _raw=row,
            )
        )
    return events


_LIMIT_PATH = "/uapi/domestic-stock/v1/quotations/capture-uplowprice"
_LIMIT_TR = "FHKST130000C0"


def fetch_limit_stocks(transport: Transport) -> list[LimitStock]:
    """상한가/하한가에 도달한 종목 전체 스냅샷(전 시장)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "11300",
        "FID_PRC_CLS_CODE": "0",           # 0: 상하한가 전체
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD": "0000",
    }
    resp = transport.request(
        method="GET", path=_LIMIT_PATH, tr_id=_LIMIT_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    stocks: list[LimitStock] = []
    for row in rows:
        code = str(row.get("mksc_shrn_iscd", "")).strip()
        if not code:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        stocks.append(
            LimitStock(
                symbol=code,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                upper_limit=required_decimal(row.get("stck_mxpr"), "stck_mxpr"),
                lower_limit=required_decimal(row.get("stck_llam"), "stck_llam"),
                total_ask_quantity=required_int(row.get("total_askp_rsqn"), "total_askp_rsqn"),
                total_bid_quantity=required_int(row.get("total_bidp_rsqn"), "total_bidp_rsqn"),
                _raw=row,
            )
        )
    return stocks


_PROGRAM_FLOW_PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"
_PROGRAM_FLOW_TR = "FHPPG04600101"


def fetch_program_flow(
    transport: Transport, *, market: Market = "KOSPI"
) -> list[ProgramFlowPoint]:
    """당일 시간대별 프로그램매매 순매수 대금(시간 순). 시장(코스피/코스닥)."""
    try:
        market_code = _PROGRAM_MARKET[market]
    except KeyError:
        raise KISUsageError(f"market 은 {sorted(_PROGRAM_MARKET)} 중 하나: {market!r}") from None
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": market_code}
    resp = transport.request(
        method="GET", path=_PROGRAM_FLOW_PATH, tr_id=_PROGRAM_FLOW_TR, params=params,
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    as_of = datetime.now(_KST)
    points: list[ProgramFlowPoint] = []
    for row in rows:
        time_text = str(row.get("bsop_hour", "")).strip()
        if not time_text:
            continue
        points.append(
            ProgramFlowPoint(
                market=market,
                timestamp=_parse_intraday_timestamp(time_text, as_of),
                arbitrage_net_amount=required_decimal(
                    row.get("arbt_smtn_ntby_tr_pbmn"), "arbt_smtn_ntby_tr_pbmn"
                ),
                nonarb_net_amount=required_decimal(
                    row.get("nabt_smtn_ntby_tr_pbmn"), "nabt_smtn_ntby_tr_pbmn"
                ),
                total_net_amount=required_decimal(
                    row.get("whol_smtn_ntby_tr_pbmn"), "whol_smtn_ntby_tr_pbmn"
                ),
                _raw=row,
            )
        )
    return points


_CALENDAR_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"
_CALENDAR_TR = "CTCA0903R"


def fetch_trading_calendar(
    transport: Transport, *, base_date: str | date | None = None
) -> list[TradingDay]:
    """거래 캘린더(``base_date`` 기준일에서 앞으로 한 페이지). 각 날짜의 영업/거래/개장/결제 여부.
    ``base_date`` 없으면 오늘. (연속조회 페이지네이션은 미구현 -- 첫 페이지만.)"""
    base = _today_kst() if base_date is None else _to_yyyymmdd(base_date, "base_date")
    params = {"BASS_DT": base, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
    resp = transport.request(
        method="GET", path=_CALENDAR_PATH, tr_id=_CALENDAR_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    days: list[TradingDay] = []
    for row in rows:
        day = str(row.get("bass_dt", "")).strip()
        if not day:
            continue
        days.append(
            TradingDay(
                date=_parse_bar_timestamp(day),
                weekday=str(row.get("wday_dvsn_cd", "")).strip(),
                is_business_day=str(row.get("bzdy_yn", "")).strip() == "Y",
                is_trading_day=str(row.get("tr_day_yn", "")).strip() == "Y",
                is_open=str(row.get("opnd_yn", "")).strip() == "Y",
                is_settlement_day=str(row.get("sttl_day_yn", "")).strip() == "Y",
                _raw=row,
            )
        )
    return days


_NEWS_PATH = "/uapi/domestic-stock/v1/quotations/news-title"
_NEWS_TR = "FHKST01011800"


def fetch_news(
    transport: Transport, *, symbol: str = "", date_: str | date | None = None
) -> list[NewsItem]:
    """시황/공시 뉴스 제목 피드(최신순). ``symbol`` 을 주면 그 종목 관련만, ``date_`` 를 주면 그 날짜.
    둘 다 없으면 전체 최근."""
    input_date = "" if date_ is None else _to_yyyymmdd(date_, "date_")
    params = {
        "FID_NEWS_OFER_ENTP_CODE": "",
        "FID_COND_MRKT_CLS_CODE": "",
        "FID_INPUT_ISCD": symbol,
        "FID_TITL_CNTT": "",
        "FID_INPUT_DATE_1": input_date,
        "FID_INPUT_HOUR_1": "",
        "FID_RANK_SORT_CLS_CODE": "",
        "FID_INPUT_SRNO": "",
    }
    resp = transport.request(
        method="GET", path=_NEWS_PATH, tr_id=_NEWS_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    items: list[NewsItem] = []
    for row in rows:
        day = str(row.get("data_dt", "")).strip()
        tm = str(row.get("data_tm", "")).strip()
        if not day or not tm:
            continue
        symbols = tuple(
            code for i in range(1, 11)
            if (code := str(row.get(f"iscd{i}", "")).strip())
        )
        items.append(
            NewsItem(
                serial=str(row.get("cntt_usiq_srno", "")).strip(),
                timestamp=_combine_date_time(day, tm),
                title=str(row.get("hts_pbnt_titl_cntt", "")).strip(),
                source=str(row.get("dorg", "")).strip(),
                category=str(row.get("news_lrdv_code", "")).strip(),
                symbols=symbols,
                _raw=row,
            )
        )
    return items


_FOREIGN_BROKER_PATH = "/uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate"
_FOREIGN_BROKER_TR = "FHKST644100C0"
_FOREIGN_BROKER_SORT = {"amount": "0", "volume": "1"}


def fetch_foreign_broker_trades(
    transport: Transport, *, sort: str = "amount"
) -> list[ForeignBrokerFlow]:
    """외국계 창구 매매종목 가집계(전 시장). ``sort`` 는 ``"amount"``(금액순)/``"volume"``(수량순)."""
    try:
        sort_code = _FOREIGN_BROKER_SORT[sort]
    except KeyError:
        raise KISUsageError(
            f"sort 는 {sorted(_FOREIGN_BROKER_SORT)} 중 하나: {sort!r}"
        ) from None
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "16441",
        "FID_INPUT_ISCD": "0000",
        "FID_RANK_SORT_CLS_CODE": sort_code,
        "FID_RANK_SORT_CLS_CODE_2": "0",
    }
    resp = transport.request(
        method="GET", path=_FOREIGN_BROKER_PATH, tr_id=_FOREIGN_BROKER_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    flows: list[ForeignBrokerFlow] = []
    for row in rows:
        code = str(row.get("stck_shrn_iscd", "")).strip()
        if not code:
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        flows.append(
            ForeignBrokerFlow(
                rank=len(flows) + 1,
                symbol=code,
                name=str(row.get("hts_kor_isnm", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                volume=required_int(row.get("acml_vol"), "acml_vol"),
                estimated_net=required_int(row.get("glob_ntsl_qty"), "glob_ntsl_qty"),
                estimated_buy=required_int(row.get("glob_total_shnu_qty"), "glob_total_shnu_qty"),
                estimated_sell=required_int(row.get("glob_total_seln_qty"), "glob_total_seln_qty"),
                _raw=row,
            )
        )
    return flows


_MARKET_FUNDS_PATH = "/uapi/domestic-stock/v1/quotations/mktfunds"
_MARKET_FUNDS_TR = "FHKST649100C0"


def fetch_market_funds(
    transport: Transport, *, as_of: str | date | None = None
) -> list[MarketFunds]:
    """증시자금 종합의 최근 일별 추이(``as_of`` 기준일에서 과거로)를 조회한다.

    고객예탁금·신용융자잔고·펀드유형별 잔고·시가총액을 시장 전체 기준으로 돌려준다.
    ``as_of`` 는 앵커 날짜이며 미지정하면 오늘을 사용하고, 응답의 최신순을 보존한다.
    KIS URL: ``GET /uapi/domestic-stock/v1/quotations/mktfunds``.
    TR-id: ``FHKST649100C0``. 연속조회 미지원으로 한 번만 호출한다.
    """
    anchor = _today_kst() if as_of is None else _to_yyyymmdd(as_of, "as_of")
    params = {"FID_INPUT_DATE_1": anchor}
    resp = transport.request(
        method="GET", path=_MARKET_FUNDS_PATH, tr_id=_MARKET_FUNDS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    funds: list[MarketFunds] = []
    for row in rows:
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        funds.append(
            MarketFunds(
                date=_parse_kst_date(str(row.get("bsop_date", "")).strip()),
                index_value=required_decimal(row.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
                index_change=_apply_change_sign(
                    required_decimal(row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"), sign
                ),
                index_change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                market_cap=optional_decimal(row.get("hts_avls"), "hts_avls"),
                customer_deposits=optional_decimal(
                    row.get("cust_dpmn_amt"), "cust_dpmn_amt"
                ),
                customer_deposits_change=optional_decimal(
                    row.get("cust_dpmn_amt_prdy_vrss"), "cust_dpmn_amt_prdy_vrss"
                ),
                turnover_rate=optional_decimal(row.get("amt_tnrt"), "amt_tnrt"),
                receivables=optional_decimal(row.get("uncl_amt"), "uncl_amt"),
                credit_loan_balance=optional_decimal(
                    row.get("crdt_loan_rmnd"), "crdt_loan_rmnd"
                ),
                futures_deposits=optional_decimal(
                    row.get("futs_tfam_amt"), "futs_tfam_amt"
                ),
                equity_fund=optional_decimal(row.get("sttp_amt"), "sttp_amt"),
                mixed_fund=optional_decimal(row.get("mxtp_amt"), "mxtp_amt"),
                bond_fund=optional_decimal(row.get("bntp_amt"), "bntp_amt"),
                mmf=optional_decimal(row.get("mmf_amt"), "mmf_amt"),
                collateral_loan_balance=optional_decimal(
                    row.get("secu_lend_amt"), "secu_lend_amt"
                ),
                _raw=row,
            )
        )
    return funds

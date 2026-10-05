"""시장 전체 분석 조회 (내부) -- 시장별 투자자매매동향 등.

사용자면은 시장 분석 네임스페이스(:class:`~kis_trader.domestic.market.MarketQueries`, ``kis.domestic.market``)다.
종목이 아니라 시장(코스피/코스닥) 전체가 대상이라 종목 핸들이 아닌 세션 네임스페이스에 둔다.

KIS URL/TR-ID:
- 시장별 투자자매매동향(일별): ``GET .../quotations/inquire-investor-daily-by-market``
  ``FHPTJ04040000`` (시장구분 U + 시장코드 + 기준일). 시장코드: 코스피 0001/KSP, 코스닥 1001/KSQ.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from ..._bars import _parse_bar_timestamp
from ..._internal._datetime import (
    _KST,
    _combine_date_time,
    _parse_intraday_timestamp,
    _parse_kst_date,
    _to_yyyymmdd,
    _today_kst,
    parse_optional_kst_date,
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
from ...news import NewsHeadline
from ...transport import RawResponse, Transport
from ..entities.investor import InvestorActivity, InvestorNetActivity
from ..entities.market import (
    BrokerOpinion,
    CreditEligibleStock,
    ForeignBrokerFlow,
    FuturesMarketSchedule,
    InterestRateQuote,
    InvestorNetBuyStock,
    LendableStock,
    LimitStock,
    Market,
    MarketFunds,
    MarketInvestorFlow,
    MarketInvestorSnapshot,
    ProgramFlowPoint,
    ProgramInvestorTrade,
    ProgramTradeSummary,
    TradingDay,
    VIEvent,
)
from ..entities.program import ProgramTradeActivity

_INVESTOR_BY_MARKET_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
_INVESTOR_BY_MARKET_TR = "FHPTJ04040000"
_INVESTOR_SNAPSHOT_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
_INVESTOR_SNAPSHOT_TR = "FHPTJ04030000"
_INVESTOR_NET_BUY_PATH = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
_INVESTOR_NET_BUY_TR = "FHPTJ04400000"
#: 시장 -> (지수코드 FID_INPUT_ISCD, 시장약어 FID_INPUT_ISCD_1). KIS 예시 대조.
_MARKET_CODE = {"KOSPI": ("0001", "KSP"), "KOSDAQ": ("1001", "KSQ")}

_LENDABLE_PATH = "/uapi/domestic-stock/v1/quotations/lendable-by-company"
_LENDABLE_TR = "CTSC2702R"
_LENDABLE_MARKET = {"all": "00", "KOSPI": "02", "KOSDAQ": "03"}
_CREDIT_ELIGIBLE_PATH = "/uapi/domestic-stock/v1/quotations/credit-by-company"
_CREDIT_ELIGIBLE_TR = "FHPST04770000"
_CREDIT_MARKET = {"all": "0000", "KOSPI": "0001", "KOSDAQ": "1001", "KOSPI200": "2001"}
_BROKER_OPINIONS_PATH = "/uapi/domestic-stock/v1/quotations/invest-opbysec"
_BROKER_OPINIONS_TR = "FHKST663400C0"


_INVESTOR_SNAPSHOT_PREFIX = {
    "foreign": "frgn", "individual": "prsn", "institutional": "orgn",
    "securities": "scrt", "investment_trust": "ivtr", "private_equity": "pe_fund",
    "bank": "bank", "insurance": "insu", "merchant_bank": "mrbn", "fund": "fund",
    "other_organization": "etc_orgt", "other_corporation": "etc_corp",
}


#: inquire-investor-daily-by-market 의 주체별 (영문명, 순매수 수량 키, 순매수 대금 키).
#: suffix 가 주체마다 불규칙해(사모펀드/기타단체/기타법인 수량은 _ntby_vol, 외국인 등록/비등록
#: 대금은 _ntby_pbmn) 규칙이 아니라 명시 표로 둔다 -- 라이브 응답으로 30개 키를 전수 확인했다.
_MARKET_INVESTOR_FLOW_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("foreign", "frgn_ntby_qty", "frgn_ntby_tr_pbmn"),
    ("foreign_registered", "frgn_reg_ntby_qty", "frgn_reg_ntby_pbmn"),
    ("foreign_unregistered", "frgn_nreg_ntby_qty", "frgn_nreg_ntby_pbmn"),
    ("individual", "prsn_ntby_qty", "prsn_ntby_tr_pbmn"),
    ("institutional", "orgn_ntby_qty", "orgn_ntby_tr_pbmn"),
    ("securities", "scrt_ntby_qty", "scrt_ntby_tr_pbmn"),
    ("investment_trust", "ivtr_ntby_qty", "ivtr_ntby_tr_pbmn"),
    ("private_equity", "pe_fund_ntby_vol", "pe_fund_ntby_tr_pbmn"),
    ("bank", "bank_ntby_qty", "bank_ntby_tr_pbmn"),
    ("insurance", "insu_ntby_qty", "insu_ntby_tr_pbmn"),
    ("merchant_bank", "mrbn_ntby_qty", "mrbn_ntby_tr_pbmn"),
    ("fund", "fund_ntby_qty", "fund_ntby_tr_pbmn"),
    ("other", "etc_ntby_qty", "etc_ntby_tr_pbmn"),
    ("other_organization", "etc_orgt_ntby_vol", "etc_orgt_ntby_tr_pbmn"),
    ("other_corporation", "etc_corp_ntby_vol", "etc_corp_ntby_tr_pbmn"),
)


_NET_BUY_MARKET = {"all": "0000", "KOSPI": "0001", "KOSDAQ": "1001"}
_NET_BUY_PARTICIPANT = {
    "foreign": "frgn", "institutional": "orgn", "investment_trust": "ivtr",
    "bank": "bank", "insurance": "insu", "merchant_bank": "mrbn", "fund": "fund",
    "other_organization": "etc_orgt", "other_corporation": "etc_corp",
}


_PROGRAM_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/investor-program-trade-today"
_PROGRAM_INVESTOR_TR = "HHPPG046600C1"


_PROGRAM_SUMMARY_PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
_PROGRAM_SUMMARY_TR = "FHPPG04600001"
#: 시장 -> FID_MRKT_CLS_CODE.
_PROGRAM_MARKET = {"KOSPI": "K", "KOSDAQ": "Q"}


_VI_STATUS_PATH = "/uapi/domestic-stock/v1/quotations/inquire-vi-status"
_VI_STATUS_TR = "FHPST01390000"


_LIMIT_PATH = "/uapi/domestic-stock/v1/quotations/capture-uplowprice"
_LIMIT_TR = "FHKST130000C0"


_PROGRAM_FLOW_PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"
_PROGRAM_FLOW_TR = "FHPPG04600101"


_CALENDAR_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"
_CALENDAR_TR = "CTCA0903R"
_FUTURES_SCHEDULE_PATH = "/uapi/domestic-stock/v1/quotations/market-time"
_FUTURES_SCHEDULE_TR = "HHMCM000002C0"


_NEWS_PATH = "/uapi/domestic-stock/v1/quotations/news-title"
_NEWS_TR = "FHKST01011800"


_FOREIGN_BROKER_PATH = "/uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate"
_FOREIGN_BROKER_TR = "FHKST644100C0"
_FOREIGN_BROKER_SORT = {"amount": "0", "volume": "1"}


_MARKET_FUNDS_PATH = "/uapi/domestic-stock/v1/quotations/mktfunds"
_MARKET_FUNDS_TR = "FHKST649100C0"

_INTEREST_RATES_PATH = "/uapi/domestic-stock/v1/quotations/comp-interest"
_INTEREST_RATES_TR = "FHPST07020000"


def fetch_lendable_stocks(
    transport: Transport, *, market: str = "all", symbol: str = ""
) -> list[LendableStock]:
    """회사 대주 가능 종목과 한도·사용·가능수량 목록."""
    market_code = _LENDABLE_MARKET.get(market)
    if market_code is None:
        raise KISUsageError(f"market 은 {sorted(_LENDABLE_MARKET)} 중 하나: {market!r}")
    params = {
        "EXCG_DVSN_CD": market_code,
        "PDNO": symbol,
        "THCO_STLN_PSBL_YN": "Y",
        "INQR_DVSN_1": "0",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK100": "",
    }
    resp = transport.request(
        method="GET", path=_LENDABLE_PATH, tr_id=_LENDABLE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output1", resp)
    stocks: list[LendableStock] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output1[]", resp)
        stocks.append(
            LendableStock(
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                par_value=required_decimal(row.get("papr"), "papr"),
                previous_close=required_decimal(row.get("bfdy_clpr"), "bfdy_clpr"),
                substitute_value=required_decimal(row.get("sbst_prvs"), "sbst_prvs"),
                trading_status=str(row.get("tr_stop_dvsn_name", "")).strip(),
                availability=str(row.get("psbl_yn_name", "")).strip(),
                limit_quantity=required_int(row.get("lmt_qty1"), "lmt_qty1"),
                used_quantity=required_int(row.get("use_qty1"), "use_qty1"),
                available_quantity=required_int(row.get("trad_psbl_qty2"), "trad_psbl_qty2"),
                rights_type=str(row.get("rght_type_cd", "")).strip(),
                # 일부 대주가능 종목은 기준일(bass_dt)이 비어 오므로 optional 로 둔다.
                base_date=parse_optional_kst_date(row.get("bass_dt")),
                is_lendable=str(row.get("psbl_yn", "")).strip() == "Y",
                _raw=row,
            )
        )
    return stocks


def fetch_credit_eligible_stocks(
    transport: Transport,
    *,
    market: str = "all",
    eligible: bool = True,
    sort: str = "name",
) -> list[CreditEligibleStock]:
    """회사 신용주문 가능·불가 종목과 신용비율 목록(최대 100건)."""
    market_code = _CREDIT_MARKET.get(market)
    if market_code is None:
        raise KISUsageError(f"market 은 {sorted(_CREDIT_MARKET)} 중 하나: {market!r}")
    sort_code = {"symbol": "0", "name": "1"}.get(sort)
    if sort_code is None:
        raise KISUsageError(f"sort 는 'symbol' 또는 'name': {sort!r}")
    params = {
        "fid_rank_sort_cls_code": sort_code,
        "fid_slct_yn": "0" if eligible else "1",
        "fid_input_iscd": market_code,
        "fid_cond_scr_div_code": "20477",
        "fid_cond_mrkt_div_code": "J",
    }
    resp = transport.request(
        method="GET", path=_CREDIT_ELIGIBLE_PATH, tr_id=_CREDIT_ELIGIBLE_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list):
        raise _missing_block_error("output", resp)
    if not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output[]", resp)
    return [
        CreditEligibleStock(
            symbol=str(row.get("stck_shrn_iscd", "")).strip(),
            name=str(row.get("hts_kor_isnm", "")).strip(),
            credit_rate=required_decimal(row.get("crdt_rate"), "crdt_rate"),
            is_eligible=eligible,
            _raw=row,
        )
        for row in rows
    ]


def fetch_broker_opinions(
    transport: Transport,
    *,
    broker: str,
    opinion: str = "all",
    start: str | date | None = None,
    end: str | date | None = None,
) -> list[BrokerOpinion]:
    """한 증권사가 낸 여러 종목 투자의견(한 호출 최대 20건)."""
    if not broker.strip():
        raise KISUsageError("broker 회원사코드가 필요하다.")
    opinion_code = {"all": "0", "buy": "1", "neutral": "2", "sell": "3"}.get(opinion)
    if opinion_code is None:
        raise KISUsageError(f"opinion 은 all/buy/neutral/sell 중 하나: {opinion!r}")
    start_date, end_date = _resolve_date_range(start=start, end=end)
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "16634",
        "FID_INPUT_ISCD": broker.strip(),
        "FID_DIV_CLS_CODE": opinion_code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
    }
    resp = transport.request(
        method="GET", path=_BROKER_OPINIONS_PATH, tr_id=_BROKER_OPINIONS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
    opinions: list[BrokerOpinion] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error("output[]", resp)
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        opinions.append(
            BrokerOpinion(
                date=_parse_kst_date(str(row.get("stck_bsop_date", "")).strip()),
                symbol=str(row.get("stck_shrn_iscd", "")).strip(),
                name=str(row.get("hts_kor_isnm", "")).strip(),
                broker=str(row.get("mbcr_name", "")).strip(),
                opinion=str(row.get("invt_opnn", "")).strip(),
                opinion_code=str(row.get("invt_opnn_cls_code", "")).strip(),
                previous_opinion=str(row.get("rgbf_invt_opnn", "")).strip(),
                previous_opinion_code=str(row.get("rgbf_invt_opnn_cls_code", "")).strip(),
                price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
                change=_apply_change_sign(
                    required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign
                ),
                target_price=optional_decimal(row.get("hts_goal_prc"), "hts_goal_prc"),
                previous_close=required_decimal(row.get("stck_prdy_clpr"), "stck_prdy_clpr"),
                disparity_rate=optional_decimal(row.get("dprt"), "dprt"),
                _raw=row,
            )
        )
    return opinions


def fetch_market_investor_flows(
    transport: Transport, *, market: Market = "KOSPI", as_of: str | date | None = None,
) -> list[MarketInvestorFlow]:
    """시장(코스피/코스닥) 전체의 투자자 순매수 최근 히스토리(``as_of`` 기준일에서 과거로).

    이 엔드포인트는 기준일 하나(FID_INPUT_DATE_1)에서 뒤로 고정 히스토리를 주므로 기간이 아니라
    앵커 날짜를 받는다(KIS 명세: DATE_2 는 "DATE_1 과 동일날짜 입력"). ``as_of`` 없으면 오늘 기준."""
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
        "FID_INPUT_DATE_2": anchor,     # KIS 명세: DATE_1 과 동일날짜(앵커에서 백워드 히스토리)
        "FID_INPUT_ISCD_2": index_code,
    }
    resp = transport.request(
        method="GET", path=_INVESTOR_BY_MARKET_PATH, tr_id=_INVESTOR_BY_MARKET_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
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
                participants={name: InvestorNetActivity(
                    net_buy_volume=required_int(row.get(qty_key), qty_key),
                    net_buy_amount=required_decimal(row.get(amt_key), amt_key),
                ) for name, qty_key, amt_key in _MARKET_INVESTOR_FLOW_FIELDS},
                _raw=row,
            )
        )
    return flows


def fetch_market_investor_snapshot(
    transport: Transport, *, market_code: str, industry_code: str
) -> MarketInvestorSnapshot:
    """한 시장·업종의 세부 투자자 매수·매도·순매수 총량."""
    if not market_code.strip():
        raise KISUsageError("market_code 가 필요하다.")
    if not industry_code.strip():
        raise KISUsageError("industry_code 가 필요하다.")
    resp = transport.request(
        method="GET", path=_INVESTOR_SNAPSHOT_PATH, tr_id=_INVESTOR_SNAPSHOT_TR,
        params={"FID_INPUT_ISCD": market_code.strip(),
                "FID_INPUT_ISCD_2": industry_code.strip()}, idempotent=True,
    )
    _raise_if_error(resp)
    row = resp.body.get("output")
    if isinstance(row, list):            # KIS 는 이 단일 스냅샷을 1-원소 리스트로 감싸 주기도 한다
        row = row[0] if row else None
    if not isinstance(row, Mapping):
        raise _missing_block_error("output", resp)
    return MarketInvestorSnapshot(
        market_code=market_code.strip(), industry_code=industry_code.strip(),
        participants={name: _parse_market_investor_activity(row, prefix)
                      for name, prefix in _INVESTOR_SNAPSHOT_PREFIX.items()},
        _raw=row,
    )


def fetch_investor_net_buy_stocks(
    transport: Transport, *, market: str = "all", basis: str = "volume",
    direction: str = "buy", investor: str = "all",
) -> list[InvestorNetBuyStock]:
    """투자자 순매수·순매도 상위 종목 집계."""
    market_code = _NET_BUY_MARKET.get(market)
    basis_code = {"volume": "0", "amount": "1"}.get(basis)
    direction_code = {"buy": "0", "sell": "1"}.get(direction)
    investor_code = {"all": "0", "foreign": "1", "institutional": "2", "other": "3"}.get(investor)
    if market_code is None:
        raise KISUsageError(f"market 은 {sorted(_NET_BUY_MARKET)} 중 하나여야 한다: {market!r}")
    if basis_code is None:
        raise KISUsageError(f"basis 는 'volume'/'amount' 중 하나여야 한다: {basis!r}")
    if direction_code is None:
        raise KISUsageError(f"direction 은 'buy'/'sell' 중 하나여야 한다: {direction!r}")
    if investor_code is None:
        raise KISUsageError(
            f"investor 는 'all'/'foreign'/'institutional'/'other' 중 하나여야 한다: {investor!r}"
        )
    resp = transport.request(
        method="GET", path=_INVESTOR_NET_BUY_PATH, tr_id=_INVESTOR_NET_BUY_TR,
        params={"FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": market_code,
                "FID_DIV_CLS_CODE": basis_code, "FID_RANK_SORT_CLS_CODE": direction_code,
                "FID_ETC_CLS_CODE": investor_code}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output", resp)
    stocks = []
    for row in rows:
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        stocks.append(InvestorNetBuyStock(
            symbol=str(row.get("mksc_shrn_iscd", "")).strip(),
            name=str(row.get("hts_kor_isnm", "")).strip(),
            net_buy_volume=required_int(row.get("ntby_qty"), "ntby_qty"),
            price=required_decimal(row.get("stck_prpr"), "stck_prpr"),
            change=_apply_change_sign(required_decimal(row.get("prdy_vrss"), "prdy_vrss"), sign),
            change_percent=_apply_change_sign(required_decimal(row.get("prdy_ctrt"), "prdy_ctrt"), sign),
            volume=required_int(row.get("acml_vol"), "acml_vol"),
            participants={name: InvestorNetActivity(
                net_buy_volume=required_int(row.get(f"{prefix}_ntby_vol" if prefix in {"etc_orgt", "etc_corp"} else f"{prefix}_ntby_qty"),
                                      f"{prefix}_ntby_vol" if prefix in {"etc_orgt", "etc_corp"} else f"{prefix}_ntby_qty"),
                net_buy_amount=required_decimal(row.get(f"{prefix}_ntby_tr_pbmn"), f"{prefix}_ntby_tr_pbmn"),
            ) for name, prefix in _NET_BUY_PARTICIPANT.items()}, _raw=row,
        ))
    return stocks


def fetch_program_investor_trades(
    transport: Transport, *, market: Market = "KOSPI"
) -> list[ProgramInvestorTrade]:
    """시장별 당일 프로그램매매 투자자 집계."""
    market_code = {"KOSPI": "1", "KOSDAQ": "4"}.get(market)
    if market_code is None:
        raise KISUsageError("market 은 KOSPI 또는 KOSDAQ 이어야 한다.")
    resp = transport.request(
        method="GET", path=_PROGRAM_INVESTOR_PATH, tr_id=_PROGRAM_INVESTOR_TR,
        # EXCH_DIV_CLS_CODE(필수) J:KRX, NX:NXT, UN:통합 -- 다른 프로그램 조회와 맞춰 KRX.
        params={"MRKT_DIV_CLS_CODE": market_code, "EXCH_DIV_CLS_CODE": "J"}, idempotent=True,
    )
    _raise_if_error(resp)
    rows = resp.body.get("output1")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise _missing_block_error("output1", resp)
    return [ProgramInvestorTrade(
        investor_code=str(row.get("invr_cls_code", "")).strip(),
        investor_name=str(row.get("invr_cls_name", "")).strip(),
        total=_parse_program_trade_activity(row, "all"),
        arbitrage=_parse_program_trade_activity(row, "arbt"),
        nonarbitrage=_parse_program_trade_activity(row, "nabt"), _raw=row,
    ) for row in rows]


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
    start_date, end_date = _resolve_date_range(start=start, end=end)
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
    rows = _require_mapping_rows("output", resp)
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
                nonarbitrage_net_volume=required_int(row.get("nabt_smtn_ntby_qty"),
                                                     "nabt_smtn_ntby_qty"),
                nonarbitrage_net_amount=required_decimal(row.get("nabt_smtn_ntby_tr_pbmn"),
                                                         "nabt_smtn_ntby_tr_pbmn"),
                _raw=row,
            )
        )
    return summaries


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
    rows = _require_mapping_rows("output", resp)
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
                disparity_rate=optional_decimal(row.get("vi_dprt"), "vi_dprt"),
                daily_trigger_count=required_int(row.get("vi_count"), "vi_count"),
                _raw=row,
            )
        )
    return events


def fetch_limit_stocks(transport: Transport) -> list[LimitStock]:
    """상한가/하한가에 도달한 종목 전체 스냅샷(전 시장)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "11300",
        "FID_PRC_CLS_CODE": "0",           # 0: 상하한가 전체
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_ISCD": "0000",
        "FID_TRGT_CLS_CODE": "",           # 대상구분코드(전체) -- KIS 가 키 존재를 요구
        "FID_TRGT_EXLS_CLS_CODE": "",      # 대상제외구분코드(없음)
        "FID_INPUT_PRICE_1": "",           # 가격 하한(없음)
        "FID_INPUT_PRICE_2": "",           # 가격 상한(없음)
        "FID_VOL_CNT": "",                 # 거래량 하한(없음)
    }
    resp = transport.request(
        method="GET", path=_LIMIT_PATH, tr_id=_LIMIT_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
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


def fetch_program_flow(
    transport: Transport, *, market: Market = "KOSPI"
) -> list[ProgramFlowPoint]:
    """당일 시간대별 프로그램매매 순매수 대금(시간 순). 시장(코스피/코스닥)."""
    try:
        market_code = _PROGRAM_MARKET[market]
    except KeyError:
        raise KISUsageError(f"market 은 {sorted(_PROGRAM_MARKET)} 중 하나: {market!r}") from None
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_MRKT_CLS_CODE": market_code,
        "FID_SCTN_CLS_CODE": "",            # 구간구분(없음) -- KIS 가 키 존재를 요구
        "FID_INPUT_ISCD": "",              # 입력종목코드(없음)
        "FID_COND_MRKT_DIV_CODE1": "",     # 시장분류코드(없음)
        "FID_INPUT_HOUR_1": "",            # 입력시간(없음)
    }
    resp = transport.request(
        method="GET", path=_PROGRAM_FLOW_PATH, tr_id=_PROGRAM_FLOW_TR, params=params,
        idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
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
                nonarbitrage_net_amount=required_decimal(
                    row.get("nabt_smtn_ntby_tr_pbmn"), "nabt_smtn_ntby_tr_pbmn"
                ),
                total_net_amount=required_decimal(
                    row.get("whol_smtn_ntby_tr_pbmn"), "whol_smtn_ntby_tr_pbmn"
                ),
                _raw=row,
            )
        )
    return points


def fetch_trading_calendar(
    transport: Transport, *, base_date: str | date | None = None
) -> list[TradingDay]:
    """거래 캘린더(``base_date`` 기준일에서 앞으로). 각 날짜의 영업/거래/개장/결제 여부.
    ``base_date`` 없으면 오늘. KIS 국내휴장일조회(CTCA0903R)는 연속조회를 지원하지 않아
    (CTX_AREA 공백 고정) 한 응답에 기준일 이후 구간을 모두 싣는다 -- 잘린 페이지가 아니다."""
    base = _today_kst() if base_date is None else _to_yyyymmdd(base_date, "base_date")
    params = {"BASS_DT": base, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
    resp = transport.request(
        method="GET", path=_CALENDAR_PATH, tr_id=_CALENDAR_TR, params=params, idempotent=True
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
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


def fetch_futures_market_schedule(transport: Transport) -> FuturesMarketSchedule:
    """국내선물의 인접 영업일 5개와 오늘 장 운영 시각."""
    resp = transport.request(
        method="GET",
        path=_FUTURES_SCHEDULE_PATH,
        tr_id=_FUTURES_SCHEDULE_TR,
        params={},
        idempotent=True,
    )
    _raise_if_error(resp)
    output = resp.body.get("output1")
    if not isinstance(output, Mapping):
        raise _missing_block_error("output1", resp)
    business_days = tuple(
        _parse_kst_date(str(output.get(f"date{position}", "")).strip())
        for position in range(1, 6)
    )
    today_text = str(output.get("today", "")).strip()
    today = _parse_kst_date(today_text)
    as_of = _parse_bar_timestamp(today_text)
    return FuturesMarketSchedule(
        business_days=business_days,
        today=today,
        current_time=_parse_intraday_timestamp(
            str(output.get("time", "")).strip(), as_of
        ),
        opens_at=_parse_intraday_timestamp(
            str(output.get("s_time", "")).strip(), as_of
        ),
        closes_at=_parse_intraday_timestamp(
            str(output.get("e_time", "")).strip(), as_of
        ),
        _raw=output,
    )


def fetch_news(
    transport: Transport, *, symbol: str = "", date_: str | date | None = None
) -> list[NewsHeadline]:
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
    rows = _require_mapping_rows("output", resp)
    items: list[NewsHeadline] = []
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
            NewsHeadline(
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


def fetch_foreign_broker_flows(
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
    rows = _require_mapping_rows("output", resp)
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


def fetch_interest_rates(transport: Transport) -> list[InterestRateQuote]:
    """국내·해외 주요 금리와 채권지수의 최신 스냅샷."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "I",
        "FID_COND_SCR_DIV_CODE": "20702",
        "FID_DIV_CLS_CODE": "1",
        "FID_DIV_CLS_CODE1": "",
    }
    resp = transport.request(
        method="GET", path=_INTEREST_RATES_PATH, tr_id=_INTEREST_RATES_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    overseas_rows = resp.body.get("output1")
    domestic_rows = resp.body.get("output2")
    if not isinstance(overseas_rows, list):
        raise _missing_block_error("output1", resp)
    if not isinstance(domestic_rows, list):
        raise _missing_block_error("output2", resp)
    quotes = _parse_interest_rates(
        overseas_rows, region="overseas", percent_field="prdy_ctrt", resp=resp
    )
    quotes.extend(
        _parse_interest_rates(
            domestic_rows, region="domestic", percent_field="bstp_nmix_prdy_ctrt", resp=resp
        )
    )
    return quotes


def fetch_market_funds(
    transport: Transport, *, as_of: str | date | None = None
) -> list[MarketFunds]:
    """증시자금 종합의 최근 일별 추이(``as_of`` 기준일에서 과거로)를 조회한다.

    고객예탁금·신용융자잔고·펀드유형별 잔고·시가총액을 시장 전체 기준으로 돌려준다.
    ``as_of`` 는 앵커 날짜이며 미지정하면 오늘을 사용하고, 응답의 최신순을 보존한다.
    KIS URL: ``GET /uapi/domestic-stock/v1/quotations/mktfunds``.
    TR-ID: ``FHKST649100C0``. 연속조회 미지원으로 한 번만 호출한다.
    """
    anchor = _today_kst() if as_of is None else _to_yyyymmdd(as_of, "as_of")
    params = {"FID_INPUT_DATE_1": anchor}
    resp = transport.request(
        method="GET", path=_MARKET_FUNDS_PATH, tr_id=_MARKET_FUNDS_TR,
        params=params, idempotent=True,
    )
    _raise_if_error(resp)
    rows = _require_mapping_rows("output", resp)
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


def _default_start(end_yyyymmdd: str, days: int = 30) -> str:
    end_day = datetime.strptime(end_yyyymmdd, "%Y%m%d")  # noqa: DTZ007 -- 날짜 산술만
    return f"{end_day - timedelta(days=days):%Y%m%d}"


def _resolve_date_range(
    *, start: str | date | None, end: str | date | None
) -> tuple[str, str]:
    """기간조회 공통 [start, end] 정규화 -- ``end`` 미지정이면 오늘, ``start`` 미지정이면 ``end`` 로부터
    30일 전으로 채운 뒤 YYYYMMDD 로 정규화한다. 뒤집힌 기간(``start > end``)은 I/O 전에 fail-closed."""
    end_date = _today_kst() if end is None else _to_yyyymmdd(end, "end")
    start_date = _default_start(end_date) if start is None else _to_yyyymmdd(start, "start")
    if start_date > end_date:                  # 뒤집힌 기간 -> I/O 전 fail-closed
        raise KISUsageError(f"start({start_date}) 가 end({end_date}) 보다 늦다.")
    return start_date, end_date


def _parse_market_investor_activity(
    row: Mapping[str, object], prefix: str
) -> InvestorActivity:
    net_key = f"{prefix}_ntby_vol" if prefix in {"pe_fund", "etc_orgt", "etc_corp"} else f"{prefix}_ntby_qty"
    return InvestorActivity(
        buy_volume=required_int(row.get(f"{prefix}_shnu_vol"), f"{prefix}_shnu_vol"),
        sell_volume=required_int(row.get(f"{prefix}_seln_vol"), f"{prefix}_seln_vol"),
        net_buy_volume=required_int(row.get(net_key), net_key),
        buy_amount=required_decimal(row.get(f"{prefix}_shnu_tr_pbmn"), f"{prefix}_shnu_tr_pbmn"),
        sell_amount=required_decimal(row.get(f"{prefix}_seln_tr_pbmn"), f"{prefix}_seln_tr_pbmn"),
        net_buy_amount=required_decimal(row.get(f"{prefix}_ntby_tr_pbmn"), f"{prefix}_ntby_tr_pbmn"),
    )


def _parse_program_trade_activity(row: Mapping[str, object], prefix: str) -> ProgramTradeActivity:
    return ProgramTradeActivity(
        sell_quantity=required_int(row.get(f"{prefix}_seln_qty"), f"{prefix}_seln_qty"),
        buy_quantity=required_int(row.get(f"{prefix}_shnu_qty"), f"{prefix}_shnu_qty"),
        net_buy_volume=required_int(row.get(f"{prefix}_ntby_qty"), f"{prefix}_ntby_qty"),
        sell_amount=required_decimal(row.get(f"{prefix}_seln_amt"), f"{prefix}_seln_amt"),
        buy_amount=required_decimal(row.get(f"{prefix}_shnu_amt"), f"{prefix}_shnu_amt"),
        net_buy_amount=required_decimal(row.get(f"{prefix}_ntby_amt"), f"{prefix}_ntby_amt"),
    )


def _parse_interest_rates(
    rows: list[object], *, region: Literal["domestic", "overseas"], percent_field: str,
    resp: RawResponse,
) -> list[InterestRateQuote]:
    quotes: list[InterestRateQuote] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise _missing_block_error(f"{region} output[]", resp)
        # KIS 는 국내(output2) 앞부분에 코드/범례 행(수치 아닌 값)을 섞어 준다 -- 실제 금리 행만 취한다.
        quote_text = str(row.get("bond_mnrt_prpr", "")).strip()
        try:
            quote_value = Decimal(quote_text) if quote_text else None
        except InvalidOperation:
            quote_value = None
        # 범례/코드 행(비숫자)은 skip. NaN/Infinity 도 저장하지 않고 skip -- 비유한값을 담으면 이후
        # 비교가 무너진다(모듈의 fail-closed 규율; 이 파서는 비데이터 행을 drop 하는 게 정상).
        if quote_value is None or not quote_value.is_finite():
            continue
        sign = str(row.get("prdy_vrss_sign", "")).strip()
        quotes.append(
            InterestRateQuote(
                code=str(row.get("bcdt_code", "")).strip(),
                name=str(row.get("hts_kor_isnm", "")).strip(),
                region=region,
                quote_value=quote_value,
                change=_apply_change_sign(
                    required_decimal(row.get("bond_mnrt_prdy_vrss"), "bond_mnrt_prdy_vrss"),
                    sign,
                ),
                change_percent=_apply_change_sign(
                    required_decimal(row.get(percent_field), percent_field), sign
                ),
                observation_date=_parse_kst_date(str(row.get("stck_bsop_date", "")).strip()),
                _raw=row,
            )
        )
    return quotes

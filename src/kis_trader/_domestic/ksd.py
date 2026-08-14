"""기업행위 캘린더 조회 (내부) -- 한국예탁결제원(KSD) 일정.

사용자면은 :class:`~kis_trader.calendar.CalendarQueries`(``kis.domestic.calendar``)다. 모든 조회가
``/uapi/domestic-stock/v1/ksdinfo/`` 아래에 있고, 기간(``F_DT`` ~ ``T_DT``) + 선택 종목(``SHT_CD``)
으로 이벤트 배열(``output1``)을 준다. 날짜는 순수 달력 날짜라 :class:`datetime.date` 로 돌려준다.

KIS URL/TR-ID:
- 배당일정: ``GET .../ksdinfo/dividend`` ``HHKDB669102C0``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .._internal._datetime import _to_yyyymmdd
from .._internal._response import (
    _raise_if_error,
    _require_mapping_rows,
)
from .._internal._wire import optional_decimal, optional_int
from ..calendar_items import (
    AppraisalRights,
    BonusIssue,
    CapitalReduction,
    DividendEvent,
    ForfeitedShares,
    IPOSubscription,
    ListingEvent,
    MandatoryDeposit,
    MergerSplit,
    ParValueChange,
    RightsOffering,
    ShareholderMeeting,
)
from ..errors import KISError, KISUsageError
from ..transport import Transport

_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ksdinfo/dividend"
_DIVIDEND_TR = "HHKDB669102C0"
_IPO_PATH = "/uapi/domestic-stock/v1/ksdinfo/pub-offer"
_IPO_TR = "HHKDB669108C0"
_RIGHTS_PATH = "/uapi/domestic-stock/v1/ksdinfo/paidin-capin"
_RIGHTS_TR = "HHKDB669100C0"
_BONUS_PATH = "/uapi/domestic-stock/v1/ksdinfo/bonus-issue"
_BONUS_TR = "HHKDB669101C0"
_CAPITAL_REDUCTION_PATH = "/uapi/domestic-stock/v1/ksdinfo/cap-dcrs"
_CAPITAL_REDUCTION_TR = "HHKDB669106C0"
_MERGER_SPLIT_PATH = "/uapi/domestic-stock/v1/ksdinfo/merger-split"
_MERGER_SPLIT_TR = "HHKDB669104C0"
_SHAREHOLDER_MEETING_PATH = "/uapi/domestic-stock/v1/ksdinfo/sharehld-meet"
_SHAREHOLDER_MEETING_TR = "HHKDB669111C0"
_MANDATORY_DEPOSIT_PATH = "/uapi/domestic-stock/v1/ksdinfo/mand-deposit"
_MANDATORY_DEPOSIT_TR = "HHKDB669110C0"
_LISTING_INFO_PATH = "/uapi/domestic-stock/v1/ksdinfo/list-info"
_LISTING_INFO_TR = "HHKDB669107C0"
_PAR_VALUE_CHANGE_PATH = "/uapi/domestic-stock/v1/ksdinfo/rev-split"
_PAR_VALUE_CHANGE_TR = "HHKDB669105C0"
_FORFEITED_SHARES_PATH = "/uapi/domestic-stock/v1/ksdinfo/forfeit"
_FORFEITED_SHARES_TR = "HHKDB669109C0"
_APPRAISAL_RIGHTS_PATH = "/uapi/domestic-stock/v1/ksdinfo/purreq"
_APPRAISAL_RIGHTS_TR = "HHKDB669103C0"

#: 배당 조회구분(GB1). KIS 명세: 0(배당전체), 1(결산배당), 2(중간배당).
_DIVIDEND_KIND = {"all": "0", "final": "1", "interim": "2"}
#: 유상증자 조회구분(GB1). KIS 명세: 1(청약일별), 2(기준일별).
_RIGHTS_BASIS = {"subscription": "1", "record": "2"}


def _parse_ksd_date(value: object, *, required: bool, name: str) -> date | None:
    """KSD 날짜 -> date. KSD는 "YYYYMMDD" 와 "YYYY/MM/DD" 를 섞어 주므로 구분자를 벗겨 통일한다.
    빈 값/"00000000"(미정 sentinel)은 ``None``(required 면 예외). 형식이 깨지면 fail-closed."""
    text = str(value).strip().replace("/", "").replace(".", "").replace("-", "")
    if not text or text == "00000000":
        if required:
            raise KISError(f"필수 날짜 필드 {name!r} 가 비어 있다: {value!r}")
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()  # noqa: DTZ007 -- 달력 날짜, 시각/tz 없음
    except ValueError as err:
        raise KISError(f"날짜 필드 {name!r} 파싱 실패: {value!r}") from err


def _rows(transport: Transport, *, path: str, tr: str, params: Mapping[str, str],
          block: str = "output1") -> list[Mapping[str, Any]]:
    resp = transport.request(method="GET", path=path, tr_id=tr, params=dict(params),
                             idempotent=True)
    _raise_if_error(resp)
    return _require_mapping_rows(block, resp)


def _ksd_query_params(
    *, start: str | date, end: str | date, symbol: str | None = None
) -> dict[str, str]:
    """KSD 기간조회 공통 쿼리 파라미터 -- 고정 ``CTS`` + 기간 ``F_DT``~``T_DT`` + 선택 종목 ``SHT_CD``.
    이 키 순서를 그대로 공유하는 엔드포인트들이 이 base 를 쓴다(키 순서가 다르거나 ``GB1`` 등 중간
    삽입 키가 있는 조회는 순서 민감성 때문에 자체 dict 를 유지한다)."""
    return {
        "CTS": "",
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
        "SHT_CD": symbol or "",
    }


def fetch_dividends(
    transport: Transport, *, start: str | date, end: str | date,
    symbol: str | None = None, dividend_kind: str = "all",
) -> list[DividendEvent]:
    """기간 [start, end] 의 배당 일정. ``symbol`` 지정 시 그 종목만, ``dividend_kind`` 는 all/final/interim."""
    try:
        gb1 = _DIVIDEND_KIND[dividend_kind]
    except KeyError:
        raise KISUsageError(
            f"dividend_kind 는 {sorted(_DIVIDEND_KIND)} 중 하나: {dividend_kind!r}"
        ) from None
    params = {
        "CTS": "",
        "GB1": gb1,
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
        "SHT_CD": symbol or "",
        "HIGH_GB": "",
    }
    events: list[DividendEvent] = []
    for row in _rows(transport, path=_DIVIDEND_PATH, tr=_DIVIDEND_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        high = str(row.get("high_divi_gb", "")).strip().upper() in {"Y", "1"}
        events.append(
            DividendEvent(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                dividend_kind=str(row.get("divi_kind", "")).strip(),
                face_value=optional_decimal(row.get("face_val"), "face_val"),
                cash_dividend=optional_decimal(row.get("per_sto_divi_amt"), "per_sto_divi_amt"),
                cash_dividend_rate=optional_decimal(row.get("divi_rate"), "divi_rate"),
                stock_dividend_rate=optional_decimal(row.get("stk_divi_rate"), "stk_divi_rate"),
                cash_pay_date=_parse_ksd_date(row.get("divi_pay_dt"), required=False,
                                              name="divi_pay_dt"),
                stock_pay_date=_parse_ksd_date(row.get("stk_div_pay_dt"), required=False,
                                               name="stk_div_pay_dt"),
                odd_lot_pay_date=_parse_ksd_date(row.get("odd_pay_dt"), required=False,
                                                 name="odd_pay_dt"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                high_dividend=high,
                _raw=row,
            )
        )
    return events


def fetch_ipo_subscriptions(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[IPOSubscription]:
    """기간 [start, end] 의 공모주 청약 일정. ``symbol`` 지정 시 그 종목만."""
    params = {
        "SHT_CD": symbol or "",
        "CTS": "",
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
    }
    events: list[IPOSubscription] = []
    for row in _rows(transport, path=_IPO_PATH, tr=_IPO_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            IPOSubscription(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                offer_price=optional_decimal(row.get("fix_subscr_pri"), "fix_subscr_pri"),
                face_value=optional_decimal(row.get("face_value"), "face_value"),
                subscription_period=str(row.get("subscr_dt", "")).strip(),
                pay_date=_parse_ksd_date(row.get("pay_dt"), required=False, name="pay_dt"),
                refund_date=_parse_ksd_date(row.get("refund_dt"), required=False, name="refund_dt"),
                list_date=_parse_ksd_date(row.get("list_dt"), required=False, name="list_dt"),
                lead_manager=str(row.get("lead_mgr", "")).strip(),
                capital_before=optional_decimal(row.get("pub_bf_cap"), "pub_bf_cap"),
                capital_after=optional_decimal(row.get("pub_af_cap"), "pub_af_cap"),
                allocated_quantity=optional_int(row.get("assign_stk_qty"), "assign_stk_qty"),
                _raw=row,
            )
        )
    return events


def fetch_rights_offerings(
    transport: Transport, *, start: str | date, end: str | date,
    symbol: str | None = None, offering_date_basis: str = "subscription",
) -> list[RightsOffering]:
    """기간 [start, end] 의 유상증자 일정. ``offering_date_basis`` 는 조회 기준 -- ``"subscription"``(청약일별) /
    ``"record"``(기준일별). ``symbol`` 지정 시 그 종목만."""
    try:
        gb1 = _RIGHTS_BASIS[offering_date_basis]
    except KeyError:
        raise KISUsageError(
            f"offering_date_basis 는 {sorted(_RIGHTS_BASIS)} 중 하나: {offering_date_basis!r}"
        ) from None
    params = {
        "CTS": "",
        "GB1": gb1,
        "F_DT": _to_yyyymmdd(start, "start"),
        "T_DT": _to_yyyymmdd(end, "end"),
        "SHT_CD": symbol or "",
    }
    events: list[RightsOffering] = []
    for row in _rows(transport, path=_RIGHTS_PATH, tr=_RIGHTS_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            RightsOffering(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                total_shares=optional_int(row.get("tot_issue_stk_qty"), "tot_issue_stk_qty"),
                new_shares=optional_int(row.get("issue_stk_qty"), "issue_stk_qty"),
                allocation_rate=optional_decimal(row.get("fix_rate"), "fix_rate"),
                discount_rate=optional_decimal(row.get("disc_rate"), "disc_rate"),
                issue_price=optional_decimal(row.get("fix_price"), "fix_price"),
                ex_rights_date=_parse_ksd_date(row.get("right_dt"), required=False,
                                               name="right_dt"),
                subscription_start=_parse_ksd_date(row.get("sub_term_ft"), required=False,
                                                   name="sub_term_ft"),
                subscription_period=str(row.get("sub_term", "")).strip(),
                list_date=_parse_ksd_date(row.get("list_date"), required=False, name="list_date"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                _raw=row,
            )
        )
    return events


def fetch_bonus_issues(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[BonusIssue]:
    """기간 [start, end] 의 무상증자 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[BonusIssue] = []
    for row in _rows(transport, path=_BONUS_PATH, tr=_BONUS_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            BonusIssue(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                allocation_rate=optional_decimal(row.get("fix_rate"), "fix_rate"),
                odd_lot_base_price=optional_decimal(row.get("odd_rec_price"), "odd_rec_price"),
                ex_rights_date=_parse_ksd_date(row.get("right_dt"), required=False,
                                               name="right_dt"),
                odd_lot_pay_date=_parse_ksd_date(row.get("odd_pay_dt"), required=False,
                                                 name="odd_pay_dt"),
                list_date=_parse_ksd_date(row.get("list_date"), required=False, name="list_date"),
                total_shares=optional_int(row.get("tot_issue_stk_qty"), "tot_issue_stk_qty"),
                new_shares=optional_int(row.get("issue_stk_qty"), "issue_stk_qty"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                _raw=row,
            )
        )
    return events


def fetch_capital_reductions(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[CapitalReduction]:
    """기간 [start, end] 의 자본감소 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[CapitalReduction] = []
    for row in _rows(
        transport, path=_CAPITAL_REDUCTION_PATH, tr=_CAPITAL_REDUCTION_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            CapitalReduction(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                reduction_type=str(row.get("reduce_cap_type", "")).strip(),
                reduction_rate=optional_decimal(row.get("reduce_cap_rate"), "reduce_cap_rate"),
                computation_method=str(row.get("comp_way", "")).strip(),
                trading_halt_period=str(row.get("td_stop_dt", "")).strip(),
                list_date=_parse_ksd_date(row.get("list_dt"), required=False, name="list_dt"),
                _raw=row,
            )
        )
    return events


def fetch_merger_splits(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[MergerSplit]:
    """기간 [start, end] 의 합병분할 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[MergerSplit] = []
    for row in _rows(transport, path=_MERGER_SPLIT_PATH, tr=_MERGER_SPLIT_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            MergerSplit(
                symbol=code,
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                company_code=str(row.get("cust_cd", "")).strip(),
                company_name=str(row.get("cust_nm", "")).strip(),
                counterparty_code=str(row.get("opp_cust_cd", "")).strip(),
                counterparty_name=str(row.get("opp_cust_nm", "")).strip(),
                merge_type=str(row.get("merge_type", "")).strip(),
                merge_ratio=optional_decimal(row.get("merge_rate"), "merge_rate"),
                trading_halt_period=str(row.get("td_stop_dt", "")).strip(),
                list_date=_parse_ksd_date(row.get("list_dt"), required=False, name="list_dt"),
                odd_lot_pay_date=_parse_ksd_date(row.get("odd_amt_pay_dt"), required=False,
                                                 name="odd_amt_pay_dt"),
                total_shares=optional_int(row.get("tot_issue_stk_qty"), "tot_issue_stk_qty"),
                new_shares=optional_int(row.get("issue_stk_qty"), "issue_stk_qty"),
                sequence=str(row.get("seq", "")).strip(),
                _raw=row,
            )
        )
    return events


def fetch_shareholder_meetings(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[ShareholderMeeting]:
    """기간 [start, end] 의 주주총회 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[ShareholderMeeting] = []
    for row in _rows(
        transport, path=_SHAREHOLDER_MEETING_PATH, tr=_SHAREHOLDER_MEETING_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            ShareholderMeeting(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                meeting_date=_parse_ksd_date(row.get("gen_meet_dt"), required=False,
                                             name="gen_meet_dt"),
                meeting_type=str(row.get("gen_meet_type", "")).strip(),
                agenda=str(row.get("agenda", "")).strip(),
                voting_shares=optional_int(row.get("vote_tot_qty"), "vote_tot_qty"),
                _raw=row,
            )
        )
    return events


def fetch_mandatory_deposits(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[MandatoryDeposit]:
    """기간 [start, end] 의 의무예치 내역. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[MandatoryDeposit] = []
    for row in _rows(
        transport, path=_MANDATORY_DEPOSIT_PATH, tr=_MANDATORY_DEPOSIT_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            MandatoryDeposit(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                deposit_shares=optional_int(row.get("stk_qty"), "stk_qty"),
                deposit_period=str(row.get("depo_date", "")).strip(),
                deposit_reason=str(row.get("depo_reason", "")).strip(),
                issued_shares_ratio=optional_decimal(
                    row.get("tot_issue_qty_per_rate"), "tot_issue_qty_per_rate"
                ),
                _raw=row,
            )
        )
    return events


def fetch_listings(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[ListingEvent]:
    """기간 [start, end] 의 상장정보. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[ListingEvent] = []
    for row in _rows(transport, path=_LISTING_INFO_PATH, tr=_LISTING_INFO_TR, params=params):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            ListingEvent(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                list_date=_parse_ksd_date(row.get("list_dt"), required=True, name="list_dt"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                issue_type=str(row.get("issue_type", "")).strip(),
                new_shares=optional_int(row.get("issue_stk_qty"), "issue_stk_qty"),
                total_shares=optional_int(row.get("tot_issue_stk_qty"), "tot_issue_stk_qty"),
                issue_price=optional_decimal(row.get("issue_price"), "issue_price"),
                _raw=row,
            )
        )
    return events


def fetch_par_value_changes(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[ParValueChange]:
    """기간 [start, end] 의 액면교체 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[ParValueChange] = []
    for row in _rows(
        transport, path=_PAR_VALUE_CHANGE_PATH, tr=_PAR_VALUE_CHANGE_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            ParValueChange(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                face_value_before=optional_decimal(
                    row.get("inter_bf_face_amt"), "inter_bf_face_amt"
                ),
                face_value_after=optional_decimal(
                    row.get("inter_af_face_amt"), "inter_af_face_amt"
                ),
                trading_halt_period=str(row.get("td_stop_dt", "")).strip(),
                list_date=_parse_ksd_date(row.get("list_dt"), required=False, name="list_dt"),
                _raw=row,
            )
        )
    return events


def fetch_forfeited_shares(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[ForfeitedShares]:
    """기간 [start, end] 의 실권주 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[ForfeitedShares] = []
    for row in _rows(
        transport, path=_FORFEITED_SHARES_PATH, tr=_FORFEITED_SHARES_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            ForfeitedShares(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                subscription_period=str(row.get("subscr_dt", "")).strip(),
                subscription_price=optional_decimal(row.get("subscr_price"), "subscr_price"),
                subscription_shares=optional_int(row.get("subscr_stk_qty"), "subscr_stk_qty"),
                refund_date=_parse_ksd_date(row.get("refund_dt"), required=False,
                                            name="refund_dt"),
                list_date=_parse_ksd_date(row.get("list_dt"), required=False, name="list_dt"),
                lead_manager=str(row.get("lead_mgr", "")).strip(),
                _raw=row,
            )
        )
    return events


def fetch_appraisal_rights(
    transport: Transport, *, start: str | date, end: str | date, symbol: str | None = None
) -> list[AppraisalRights]:
    """기간 [start, end] 의 주식매수청구 일정. ``symbol`` 지정 시 그 종목만."""
    params = _ksd_query_params(start=start, end=end, symbol=symbol)
    events: list[AppraisalRights] = []
    for row in _rows(
        transport, path=_APPRAISAL_RIGHTS_PATH, tr=_APPRAISAL_RIGHTS_TR, params=params
    ):
        code = str(row.get("sht_cd", "")).strip()
        if not code:
            continue
        events.append(
            AppraisalRights(
                symbol=code,
                name=str(row.get("isin_name", "")).strip(),
                record_date=_parse_ksd_date(row.get("record_date"), required=True,
                                            name="record_date"),
                stock_kind=str(row.get("stk_kind", "")).strip(),
                opposition_period=str(row.get("opp_opi_rcpt_term", "")).strip(),
                buyback_request_period=str(row.get("buy_req_rcpt_term", "")).strip(),
                buyback_price=optional_decimal(row.get("buy_req_price"), "buy_req_price"),
                payment_date=_parse_ksd_date(row.get("buy_amt_pay_dt"), required=False,
                                             name="buy_amt_pay_dt"),
                meeting_date=_parse_ksd_date(row.get("get_meet_dt"), required=False,
                                             name="get_meet_dt"),
                _raw=row,
            )
        )
    return events

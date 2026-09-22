"""기업행위 캘린더 -- kis.domestic.calendar.dividends().

배당일정(HHKDB669102C0)의 TR/URL·기간·종목·구분 파라미터·필드 매핑(zero/space padding, 날짜
sentinel)·fail-closed 를 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import DividendEvent, KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse


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


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output1": rows})


def test_dividends_maps_and_params():
    # 원장 응답 예시값(현대건설 결산배당; zero-pad face_val, space-pad divi_rate, 지급일 미정=빈값).
    rows = [{"record_date": "20240326", "sht_cd": "000720", "isin_name": "현대건설",
             "divi_kind": "결산", "face_val": "000005000", "per_sto_divi_amt": "000000000600",
             "divi_rate": " 12.00", "stk_divi_rate": "  0.00", "divi_pay_dt": "",
             "stk_div_pay_dt": "", "odd_pay_dt": "", "stk_kind": "보통", "high_divi_gb": ""}]
    fake = FakeTransport(response=_resp(rows))
    events = _client(fake).domestic.calendar.dividends(start="20240301", end="20240331")
    assert isinstance(events[0], DividendEvent)
    e = events[0]
    assert e.symbol == "000720"                          # 코드 문자열 유지(정수화 안 함)
    assert e.name == "현대건설"
    assert e.record_date == date(2024, 3, 26)
    assert e.face_value == Decimal(5000)                 # zero-pad 파싱
    assert e.cash_dividend == Decimal(600)
    assert e.cash_dividend_rate == Decimal("12.00")      # space-pad 파싱
    assert e.cash_pay_date is None                       # 빈 지급일 -> None
    assert e.stock_kind == "보통"
    assert e.high_dividend is False
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/dividend"
    assert call["tr_id"] == "HHKDB669102C0"
    assert call["params"]["F_DT"] == "20240301"
    assert call["params"]["T_DT"] == "20240331"
    assert call["params"]["GB1"] == "0"                  # all(기본)
    assert call["params"]["SHT_CD"] == ""                # 종목 미지정 -> 전체


def test_dividends_symbol_and_kind_filters():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.calendar.dividends(
        start=date(2024, 3, 1), end=date(2024, 3, 31), symbol="005930", dividend_kind="interim"
    )
    call = fake.calls[0]
    assert call["params"]["SHT_CD"] == "005930"
    assert call["params"]["GB1"] == "2"                  # interim


def test_dividends_high_dividend_flag_and_zero_sentinel_date():
    rows = [{"record_date": "20240326", "sht_cd": "000720", "isin_name": "X", "divi_kind": "결산",
             "face_val": "5000", "per_sto_divi_amt": "600", "divi_rate": "12.0",
             "stk_divi_rate": "0.0", "divi_pay_dt": "00000000", "stk_div_pay_dt": "",
             "odd_pay_dt": "", "stk_kind": "보통", "high_divi_gb": "Y"}]
    fake = FakeTransport(response=_resp(rows))
    e = _client(fake).domestic.calendar.dividends(start="20240301", end="20240331")[0]
    assert e.high_dividend is True                        # "Y" -> True
    assert e.cash_pay_date is None                        # "00000000" sentinel -> None


def test_dividends_bad_kind_raises():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.calendar.dividends(
            start="20240301", end="20240331", dividend_kind="nope"
        )


def test_dividends_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.dividends(start="20240301", end="20240331")


@pytest.mark.parametrize("bad_row", [None, 1, "x"])
def test_dividends_nonmapping_row_fails_closed(bad_row):
    # output1 이 배열이긴 하나 원소가 매핑이 아니면 경계에서 fail-closed(row.get AttributeError 누출 금지).
    fake = FakeTransport(response=_resp([bad_row]))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.dividends(start="20240301", end="20240331")


def test_dividends_valid_rows_still_parse_after_row_check():
    # 정상 매핑 배열은 행-검증 도입 후에도 동일하게 파싱된다(회귀 방지).
    rows = [{"record_date": "20240326", "sht_cd": "000720", "isin_name": "현대건설",
             "divi_kind": "결산", "face_val": "5000", "per_sto_divi_amt": "600",
             "divi_rate": "12.00", "stk_divi_rate": "0.00", "divi_pay_dt": "",
             "stk_div_pay_dt": "", "odd_pay_dt": "", "stk_kind": "보통", "high_divi_gb": ""}]
    fake = FakeTransport(response=_resp(rows))
    events = _client(fake).domestic.calendar.dividends(start="20240301", end="20240331")
    assert [e.symbol for e in events] == ["000720"]


def test_ipo_subscriptions_maps_slash_dates_and_text_period():
    # 원장 예시값(아이엠비디엑스). pay_dt는 YYYY/MM/DD, list_dt는 빈값, subscr_dt는 텍스트 범위.
    rows = [{"record_date": "20240325", "sht_cd": "461030", "isin_name": "아이엠비디엑스",
             "fix_subscr_pri": "       13000", "face_value": "000000100",
             "subscr_dt": "2024/03/25 ~ 2024/03/26", "pay_dt": "2024/03/28",
             "refund_dt": "2024/03/28", "list_dt": "", "lead_mgr": "미래에셋증권",
             "pub_bf_cap": "     1141762", "pub_af_cap": "       62500", "assign_stk_qty": "  0"}]
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                                              body={"output1": rows}))
    from kis_trader import IPOSubscription
    events = _client(fake).domestic.calendar.ipo_subscriptions(start="20240301", end="20240331")
    assert isinstance(events[0], IPOSubscription)
    e = events[0]
    assert e.symbol == "461030"
    assert e.offer_price == Decimal(13000)
    assert e.subscription_period == "2024/03/25 ~ 2024/03/26"   # 텍스트 그대로
    assert e.pay_date == date(2024, 3, 28)                      # YYYY/MM/DD -> date
    assert e.list_date is None                                  # 빈값 -> None
    assert e.lead_manager == "미래에셋증권"
    assert e.allocated_quantity == 0
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/pub-offer"
    assert call["tr_id"] == "HHKDB669108C0"


def test_rights_offerings_maps_and_basis():
    # 원장 예시값(메타록). 응답 배열 키는 예시대로 output1(레이아웃 output 아님).
    rows = [{"record_date": "20240222", "sht_cd": "426530", "isin_name": "메타록",
             "tot_issue_stk_qty": "    31000000", "issue_stk_qty": "      273199",
             "fix_rate": " 20.00", "disc_rate": " 0.00", "fix_price": "     500",
             "right_dt": "20240221", "sub_term_ft": "20240325",
             "sub_term": "2024/03/25 ~ 2024/03/26", "list_date": "", "stk_kind": "01"}]
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                                              body={"output1": rows}))
    from kis_trader import RightsOffering
    events = _client(fake).domestic.calendar.rights_offerings(
        start="20240201", end="20240229", offering_date_basis="record"
    )
    assert isinstance(events[0], RightsOffering)
    e = events[0]
    assert e.new_shares == 273199
    assert e.allocation_rate == Decimal("20.00")
    assert e.issue_price == Decimal(500)
    assert e.ex_rights_date == date(2024, 2, 21)
    assert e.subscription_start == date(2024, 3, 25)
    assert e.stock_kind == "01"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/paidin-capin"
    assert call["tr_id"] == "HHKDB669100C0"
    assert call["params"]["GB1"] == "2"                        # record


def test_rights_offerings_bad_basis_raises():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": []}))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.calendar.rights_offerings(
            start="20240201", end="20240229", offering_date_basis="nope"
        )


def test_bonus_issues_maps_and_params():
    # 원장 예시값(클로봇). zero/space padding과 미정 날짜 빈값을 그대로 검증한다.
    rows = [{"record_date": "20240326", "sht_cd": "466100", "isin_name": "클로봇",
             "fix_rate": "1000.0", "odd_rec_price": "000000000", "right_dt": "20240325",
             "odd_pay_dt": "", "list_date": "", "tot_issue_stk_qty": "     1885394",
             "issue_stk_qty": "    18853940", "stk_kind": "01"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import BonusIssue
    events = _client(fake).domestic.calendar.bonus_issues(start="20240301", end="20240331")
    assert isinstance(events[0], BonusIssue)
    e = events[0]
    assert e.symbol == "466100"
    assert e.allocation_rate == Decimal("1000.0")
    assert e.ex_rights_date == date(2024, 3, 25)
    assert e.odd_lot_pay_date is None
    assert e.list_date is None
    assert e.new_shares == 18853940
    assert e.stock_kind == "01"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/bonus-issue"
    assert call["tr_id"] == "HHKDB669101C0"


def test_bonus_issues_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.bonus_issues(start="20240301", end="20240331")


def test_capital_reductions_maps_and_params():
    # 원장 예시값(아스트). list_dt는 YYYY/MM/DD이고 td_stop_dt는 텍스트 범위다.
    rows = [{"record_date": "20240315", "sht_cd": "067390", "isin_name": "아스트",
             "stk_kind": "보통", "reduce_cap_type": "무상감자", "reduce_cap_rate": " 1.00",
             "comp_way": "곱하기", "td_stop_dt": "2024/03/14 ~ 2024/03/31",
             "list_dt": "2024/04/01"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import CapitalReduction
    events = _client(fake).domestic.calendar.capital_reductions(start="20240301", end="20240331")
    assert isinstance(events[0], CapitalReduction)
    e = events[0]
    assert e.symbol == "067390"
    assert e.record_date == date(2024, 3, 15)
    assert e.reduction_rate == Decimal("1.00")
    assert e.trading_halt_period == "2024/03/14 ~ 2024/03/31"
    assert e.list_date == date(2024, 4, 1)
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/cap-dcrs"
    assert call["tr_id"] == "HHKDB669106C0"


def test_capital_reductions_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.capital_reductions(start="20240301", end="20240331")


def test_merger_splits_maps_and_params():
    # 원장 예시값(베셀/에스케이씨에스). list_dt는 YYYYMMDD이고 td_stop_dt는 텍스트 범위다.
    rows = [{"record_date": "20240311", "sht_cd": "224020", "opp_cust_cd": "22402",
             "opp_cust_nm": "에스케이씨에스", "cust_cd": "17735", "cust_nm": "베셀",
             "merge_type": "흡수합병", "merge_rate": " 0.66",
             "td_stop_dt": "2024/03/08 ~ 2024/03/28", "list_dt": "20240329",
             "odd_amt_pay_dt": "2024/04/05", "tot_issue_stk_qty": "           0",
             "issue_stk_qty": "           0", "seq": "00"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import MergerSplit
    events = _client(fake).domestic.calendar.merger_splits(start="20240301", end="20240331")
    assert isinstance(events[0], MergerSplit)
    e = events[0]
    assert e.symbol == "224020"
    assert e.record_date == date(2024, 3, 11)
    assert e.company_name == "베셀"
    assert e.counterparty_name == "에스케이씨에스"
    assert e.merge_ratio == Decimal("0.66")
    assert e.trading_halt_period == "2024/03/08 ~ 2024/03/28"
    assert e.list_date == date(2024, 3, 29)
    assert e.new_shares == 0
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/merger-split"
    assert call["tr_id"] == "HHKDB669104C0"


def test_merger_splits_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.merger_splits(start="20240301", end="20240331")


def test_shareholder_meetings_maps_and_params():
    rows = [{"record_date": "20240322", "sht_cd": "388370",
             "isin_name": "(주)우앤컴퍼니", "gen_meet_dt": "2024/04/18",
             "gen_meet_type": "임시총회", "agenda": "정관변경",
             "vote_tot_qty": "      959800"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import ShareholderMeeting
    events = _client(fake).domestic.calendar.shareholder_meetings(
        start="20240301", end="20240331", symbol="388370"
    )
    assert isinstance(events[0], ShareholderMeeting)
    e = events[0]
    assert e.symbol == "388370"
    assert e.record_date == date(2024, 3, 22)
    assert e.meeting_date == date(2024, 4, 18)
    assert e.meeting_type == "임시총회"
    assert e.agenda == "정관변경"
    assert e.voting_shares == 959800
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/sharehld-meet"
    assert call["tr_id"] == "HHKDB669111C0"
    assert call["params"]["SHT_CD"] == "388370"


def test_shareholder_meetings_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.shareholder_meetings(start="20240301", end="20240331")


def test_mandatory_deposits_maps_and_params():
    rows = [{"sht_cd": "27322R", "isin_name": "뷰텔7우", "stk_qty": "       68966",
             "depo_date": "2024/03/26 ~ 2025/03/26", "depo_reason": "모집매출",
             "tot_issue_qty_per_rate": "10000"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import MandatoryDeposit
    events = _client(fake).domestic.calendar.mandatory_deposits(
        start="20240301", end="20240331", symbol="27322R"
    )
    assert isinstance(events[0], MandatoryDeposit)
    e = events[0]
    assert e.symbol == "27322R"
    assert e.deposit_shares == 68966
    assert e.deposit_period == "2024/03/26 ~ 2025/03/26"
    assert e.deposit_reason == "모집매출"
    assert e.issued_shares_ratio == Decimal(10000)
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/mand-deposit"
    assert call["tr_id"] == "HHKDB669110C0"
    assert call["params"]["SHT_CD"] == "27322R"


def test_mandatory_deposits_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.mandatory_deposits(start="20240301", end="20240331")


def test_listings_maps_and_params():
    rows = [{"list_dt": "20240326", "sht_cd": "034220", "isin_name": "LG디스플레이",
             "stk_kind": "보통", "issue_type": "유상증자",
             "issue_stk_qty": "   142184300", "tot_issue_stk_qty": "   500000000",
             "issue_price": "     9090"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import ListingEvent
    events = _client(fake).domestic.calendar.listings(
        start="20240301", end="20240331", symbol="034220"
    )
    assert isinstance(events[0], ListingEvent)
    e = events[0]
    assert e.symbol == "034220"
    assert e.list_date == date(2024, 3, 26)
    assert e.issue_type == "유상증자"
    assert e.new_shares == 142184300
    assert e.total_shares == 500000000
    assert e.issue_price == Decimal(9090)
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/list-info"
    assert call["tr_id"] == "HHKDB669107C0"
    assert call["params"]["SHT_CD"] == "034220"


def test_listings_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.listings(start="20240301", end="20240331")


def test_par_value_changes_maps_and_params():
    rows = [{"record_date": "20230823", "sht_cd": "001390", "isin_name": "케이지케미칼",
             "inter_bf_face_amt": "000005000", "inter_af_face_amt": "000001000",
             "td_stop_dt": "2023/08/22 ~ 2023/08/27", "list_dt": "2023/08/28"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import ParValueChange
    events = _client(fake).domestic.calendar.par_value_changes(
        start="20230801", end="20230831", symbol="001390"
    )
    assert isinstance(events[0], ParValueChange)
    e = events[0]
    assert e.symbol == "001390"
    assert e.record_date == date(2023, 8, 23)
    assert e.face_value_before == Decimal(5000)
    assert e.trading_halt_period == "2023/08/22 ~ 2023/08/27"
    assert e.list_date == date(2023, 8, 28)
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/rev-split"
    assert call["tr_id"] == "HHKDB669105C0"
    assert call["params"]["SHT_CD"] == "001390"


def test_par_value_changes_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.par_value_changes(start="20230801", end="20230831")


def test_forfeited_shares_maps_and_params():
    rows = [{"record_date": "20240131", "sht_cd": "001440", "isin_name": "대한전선",
             "subscr_dt": "2024/03/14 ~ 2024/03/15", "subscr_price": "000007460",
             "subscr_stk_qty": "    62000000", "refund_dt": "2024/03/19",
             "list_dt": "2024/04/02", "lead_mgr": "케이비증권,미래에셋증권,"}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import ForfeitedShares
    events = _client(fake).domestic.calendar.forfeited_shares(start="20240101", end="20240131")
    assert isinstance(events[0], ForfeitedShares)
    e = events[0]
    assert e.symbol == "001440"
    assert e.record_date == date(2024, 1, 31)
    assert e.subscription_price == Decimal(7460)
    assert e.subscription_period == "2024/03/14 ~ 2024/03/15"
    assert e.refund_date == date(2024, 3, 19)
    assert e.lead_manager == "케이비증권,미래에셋증권,"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/forfeit"
    assert call["tr_id"] == "HHKDB669109C0"


def test_forfeited_shares_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.forfeited_shares(start="20240101", end="20240131")


def test_appraisal_rights_maps_and_params():
    rows = [{"record_date": "20240313", "sht_cd": "065350", "isin_name": "신성델타테크",
             "stk_kind": "보통", "opp_opi_rcpt_term": "020240326",
             "buy_req_rcpt_term": "", "buy_req_price": "000000000000",
             "buy_amt_pay_dt": "", "meet_dt": ""}]
    fake = FakeTransport(response=_resp(rows))
    from kis_trader import AppraisalRights
    events = _client(fake).domestic.calendar.appraisal_rights(start="20240301", end="20240331")
    assert isinstance(events[0], AppraisalRights)
    e = events[0]
    assert e.symbol == "065350"
    assert e.record_date == date(2024, 3, 13)
    assert e.opposition_period == "020240326"
    assert e.buyback_price == Decimal(0)
    assert e.payment_date is None
    assert e.meeting_date is None
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/ksdinfo/purreq"
    assert call["tr_id"] == "HHKDB669103C0"


def test_appraisal_rights_reads_meet_dt_key():
    # 주총일은 원장 ksdinfo_purreq/chk 의 meet_dt 로 읽는다("get_meet_dt" 는 오타였다).
    rows = [{"record_date": "20240313", "sht_cd": "065350", "isin_name": "신성델타테크",
             "stk_kind": "보통", "opp_opi_rcpt_term": "", "buy_req_rcpt_term": "",
             "buy_req_price": "0", "buy_amt_pay_dt": "", "meet_dt": "2024/05/20"}]
    events = _client(FakeTransport(response=_resp(rows))).domestic.calendar.appraisal_rights(
        start="20240301", end="20240531")
    assert events[0].meeting_date == date(2024, 5, 20)


def test_appraisal_rights_missing_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.calendar.appraisal_rights(start="20240301", end="20240331")

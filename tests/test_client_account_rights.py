"""기간별 계좌 권리현황 -- kis.account.domestic.rights(start=, end=) (CTRGA011R).

계좌에 배정/신청/환불된 권리 내역을 검증한다. 응답 배열 키는 원장 예시 기준 ``output``
(레이아웃 output1 과 다름). 픽스처는 원장 응답예시(period-rights) 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import AccountRight, KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/trading/period-rights"

# 원장 응답예시(period-rights) 실값 -- 유상증자 배정 1건.
_ROW = {
    "acno10": "1234567801", "rght_type_cd": "01", "bass_dt": "20240919",
    "rght_cblc_type_cd": "01", "rptt_pdno": "00000A357880", "pdno": "00000A357880",
    "prdt_type_cd": "300", "shtn_pdno": "357880", "prdt_name": "비트나인",
    "cblc_qty": "1000", "last_alct_qty": "1050", "excs_alct_qty": "0", "tot_alct_qty": "1050",
    "last_ftsk_qty": "0.0000000000", "last_alct_amt": "0", "last_ftsk_chgs": "0",
    "rdpt_prca": "0", "dlay_int_amt": "0", "lstg_dt": "", "sbsc_end_dt": "20241011",
    "cash_dfrm_dt": "", "rqst_qty": "1000", "rqst_amt": "1865000", "rqst_dt": "20241011",
    "rfnd_dt": "", "rfnd_amt": "0", "lstg_stqt": "0", "tax_amt": "0", "sbsc_unpr": "1865.0000",
}


def _resp(rows=None, *, nk="", fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [_ROW],
            "ctx_area_nk100": nk, "ctx_area_fk100": fk}
    return RawResponse(rt_cd="0", msg_cd="KIOK0460", msg1="조회", body=body, tr_cont=tr_cont)


class FakeTransport:
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "idempotent": idempotent, "tr_cont": tr_cont})
        outcome = self.pages.pop(0) if self.pages else self.response
        assert outcome is not None
        return outcome


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def test_account_rights_parses_ledger_row():
    rights = _client(FakeTransport(response=_resp())).account.domestic.rights(start="20240508", end="20241106")
    assert len(rights) == 1
    r = rights[0]
    assert isinstance(r, AccountRight)
    assert r.account_number == "1234567801"
    assert r.right_type_code == "01"
    assert r.record_date == date(2024, 9, 19)
    assert r.symbol == "00000A357880"
    assert r.short_symbol == "357880"
    assert r.name == "비트나인"
    assert r.balance_quantity == Decimal(1000)
    assert r.allocated_quantity == Decimal(1050)
    assert r.total_allocated_quantity == Decimal(1050)
    assert r.subscription_price == Decimal("1865.0000")
    assert r.requested_amount == Decimal(1865000)
    assert r.subscription_end_date == date(2024, 10, 11)
    assert r.request_date == date(2024, 10, 11)
    assert r.listing_date is None          # 빈 문자열 -> None
    assert r.refund_amount == Decimal(0)


def test_account_rights_tr_method_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).account.domestic.rights(start="20240508", end="20241106")
    call = fake.calls[0]
    assert call["tr_id"] == "CTRGA011R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["INQR_DVSN"] == "03"
    assert call["params"]["INQR_STRT_DT"] == "20240508"
    assert call["params"]["INQR_END_DT"] == "20241106"
    assert call["params"]["CANO"] == "12345678"


def test_account_rights_reads_output_key_not_output1():
    # 레이아웃은 output1 이라 하지만 실제 응답은 output -- output1 만 주면 안 읽혀야 정상 실패
    body = {"output1": [_ROW]}   # output 키 없음
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body=body, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.rights(start="1", end="2")


def test_account_rights_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").account.domestic.rights(start="1", end="2")
    assert fake.calls == []


def test_account_rights_empty_is_ok():
    assert _client(FakeTransport(response=_resp([]))).account.domestic.rights(start="1", end="2") == []


def test_account_rights_skips_padding_row():
    rights = _client(FakeTransport(response=_resp([dict(_ROW, pdno=""), _ROW]))).account.domestic.rights(
        start="1", end="2"
    )
    assert len(rights) == 1


def test_account_rights_paginates_and_merges():
    page1 = _resp([_ROW], nk="NEXT", tr_cont="M")
    page2 = _resp([dict(_ROW, pdno="00000B111111")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    rights = _client(fake).account.domestic.rights(start="1", end="2")
    assert [r.symbol for r in rights] == ["00000A357880", "00000B111111"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NEXT"


def test_account_rights_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).account.domestic.rights(start="1", end="2")


def test_account_rights_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).account.domestic.rights(start="1", end="2")

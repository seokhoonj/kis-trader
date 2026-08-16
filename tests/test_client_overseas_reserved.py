"""미국 해외주식 예약주문 조회 -- kis.overseas.account.reserved_orders(start=, end=) (TTTT3039R).

미국 예약주문 목록(order-resv-list)을 네트워크 없이 검증한다. 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import KISClient, OverseasReservedOrder
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_PATH = "/uapi/overseas-stock/v1/trading/order-resv-list"

# 원장 응답예시(order-resv-list) 실값.
_ROW = {
    "cncl_yn": "N", "rsvn_ord_rcit_dt": "20250523", "ovrs_rsvn_odno": "0031111234",
    "ord_dt": "", "ord_gno_brno": "", "odno": "", "sll_buy_dvsn_cd": "02",
    "sll_buy_dvsn_cd_name": "TWAP지정가매수", "ovrs_rsvn_ord_stat_cd": "01",
    "ovrs_rsvn_ord_stat_cd_name": "접수", "pdno": "AAPL", "prdt_name": "애플",
    "ord_rcit_tmd": "161928", "ord_fwdg_tmd": "", "tr_dvsn_name": "접수",
    "ovrs_excg_cd": "NASD", "ft_ord_qty": "1", "ft_ord_unpr3": "150.25", "ft_ccld_qty": "0",
    "nprc_rson_text": "",
}


def _resp(rows=None, *, nk="", fk="", tr_cont=""):
    body = {"output": rows if rows is not None else [_ROW],
            "ctx_area_nk200": nk, "ctx_area_fk200": fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


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


def test_overseas_reserved_parses_ledger_row():
    orders = _client(FakeTransport(response=_resp())).overseas.account.reserved_orders(
        start="20250501", end="20250531")
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, OverseasReservedOrder)
    assert o.reserved_order_id == "0031111234"
    assert o.receipt_date == date(2025, 5, 23)
    assert o.order_date is None                      # 미집행
    assert o.executed_order_id == ""
    assert o.symbol == "AAPL"
    assert o.name == "애플"
    assert o.side == "buy"                           # 02 -> buy
    assert o.status == "접수"
    assert o.exchange == "NASD"
    assert o.quantity == Decimal(1)
    assert o.order_price == Decimal("150.25")
    assert o.filled_quantity == Decimal(0)
    assert o.canceled is False


def test_overseas_reserved_tr_and_params():
    fake = FakeTransport(response=_resp())
    _client(fake).overseas.account.reserved_orders(start="20250501", end="20250531")
    call = fake.calls[0]
    assert call["tr_id"] == "TTTT3039R"
    assert call["method"] == "GET"
    assert call["path"] == _PATH
    assert call["idempotent"] is True
    assert call["params"]["INQR_STRT_DT"] == "20250501"
    assert call["params"]["INQR_END_DT"] == "20250531"
    assert call["params"]["INQR_DVSN_CD"] == "00"
    assert call["params"]["OVRS_EXCG_CD"] == ""      # 공백 = 미국 전체
    assert call["params"]["PRDT_TYPE_CD"] == ""


def test_overseas_reserved_demo_rejected_before_io():
    fake = FakeTransport(response=_resp())
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").overseas.account.reserved_orders(start="1", end="2")
    assert fake.calls == []


def test_overseas_reserved_canceled_flag():
    orders = _client(FakeTransport(response=_resp([dict(_ROW, cncl_yn="Y")]))).overseas.account.reserved_orders(
        start="20250501", end="20250531")
    assert orders[0].canceled is True


def test_overseas_reserved_paginates():
    page1 = _resp([_ROW], nk="NEXT", fk="FK", tr_cont="M")
    page2 = _resp([dict(_ROW, ovrs_rsvn_odno="0031111299")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    orders = _client(fake).overseas.account.reserved_orders(start="20250501", end="20250531")
    assert [o.reserved_order_id for o in orders] == ["0031111234", "0031111299"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_overseas_reserved_empty_ok():
    assert _client(FakeTransport(response=_resp([]))).overseas.account.reserved_orders(start="20250501", end="20250531") == []


def test_overseas_reserved_skips_padding_row():
    orders = _client(FakeTransport(response=_resp([dict(_ROW, ovrs_rsvn_odno=""), _ROW]))).overseas.account.reserved_orders(
        start="20250501", end="20250531")
    assert len(orders) == 1


def test_overseas_reserved_non_list_output_fails_closed():
    resp = RawResponse(rt_cd="0", msg_cd="M", msg1="", body={"output": {"ovrs_rsvn_odno": "1"}}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.reserved_orders(start="1", end="2")


def test_overseas_reserved_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="E", msg1="실패", body={"output": []}, tr_cont="")
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).overseas.account.reserved_orders(start="1", end="2")


def test_overseas_reserved_requires_account():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_resp()), account=None).overseas.account.reserved_orders(start="1", end="2")


@pytest.mark.parametrize("bad_code", ["99", "", "0", "XX"])
def test_overseas_reserved_unknown_side_code_fails_closed(bad_code):
    """A-12: 알 수 없는/빈 매매구분코드는 side="" 로 뭉개지 않고 fail-closed(KISError)."""
    fake = FakeTransport(response=_resp([dict(_ROW, sll_buy_dvsn_cd=bad_code)]))
    with pytest.raises(KISError):
        _client(fake).overseas.account.reserved_orders(start="20250501", end="20250531")


def test_overseas_reserved_known_side_codes_still_map():
    """A-12: 알려진 01(매도)/02(매수)는 종전과 동일하게 매핑된다(회귀 방지)."""
    sell = _client(FakeTransport(response=_resp([dict(_ROW, sll_buy_dvsn_cd="01")]))).overseas.account.reserved_orders(
        start="20250501", end="20250531")
    buy = _client(FakeTransport(response=_resp([dict(_ROW, sll_buy_dvsn_cd="02")]))).overseas.account.reserved_orders(
        start="20250501", end="20250531")
    assert sell[0].side == "sell"
    assert buy[0].side == "buy"

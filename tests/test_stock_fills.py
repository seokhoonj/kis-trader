"""국내주식 일별 주문·체결 내역 -- kis.account.domestic.fills (TTTC0081R/VTTC0081R).

FakeTransport 로 네트워크 없이 파싱·연속조회·와이어 파라미터를 검증한다. 픽스처 필드는
원장(주식일별주문체결조회) 응답예시 기반. 실전·모의 모두 지원한다.
"""

from __future__ import annotations

import threading
from datetime import date, time
from decimal import Decimal

import pytest

from kis_trader import KISClient, StockFillHistory
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_FILLS_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


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
        if self.pages is not None:
            return self.pages.pop(0)
        assert self.response is not None
        return self.response


def _client(transport, *, environment="real", account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment=environment, transport=transport)


def _fills(transport, *, environment="real"):
    return _client(transport, environment=environment).account.domestic.fills


def _row(*, odno="0001568197", pdno="005930", side="02", ord_qty="10", ord_unpr="70000",
         filled="10", avg="70050", ccld_amt="700500", rmn="0", rjct="0", cncl=""):
    return {
        "ord_dt": "20240216", "ord_gno_brno": "06010", "odno": odno, "orgn_odno": "",
        "ord_dvsn_name": "지정가", "sll_buy_dvsn_cd": side, "sll_buy_dvsn_cd_name": "매수",
        "pdno": pdno, "prdt_name": "삼성전자", "ord_qty": ord_qty, "ord_unpr": ord_unpr,
        "ord_tmd": "131438", "tot_ccld_qty": filled, "avg_prvs": avg, "cncl_yn": cncl,
        "tot_ccld_amt": ccld_amt, "loan_dt": "", "ord_dvsn_cd": "00",
        "rmn_qty": rmn, "rjct_qty": rjct,
    }


_SUMMARY = {
    "tot_ord_qty": "10", "tot_ccld_qty": "10", "tot_ccld_amt": "700500",
    "prsm_tlex_smtl": "118", "pchs_avg_pric": "70050.0000",
}


def _resp(*, rows=None, summary=None, tr_cont="", ctx_nk="", ctx_fk=""):
    body = {"output1": rows if rows is not None else [],
            "output2": summary if summary is not None else _SUMMARY,
            "ctx_area_nk100": ctx_nk, "ctx_area_fk100": ctx_fk}
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body, tr_cont=tr_cont)


def test_fills_parses_rows_and_summary_and_routes():
    fake = FakeTransport(response=_resp(rows=[_row()]))
    history = _fills(fake)(start="20240201", end="20240229")
    assert isinstance(history, StockFillHistory)
    assert history.total_order_quantity == Decimal(10)
    assert history.total_filled_amount == Decimal(700500)
    assert history.average_purchase_price == Decimal("70050.0000")
    assert history.estimated_expenses == Decimal(118)
    assert len(history.fills) == 1
    fill = history.fills[0]
    assert fill.order_id == "0001568197"
    assert fill.symbol == "005930"
    assert fill.side == "buy"
    assert fill.order_date == date(2024, 2, 16)
    assert fill.order_time == time(13, 14, 38)
    assert fill.order_quantity == Decimal(10)
    assert fill.filled_quantity == Decimal(10)
    assert fill.average_price == Decimal(70050)
    assert fill.cancelled is False
    call = fake.calls[0]
    assert call["path"] == _FILLS_PATH
    assert call["tr_id"] == "TTTC0081R"
    assert call["idempotent"] is True
    assert call["params"]["INQR_STRT_DT"] == "20240201"
    assert call["params"]["INQR_END_DT"] == "20240229"
    assert call["params"]["SLL_BUY_DVSN_CD"] == "00"  # 기본 all
    assert call["params"]["PDNO"] == ""
    assert call["params"]["CCLD_DVSN"] == "00"


def test_fills_paper_uses_demo_tr():
    fake = FakeTransport(response=_resp(rows=[_row()]))
    _fills(fake, environment="paper")(start="20240201", end="20240229")
    assert fake.calls[0]["tr_id"] == "VTTC0081R"


def test_fills_older_changes_only_the_tr_id():
    # 3개월 이전/이내는 같은 엔드포인트에서 tr_id 만 다르다 -- 경로·파라미터 전부 동일함을 못박는다.
    recent = FakeTransport(response=_resp(rows=[_row()]))
    older = FakeTransport(response=_resp(rows=[_row()]))
    kwargs = {"start": "20230101", "end": "20230331", "side": "sell",
              "symbol": "005930", "unfilled_only": True}
    _fills(recent)(**kwargs)
    _fills(older)(**kwargs, older_than_three_months=True)
    recent_call, older_call = recent.calls[0], older.calls[0]
    assert recent_call["tr_id"] == "TTTC0081R"
    assert older_call["tr_id"] == "CTSC9215R"
    assert older_call["path"] == recent_call["path"]
    assert older_call["params"] == recent_call["params"]


def test_fills_older_paper_uses_demo_before_tr():
    fake = FakeTransport(response=_resp(rows=[_row()]))
    _fills(fake, environment="paper")(start="20230101", end="20230331",
                                      older_than_three_months=True)
    assert fake.calls[0]["tr_id"] == "VTSC9215R"


@pytest.mark.parametrize("side,code", [("all", "00"), ("sell", "01"), ("buy", "02")])
def test_fills_side_maps_to_wire(side, code):
    fake = FakeTransport(response=_resp(rows=[]))
    _fills(fake)(start="20240201", end="20240229", side=side)
    assert fake.calls[0]["params"]["SLL_BUY_DVSN_CD"] == code


def test_fills_symbol_and_unfilled_only_route():
    fake = FakeTransport(response=_resp(rows=[]))
    _fills(fake)(start="20240201", end="20240229", symbol="005930", unfilled_only=True)
    params = fake.calls[0]["params"]
    assert params["PDNO"] == "005930"
    assert params["CCLD_DVSN"] == "02"  # 미체결만


def test_fills_rejects_unknown_side():
    fake = FakeTransport(response=_resp(rows=[]))
    with pytest.raises(KISUsageError, match="side"):
        _fills(fake)(start="20240201", end="20240229", side="both")
    assert fake.calls == []  # 와이어 전 거부


def test_fills_skips_blank_odno_padding_rows():
    fake = FakeTransport(response=_resp(rows=[_row(odno="0001"), _row(odno="  ")]))
    history = _fills(fake)(start="20240201", end="20240229")
    assert [f.order_id for f in history.fills] == ["0001"]


def test_fills_missing_summary_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "ctx_area_nk100": "", "ctx_area_fk100": ""})
    with pytest.raises(KISError, match="합계 요약"):
        _fills(FakeTransport(response=resp))(start="20240201", end="20240229")


def test_fills_non_list_output_raises():
    resp = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {"odno": "x"}, "output2": _SUMMARY})
    with pytest.raises(KISError):
        _fills(FakeTransport(response=resp))(start="20240201", end="20240229")


def test_fills_error_response_raises():
    resp = RawResponse(rt_cd="1", msg_cd="EGW00215", msg1="초당 거래건수 초과", body={})
    with pytest.raises(KISError):
        _fills(FakeTransport(response=resp))(start="20240201", end="20240229")


def test_fills_paginates_two_pages():
    page1 = _resp(rows=[_row(odno="1")], tr_cont="F", ctx_nk="NK", ctx_fk="FK")
    page2 = _resp(rows=[_row(odno="2")], tr_cont="D")
    fake = FakeTransport(pages=[page1, page2])
    history = _fills(fake)(start="20240201", end="20240229")
    assert [f.order_id for f in history.fills] == ["1", "2"]
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[1]["params"]["CTX_AREA_NK100"] == "NK"
    assert fake.calls[1]["params"]["CTX_AREA_FK100"] == "FK"


def test_fills_cancelled_flag():
    fake = FakeTransport(response=_resp(rows=[_row(cncl="Y")]))
    history = _fills(fake)(start="20240201", end="20240229")
    assert history.fills[0].cancelled is True

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
         filled="10", avg="70050", ccld_amt="700500", rmn="0", rjct="0", cncl="",
         cncl_cfrm="0", exchange="KRX", excg_dvsn="02", ccld_cndt="없음",
         stpm_pric="0", stpm_eff=""):
    return {
        "ord_dt": "20240216", "ord_gno_brno": "06010", "odno": odno, "orgn_odno": "",
        "ord_dvsn_name": "지정가", "sll_buy_dvsn_cd": side, "sll_buy_dvsn_cd_name": "매수",
        "pdno": pdno, "prdt_name": "삼성전자", "ord_qty": ord_qty, "ord_unpr": ord_unpr,
        "ord_tmd": "131438", "tot_ccld_qty": filled, "avg_prvs": avg, "cncl_yn": cncl,
        "tot_ccld_amt": ccld_amt, "loan_dt": "", "ord_dvsn_cd": "00",
        "rmn_qty": rmn, "rjct_qty": rjct,
        # 0.6.0 보강 필드(원장 응답예시 기반; PII/운영 필드는 승격 안 함)
        "cncl_cfrm_qty": cncl_cfrm, "excg_id_dvsn_cd": exchange, "excg_dvsn_cd": excg_dvsn,
        "ccld_cndt_name": ccld_cndt, "prdt_type_cd": "", "ord_orgno": "06010",
        "rsvn_ord_end_dt": "", "stpm_cndt_pric": stpm_pric, "stpm_efct_occr_dtmd": stpm_eff,
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
    assert fill.canceled is False
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


# --- 0.6.0 보강 필드 (라이브 응답예시로 키셋/타입 확정) -----------------------
def test_fill_exposes_0_6_0_fields():
    fake = FakeTransport(response=_resp(rows=[_row(cncl_cfrm="3", excg_dvsn="12", ccld_cndt="IOC")]))
    fill = _fills(fake)(start="20240201", end="20240229").fills[0]
    assert fill.canceled_quantity == Decimal(3)
    assert fill.exchange_division_code == "12"
    assert fill.fill_condition_name == "IOC"
    assert fill.order_type_code == "00"


def test_fill_exchange_is_typed_and_none_when_blank():
    # excg_id_dvsn_cd 값이 그대로 타입드 거래소; NXT 도입 전 주문은 공란 -> None.
    krx = _fills(FakeTransport(response=_resp(rows=[_row(exchange="KRX")])))(start="20240201", end="20240229")
    assert krx.fills[0].exchange == "KRX"
    sor = _fills(FakeTransport(response=_resp(rows=[_row(exchange="SOR")])))(start="20240201", end="20240229")
    assert sor.fills[0].exchange == "SOR"
    blank = _fills(FakeTransport(response=_resp(rows=[_row(exchange="")])))(start="20240201", end="20240229")
    assert blank.fills[0].exchange is None


def test_fill_exchange_unknown_code_degrades_to_none(caplog):
    # 조회(READ) 경로: 모르는 거래소 코드 한 건이 전체 체결내역 조회를 깨면 안 된다 -> None 으로
    # 저하하고 경고를 남기며, 원본 코드는 _raw 에 보존한다(주문 경로는 Order 가 여전히 fail-closed).
    fake = FakeTransport(response=_resp(rows=[_row(exchange="ZZZ")]))
    with caplog.at_level("WARNING"):
        fills = _fills(fake)(start="20240201", end="20240229").fills
    assert fills[0].exchange is None
    assert fills[0].symbol == "005930"                  # 나머지 행은 정상 파싱(조회가 안 깨짐)
    assert fills[0]._raw["excg_id_dvsn_cd"] == "ZZZ"    # 원본 코드 보존(조용한 손실 아님)
    assert any("excg_id_dvsn_cd" in rec.message for rec in caplog.records)


def test_fill_stop_is_none_for_non_stop_order():
    # stpm_cndt_pric 가 0/공란이면 비-스톱 주문 -> stop 은 None(joint-presence).
    fake = FakeTransport(response=_resp(rows=[_row(stpm_pric="0")]))
    assert _fills(fake)(start="20240201", end="20240229").fills[0].stop is None


def test_fill_stop_present_with_trigger_price():
    fake = FakeTransport(response=_resp(rows=[_row(stpm_pric="71000", stpm_eff="20240216131500")]))
    stop = _fills(fake)(start="20240201", end="20240229").fills[0].stop
    assert stop is not None
    assert stop.trigger_price == Decimal(71000)
    assert stop.effective_datetime is not None
    et = stop.effective_datetime
    assert (et.year, et.month, et.day, et.hour, et.minute, et.second) == (2024, 2, 16, 13, 15, 0)
    assert et.tzinfo is not None                        # KST-aware


def test_fill_canceled_quantity_defaults_to_zero_when_absent():
    # 관용 파싱: cncl_cfrm_qty 키가 없어도 크래시 없이 0.
    row = _row()
    del row["cncl_cfrm_qty"]
    fill = _fills(FakeTransport(response=_resp(rows=[row])))(start="20240201", end="20240229").fills[0]
    assert fill.canceled_quantity == Decimal(0)


def test_fill_keeps_pii_and_operator_fields_in_raw_only():
    # PII/운영-통보 키는 _raw 에만 남고 공개 속성으로 승격되지 않는다(실수 승격 회귀 가드).
    pii_keys = ["ctac_tlno", "inqr_ip_addr", "ordr_empno", "cpbc_ordp_infm_mthd_dvsn_cd",
                "cpbc_ordp_mtrl_dvsn_cd", "cpbc_ordp_ord_rcit_dvsn_cd", "infm_tmd",
                "sll_buy_dvsn_cd_name"]
    row = _row()
    row.update({k: "SENSITIVE" for k in pii_keys})
    fill = _fills(FakeTransport(response=_resp(rows=[row])))(start="20240201", end="20240229").fills[0]
    for k in pii_keys:
        # hasattr 은 슬롯/속성뿐 아니라 @property 노출까지 잡는다(슬롯만 보면 property 를 놓침).
        assert not hasattr(fill, k), f"{k} must not be publicly accessible"
        assert fill._raw[k] == "SENSITIVE"              # 원본엔 보존(조회·감사용)
    # 값으로도 유출되지 않는다(어떤 공개 str 필드에도 'SENSITIVE' 가 없다)
    assert not any(getattr(fill, f) == "SENSITIVE" for f in fill.__slots__ if f != "_raw")


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
    assert history.fills[0].canceled is True

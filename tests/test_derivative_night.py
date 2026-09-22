"""국내 선물·옵션 야간(NIGHT) 전 경로 -- 야간 발주(FUOP 필수·paper 거부)·야간 정정취소(신선
잔량)·이관/T+1 을 넘는 union 재조회.

야간 발주는 실전 ``STTN1101U``(모의 미지원), 야간 정정취소는 ``TTTN1103U``(발주는 STTN 접두지만
정정취소는 TTTN 접두 -- KIS 명세가 다르다; 잔량 재지정이라 주간과 달리 ORD_QTY 는 실잔량이라
0/공백 금지), 야간 재조회는 두 테이블의 union 이다:
- 다리 A: (야간)선물옵션 주문체결내역조회 ``inquire-ngt-ccnl``(``STTN5201R``).
- 다리 B: 주간 일별체결내역 ``inquire-ccnl``(``TTTO5201R``) -- 야간 체결이 06:10 경 주간으로
  이관되고 주문일자가 T+1 이라, claim 시각 앵커 T-0~T+3 영업일 창으로 훑는다.
어느 다리든 실패면 all-or-nothing 으로 KISError, 병합은 ``(odno, ord_dt)`` 둘 다 같을 때만 한다.

픽스처는 원장 응답예시/필드 스키마 실값을 쓴다(야간 output1 은 ``nmpr_type_name``/``ord_idx4``,
주간 output1 은 ``nmpr_type_cd``/``ord_idx`` -- 두 테이블의 필드명이 다르다). 날짜 로직은
claim 앵커/``now`` 주입으로 결정적이다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader.domestic._engine import derivative_orders as fo
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_KST = timezone(timedelta(hours=9))
_PLACE = "/uapi/domestic-futureoption/v1/trading/order"
_CHANGE = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
_DAY_INQUIRY = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl"
_NIGHT_INQUIRY = "/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl"

_PLACE_OK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                        body={"output": {"ODNO": "0000005605"}})
_CHANGE_OK = RawResponse(rt_cd="0", msg_cd="A", msg1="",
                         body={"output": {"ODNO": "0000005606"}})


# --- 픽스처 -----------------------------------------------------------------
def _night_row(*, odno="0000005605", orgn_odno="0000000000", side="02", pdno="101S03",
               ord_qty="2", ord_idx4="400.00", qty="1", tot_ccld_qty="1", rjct_qty="0",
               ord_dt="20220114"):
    # (야간)선물옵션 주문체결내역조회 output1 스키마 실값 -- nmpr_type_name/ord_idx4 를 쓰고
    # nmpr_type_cd/ord_idx 는 없다(주간 테이블과 다른 필드명).
    return {"ord_gno_brno": "06010", "cano": "810XXXXX", "acnt_prdt_cd": "03", "ord_dt": ord_dt,
            "odno": odno, "orgn_odno": orgn_odno, "sll_buy_dvsn_cd": side,
            "trad_dvsn_name": "NIGHT BUY", "nmpr_type_name": "Limit Order", "pdno": pdno,
            "prdt_name": "F 202203", "prdt_type_cd": "301", "ord_qty": ord_qty, "ord_idx4": ord_idx4,
            "qty": qty, "ord_tmd": "230101", "tot_ccld_qty": tot_ccld_qty, "avg_idx": "400.00",
            "tot_ccld_amt": "0", "rjct_qty": rjct_qty, "sprd_item_yn": "N"}


def _day_row(*, odno="0000005605", orgn_odno="0000000000", side="02", nmpr="01", pdno="101S03",
             ord_qty="2", ord_idx="400.00", qty="1", tot_ccld_qty="1", rjct_qty="0",
             ord_dt="20220114"):
    # 주간 일별체결내역 output1 -- nmpr_type_cd/ord_idx 를 쓴다(야간 이관 후 여기서 보인다).
    return {"ord_gno_brno": "06010", "cano": "810XXXXX", "acnt_prdt_cd": "03", "ord_dt": ord_dt,
            "odno": odno, "orgn_odno": orgn_odno, "sll_buy_dvsn_cd": side, "nmpr_type_cd": nmpr,
            "pdno": pdno, "ord_qty": ord_qty, "ord_idx": ord_idx, "qty": qty,
            "tot_ccld_qty": tot_ccld_qty, "rjct_qty": rjct_qty, "sprd_item_yn": "N"}


def _ngt_ccnl(rows, **extra):
    body = {"output1": rows, "output2": {"tot_ccld_qty": "1"}}
    body.update(extra)
    return RawResponse(rt_cd="0", msg_cd="0", msg1="정상", body=body)


def _day_ccnl(rows, **extra):
    body = {"output1": rows, "output2": {"tot_ccld_qty": "1"}}
    body.update(extra)
    return RawResponse(rt_cd="0", msg_cd="0", msg1="정상", body=body)


class FakeTransport:
    """(method, path) 별 큐 응답/예외를 준다. 리스트면 페이지별 순차 pop."""

    def __init__(self):
        self.by_path: dict[str, object] = {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def queue(self, path, outcome):
        self.by_path[path] = outcome

    @property
    def request_count(self) -> int:
        return len(self.calls)

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "body": body})
        outcome = self.by_path.get(path)
        if outcome is None:
            raise AssertionError(f"unexpected {method} to {path}")
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _order(**kw):
    base = {"symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": Decimal(2),
            "limit_price": Decimal("400.00"), "exchange": "XKFE", "session": "night",
            "derivative_item": "01", "client_order_id": "n1"}
    base.update(kw)
    return Order(**base)


def _fp(**kw):
    from kis_trader.order import ImmediateOrderFingerprint
    base = {"symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": "2",
            "limit_price": "400.00", "stop_price": "", "time_in_force": "day", "exchange": "XKFE",
            "session": "night", "division": "", "derivative_item": "01"}
    base.update(kw)
    return ImmediateOrderFingerprint(**base)


def _real_client(transport, *, store=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=transport, store=store)


def _paper_client(transport, *, store=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="paper", transport=transport, store=store)


# =====================================================================
# T1: 야간 발주 골든바디 -- FUOP 필수 + STTN1101U
# =====================================================================
def test_make_order_request_night_populates_fuop_and_uses_sttn():
    req = fo.make_order_request(_order(client_order_id="c"), cano="81012345",
                                product_code="03", environment="real")
    assert req.tr_id == "STTN1101U"
    assert req.body["FUOP_ITEM_DVSN_CD"] == "01"       # 야간은 상품구분 필수(원지문 derivative_item)
    assert req.body["ORD_QTY"] == "2"
    assert req.body["UNIT_PRICE"] == "400.00"


def test_night_call_option_populates_fuop_02():
    req = fo.make_order_request(_order(derivative_item="02", symbol="201S03310"),
                                cano="8", product_code="03", environment="real")
    assert req.body["FUOP_ITEM_DVSN_CD"] == "02"


# =====================================================================
# T6: paper + 야간 이중거부 -- 와이어 미접촉 + claim 미발생
# =====================================================================
def test_night_paper_place_rejected_no_side_effects():
    store = OrderStore()
    fake = FakeTransport()                              # 어떤 호출이든 unexpected -> AssertionError
    client = _paper_client(fake, store=store)
    with pytest.raises(KISUsageError):
        client._place_order(_order())
    assert fake.request_count == 0                      # 와이어 미접촉
    assert not store.is_in_flight("n1")                 # claim 미발생


def test_night_paper_reconcile_fails_closed_usage_error():
    # 야간은 실전 전용 -- paper 야간 지문 도달은 손상 신호라 KISUsageError.
    store = OrderStore()
    store.try_claim("n1", _fp())
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        fo.reconcile(fake, store, "n1", cano="1", product_code="03", environment="paper")
    assert fake.request_count == 0


# =====================================================================
# T2: 야간 취소 -- 신선 잔량(로컬 리포트 stale) + 조회 실패 fail-closed
# =====================================================================
def test_night_cancel_uses_fresh_remaining():
    store = OrderStore()
    place = FakeTransport()
    place.queue(_PLACE, _PLACE_OK)
    rep = _real_client(place, store=store)._place_order(_order())   # 로컬 리포트: quantity 2, filled 0
    assert rep.order_id == "0000005605"

    change_t = FakeTransport()
    # 취소 직전 신선 잔량 조회 -- 실제 잔량 1(로컬 리포트 잔량 2 가 아님).
    change_t.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605", qty="1")]))
    change_t.queue(_CHANGE, _CHANGE_OK)
    _real_client(change_t, store=store).orders.cancel(rep.client_order_id)

    change_call = next(c for c in change_t.calls if c["path"] == _CHANGE)
    assert change_call["tr_id"] == "TTTN1103U"
    assert change_call["body"]["ORD_QTY"] == "1"        # 신선 잔량(stale 2 아님)
    assert change_call["body"]["RMN_QTY_YN"] == "Y"
    assert change_call["body"]["FUOP_ITEM_DVSN_CD"] == "01"
    assert change_call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert change_call["body"]["ORGN_ODNO"] == "0000005605"


def test_night_cancel_fetch_error_fails_closed_no_wire():
    store = OrderStore()
    place = FakeTransport(); place.queue(_PLACE, _PLACE_OK)
    rep = _real_client(place, store=store)._place_order(_order())

    change_t = FakeTransport()
    change_t.queue(_NIGHT_INQUIRY, RawResponse(rt_cd="7", msg_cd="E", msg1="조회 실패", body={}))
    change_t.queue(_CHANGE, _CHANGE_OK)
    with pytest.raises(KISError):
        _real_client(change_t, store=store).orders.cancel(rep.client_order_id)
    assert not any(c["path"] == _CHANGE for c in change_t.calls)     # 취소 와이어 미접촉


def test_night_cancel_zero_remaining_rows_fails_closed():
    store = OrderStore()
    place = FakeTransport(); place.queue(_PLACE, _PLACE_OK)
    rep = _real_client(place, store=store)._place_order(_order())

    change_t = FakeTransport()
    change_t.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))                     # 0행 -> fail-closed
    change_t.queue(_CHANGE, _CHANGE_OK)
    with pytest.raises(KISError):
        _real_client(change_t, store=store).orders.cancel(rep.client_order_id)
    assert not any(c["path"] == _CHANGE for c in change_t.calls)


# =====================================================================
# T3: 야간 지정가 정정 골든바디 -- TTTN1103U + FUOP + 원지문 코드 산출
# =====================================================================
def test_night_modify_golden_body():
    # 야간 지정가 정정(잔량 재지정)의 well-formed 바디: TR TTTN1103U, RVSE_CNCL_DVSN_CD="01",
    # FUOP_ITEM_DVSN_CD 는 원지문 상품구분(01), 세 주문구분 코드는 원지문(limit,day) -> (01,01,0).
    from kis_trader.report import ExecutionReport, OrderStatus
    report = ExecutionReport(client_order_id="n1", order_id="0000005605", symbol="101S03",
                             side="buy", status=OrderStatus.NEW, filled_quantity=Decimal(0),
                             average_price=None, recorded_at=datetime.now(_KST))
    req = fo.make_night_change_request(
        original_report=report, original_fingerprint=_fp(order_type="limit"),
        action="modify", quantity=Decimal(1), limit_price=Decimal("401.00"),
        cano="8", product_code="03", environment="real",
    )
    assert req.path == _CHANGE
    assert req.tr_id == "TTTN1103U"
    assert req.body["RVSE_CNCL_DVSN_CD"] == "01"
    assert req.body["FUOP_ITEM_DVSN_CD"] == "01"       # 야간 필수(원지문 derivative_item)
    assert req.body["UNIT_PRICE"] == "401.00"
    assert req.body["ORD_QTY"] == "1"
    assert req.body["ORD_DVSN_CD"] == "01"             # (limit, day) 원지문 산출
    assert req.body["NMPR_TYPE_CD"] == "01"
    assert req.body["KRX_NMPR_CNDT_CD"] == "0"
    assert req.body["RMN_QTY_YN"] == "Y"               # 야간은 잔량 전체가 대상
    assert req.body["ORGN_ODNO"] == "0000005605"


def test_fuop_dvsn_from_symbol_invalid_length_fails_closed():
    # 6(선물)/9(옵션) 이 아닌 심볼 길이는 예상 밖 형상 -> KISError(엉뚱한 구분으로 훑지 않는다).
    with pytest.raises(KISError):
        fo._fuop_dvsn_from_symbol("1234")


# =====================================================================
# T5: union reconcile -- 이관 전/후/중, 병합, all-or-nothing, 창 결정성
# =====================================================================
_ANCHOR = datetime(2022, 1, 13, 23, 0, tzinfo=_KST)   # 목요일 야간 세션
_NOW = datetime(2022, 1, 14, 6, 30, tzinfo=_KST)


def _night_store(cid="n1"):
    store = OrderStore(now=lambda: _ANCHOR)
    store.try_claim(cid, _fp())
    return store


def _reconcile(fake, store, cid="n1"):
    return fo.reconcile(fake, store, cid, cano="81012345", product_code="03",
                        environment="real", now=_NOW)


def test_night_reconcile_a_only_confirms():
    # 이관 전: 야간 테이블(A)에만 1건, 주간(B) 0건 -> 확정.
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605")]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    report = _reconcile(fake, store)
    assert report is not None and report.order_id == "0000005605"


def test_night_reconcile_b_only_confirms_t_plus_one_dated():
    # 이관 후: 주간(B)에만 1건(T+1 일자), 야간(A) 0건 -> 확정.
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="0000005605", ord_dt="20220117")]))
    report = _reconcile(fake, store)
    assert report is not None and report.order_id == "0000005605"


def test_night_reconcile_merges_on_equal_odno_and_ord_dt():
    # 이관 중: 양 테이블에 (odno, ord_dt) 둘 다 동일한 같은 주문 -> 병합 확정(이중확정 아님).
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605", ord_dt="20220114")]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="0000005605", ord_dt="20220114")]))
    report = _reconcile(fake, store)
    assert report is not None and report.order_id == "0000005605"


def test_night_reconcile_differing_odno_raises():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605")]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="0000009999")]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_differing_ord_dt_raises():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605", ord_dt="20220114")]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="0000005605", ord_dt="20220117")]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_leg_a_failure_raises_even_if_b_ok():
    # all-or-nothing: 다리 A 실패면 B 가 정상이라도 확정하지 않는다.
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, RawResponse(rt_cd="7", msg_cd="E", msg1="야간 조회 실패", body={}))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="0000005605")]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_leg_b_failure_raises_even_if_a_matches():
    # all-or-nothing 반대 순서: 다리 A 가 단일매칭이어도 B 실패면 확정하지 않는다.
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="0000005605")]))
    fake.queue(_DAY_INQUIRY, RawResponse(rt_cd="7", msg_cd="E", msg1="주간 조회 실패", body={}))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_both_zero_returns_none():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    assert _reconcile(fake, store) is None


def test_night_reconcile_two_in_night_table_raises():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([_night_row(odno="1"), _night_row(odno="2")]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_two_in_day_table_raises():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([_day_row(odno="1"), _day_row(odno="2")]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_non_list_output1_fails_closed():
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                                           body={"output1": {"odno": "x"}}))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    with pytest.raises(KISError):
        _reconcile(fake, store)


def test_night_reconcile_date_window_is_deterministic():
    # 앵커(2022-01-13 목요일) 기준 T-0 ~ T+3 영업일: 목-금-(주말 롤)-월-화 = 20220113 ~ 20220118.
    store = _night_store()
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    _reconcile(fake, store)
    day_params = next(c["params"] for c in fake.calls if c["path"] == _DAY_INQUIRY)
    ngt_params = next(c["params"] for c in fake.calls if c["path"] == _NIGHT_INQUIRY)
    assert day_params["STRT_ORD_DT"] == "20220113"
    assert day_params["END_ORD_DT"] == "20220118"       # 주간 END 는 포함(inclusive)
    assert ngt_params["STRT_ORD_DT"] == "20220113"
    assert ngt_params["END_ORD_DT"] == "20220119"       # 야간 END 는 배타(exclusive) -> +1일
    assert ngt_params["FUOP_DVSN_CD"] == "01"           # 심볼 길이 6 -> 선물


def test_night_reconcile_legacy_blank_anchor_uses_wide_window():
    # 구 v7 레코드(claim 시각 "") -> 최광폭 창(now 기준 T-7 ~ 오늘).
    store = OrderStore()
    store.try_claim("n1", _fp())
    # v7 폴백을 흉내내려 claim 시각을 ""로 만든다(구 저장소 로드 경로와 동일한 앵커).
    store._in_flight["n1"] = ""      # 시각 미상 폴백 앵커
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([]))
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    _reconcile(fake, store)
    day_params = next(c["params"] for c in fake.calls if c["path"] == _DAY_INQUIRY)
    assert day_params["STRT_ORD_DT"] == "20220107"       # 20220114 - 7
    assert day_params["END_ORD_DT"] == "20220114"


class _DateWindowNightTransport:
    """야간 조회를 STRT/END 창(END 배타)으로 실제 필터한다 -- 창에 든 ord_dt 행만 돌려준다.

    FakeTransport 는 날짜 파라미터를 무시해 창 오류를 감추지만, 이 전송은 창 밖 행을 실제로
    떨어뜨려 신선조회 앵커가 잘못되면(뒷방향 창) 0행 -> KISError 로 드러나게 한다."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                           "params": params, "body": body})
        if path == _NIGHT_INQUIRY:
            strt, end = params["STRT_ORD_DT"], params["END_ORD_DT"]     # 야간 END 는 배타
            hit = [r for r in self._rows if strt <= r["ord_dt"] < end]
            return _ngt_ccnl(hit)
        if path == _CHANGE:
            return _CHANGE_OK
        raise AssertionError(f"unexpected {method} to {path}")


def _recorded_night_store(*, recorded, cid="n1", quantity="2", filled="0", order_id="0000005605"):
    # 접수 확정 상태(in-flight 아님)로 스토어를 심는다 -- 정정·취소는 확정 주문에만 걸리므로
    # 이 상태가 실제 변경 경로의 입력이다. recorded_at 을 고정해 신선조회 날짜창을 결정적으로.
    from kis_trader.report import ExecutionReport, OrderStatus
    store = OrderStore()
    report = ExecutionReport(client_order_id=cid, order_id=order_id, symbol="101S03",
                             side="buy", status=OrderStatus.NEW, filled_quantity=Decimal(filled),
                             average_price=None, recorded_at=recorded)
    store.record(report, _fp(quantity=quantity))
    return store


# =====================================================================
# P1-1: 야간 취소 공개경로 -- 신선조회 앵커는 접수(recorded) 일자(claim 이 아님)
# =====================================================================
_REC = datetime(2022, 1, 14, 23, 30, tzinfo=_KST)      # 금요일 야간 세션(T)
_TPLUS1 = "20220117"                                    # 야간 주문일자 T+1(다음 거래일=월)


def test_night_cancel_public_route_anchors_on_recorded_date():
    # 확정 야간주문을 공개경로로 취소한다. 신선조회 창은 접수일(T) 기준 전방창이라 T+1 일자
    # 행을 포함해 신선잔량이 잡히고 TTTN1103U 취소가 와이어에 닿는다(예전엔 claim 앵커가 None
    # 이라 뒷방향 창 -> T+1 미포함 -> 0행 -> KISError 로 취소가 와이어에 닿지 못했다).
    store = _recorded_night_store(recorded=_REC)                       # 로컬 잔량 2
    transport = _DateWindowNightTransport(
        [_night_row(odno="0000005605", ord_dt=_TPLUS1, ord_qty="2", tot_ccld_qty="1")])
    _real_client(transport, store=store).orders.cancel("n1")
    change_call = next(c for c in transport.calls if c["path"] == _CHANGE)
    assert change_call["tr_id"] == "TTTN1103U"
    assert change_call["body"]["ORD_QTY"] == "1"                       # 신선 잔량(2-1)
    assert change_call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    ngt = next(c["params"] for c in transport.calls if c["path"] == _NIGHT_INQUIRY)
    assert ngt["STRT_ORD_DT"] == "20220114"                            # 접수일 기준(claim 아님)
    assert ngt["STRT_ORD_DT"] <= _TPLUS1 < ngt["END_ORD_DT"]          # 전방창이 T+1 을 덮는다


def test_night_modify_public_route_uses_fresh_remaining_before_wire():
    # P1-3: 야간 정정 공개경로 -- 신선조회가 TTTN1103U 정정보다 먼저 돌고 신선잔량을 싣는다.
    store = _recorded_night_store(recorded=_REC)
    transport = _DateWindowNightTransport(
        [_night_row(odno="0000005605", ord_dt=_TPLUS1, ord_qty="2", tot_ccld_qty="1")])
    _real_client(transport, store=store).orders.modify("n1", limit_price=Decimal("401.00"))
    change_call = next(c for c in transport.calls if c["path"] == _CHANGE)
    assert change_call["tr_id"] == "TTTN1103U"
    assert change_call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert change_call["body"]["UNIT_PRICE"] == "401.00"
    assert change_call["body"]["ORD_QTY"] == "1"                       # 신선 잔량
    idx_inq = next(i for i, c in enumerate(transport.calls) if c["path"] == _NIGHT_INQUIRY)
    idx_chg = next(i for i, c in enumerate(transport.calls) if c["path"] == _CHANGE)
    assert idx_inq < idx_chg                                           # 조회가 와이어보다 먼저


def test_night_modify_public_route_inquiry_failure_no_wire():
    # P1-3: 신선조회가 창 밖(0행)이면 KISError 로 fail-closed 하고 정정 와이어에 닿지 않는다.
    store = _recorded_night_store(recorded=_REC)
    transport = _DateWindowNightTransport(
        [_night_row(odno="0000005605", ord_dt="20991231", ord_qty="2", tot_ccld_qty="1")])
    with pytest.raises(KISError):
        _real_client(transport, store=store).orders.modify("n1", limit_price=Decimal("401.00"))
    assert not any(c["path"] == _CHANGE for c in transport.calls)


# =====================================================================
# P2-5: 야간 부분 정정·취소 미지원 -- 명시 quantity != 전량이면 와이어 미접촉
# =====================================================================
def test_night_partial_cancel_rejected_no_wire():
    store = _recorded_night_store(recorded=_REC, quantity="5")         # 로컬 잔량 5
    fake = FakeTransport()                                             # 어떤 호출이든 unexpected
    with pytest.raises(KISUsageError):
        _real_client(fake, store=store).orders.cancel("n1", quantity=Decimal(2))
    assert fake.request_count == 0                                     # 신선조회조차 미접촉


def test_night_partial_modify_rejected_no_wire():
    store = _recorded_night_store(recorded=_REC, quantity="5")
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _real_client(fake, store=store).orders.modify(
            "n1", quantity=Decimal(2), limit_price=Decimal("401.00"))
    assert fake.request_count == 0


def test_night_full_cancel_with_explicit_quantity_equal_remaining_ok():
    # 전량과 같은 명시 quantity 는 통과(부분이 아님) -- 이후 신선잔량으로 덮인다.
    store = _recorded_night_store(recorded=_REC, quantity="2")         # 로컬 잔량 2
    transport = _DateWindowNightTransport(
        [_night_row(odno="0000005605", ord_dt=_TPLUS1, ord_qty="2", tot_ccld_qty="1")])
    _real_client(transport, store=store).orders.cancel("n1", quantity=Decimal(2))
    assert any(c["path"] == _CHANGE for c in transport.calls)


# =====================================================================
# P2-6: 신선 잔량 = 주문수량 - 체결수량(미검증 qty 필드 불신), 필드 누락 fail-closed
# =====================================================================
def test_fetch_night_remaining_computes_ordered_minus_filled():
    fake = FakeTransport()
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl(
        [_night_row(odno="0000005605", ord_qty="5", tot_ccld_qty="3", qty="99")]))
    remaining = fo.fetch_night_remaining(
        fake, order_id="0000005605", symbol="101S03", cano="8", product_code="03",
        environment="real", anchor=_REC.isoformat())
    assert remaining == Decimal(2)                                     # 5-3, 미검증 qty=99 무시


def test_fetch_night_remaining_missing_field_fails_closed():
    fake = FakeTransport()
    row = _night_row(odno="0000005605", ord_qty="5")
    del row["tot_ccld_qty"]                                            # 체결수량 누락 -> fail-closed
    fake.queue(_NIGHT_INQUIRY, _ngt_ccnl([row]))
    with pytest.raises(KISError):
        fo.fetch_night_remaining(
            fake, order_id="0000005605", symbol="101S03", cano="8", product_code="03",
            environment="real", anchor=_REC.isoformat())


def test_day_session_reconcile_still_single_day():
    # 회귀: 주간(regular) 지문은 종전대로 당일 하루만 조회한다(union 경로로 새지 않는다).
    store = OrderStore()
    store.try_claim("d1", _fp(session="regular"))
    fake = FakeTransport()
    fake.queue(_DAY_INQUIRY, _day_ccnl([]))
    fo.reconcile(fake, store, "d1", cano="1", product_code="03", environment="real",
                 now=datetime(2022, 1, 14, 10, 0, tzinfo=_KST))
    params = next(c["params"] for c in fake.calls if c["path"] == _DAY_INQUIRY)
    assert params["STRT_ORD_DT"] == params["END_ORD_DT"] == "20220114"
    assert not any(c["path"] == _NIGHT_INQUIRY for c in fake.calls)

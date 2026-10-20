"""국내 선물·옵션 주간(DAY) 보수적 재조회 -- kis.orders.reconcile 의 XKFE 경로.

저장된 주문번호가 있는 파생 주문을 일별 체결내역(inquire-ccnl, TTTO5201R)으로 재조회해
정확히 1건이면 갱신, 0건이면 None, 2건 이상/스캔 실패는 KISError 로
fail-closed 하는지 검증한다. 매칭은 원장 output1 에 실재하는 ``nmpr_type_cd`` 기반이다
(그 행에는 ``ord_dvsn_cd`` 가 없다). 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStatus
from kis_trader.domestic._engine import derivative_orders as fo
from kis_trader.errors import KISError, OrderTimeoutError
from kis_trader.store import OrderStore
from kis_trader.transport import RawResponse, TransportTimeoutError

_INQUIRY = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl"
_KST = timezone(timedelta(hours=9))


class FakeTransport:
    """POST(발주)와 GET(재조회)을 갈라 응답/예외를 준다. 재조회는 리스트면 페이지별 순차."""

    def __init__(self, *, on_post=None, on_get=None):
        self.on_post = on_post
        self.on_get = on_get
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "body": body, "idempotent": idempotent})
        if method == "POST" and self.on_post is not None:
            outcome = self.on_post.pop(0) if isinstance(self.on_post, list) else self.on_post
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if method == "GET" and self.on_get is not None:
            outcome = self.on_get.pop(0) if isinstance(self.on_get, list) else self.on_get
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        raise AssertionError(f"unexpected {method} to {path}")


def _ccnl(rows, **body_extra):
    body = {"output1": rows, "output2": {"tot_ccld_qty": "1"}}
    body.update(body_extra)
    return RawResponse(rt_cd="0", msg_cd="0", msg1="정상", body=body)


def _row(*, odno="0000007045", orgn_odno="0000000000", side="02", nmpr="01", pdno="101S03",
         ord_qty="1", ord_idx="400.00", qty="0", tot_ccld_qty="1", rjct_qty="0"):
    # 원장(국내선물옵션 일별체결내역) output1 응답예시 실값. ord_dvsn_cd 는 이 행에 없다.
    return {"odno": odno, "orgn_odno": orgn_odno, "sll_buy_dvsn_cd": side,
            "nmpr_type_cd": nmpr, "pdno": pdno, "ord_qty": ord_qty, "ord_idx": ord_idx,
            "qty": qty, "tot_ccld_qty": tot_ccld_qty, "rjct_qty": rjct_qty, "sprd_item_yn": "N"}


def _order(**kw):
    base = {"symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
            "limit_price": Decimal("400.00"), "asset_class": "derivative", "session": "regular",
            "derivative_item": "01"}
    base.update(kw)
    return Order(**base)


def _client(transport):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=transport)


_ACCEPTED = RawResponse(rt_cd="0", msg_cd="0", msg1="접수", body={"output": {"ODNO": "0000007045"}})


def _place_accepted(client, client_order_id, **order_kw):
    """실제 발주 파서로 접수 주문번호를 저장한 뒤 refresh 경로를 검증한다."""
    report = client._place_order(_order(client_order_id=client_order_id, **order_kw))
    assert report.order_id == "0000007045"


def _place_timeout(client, client_order_id, **order_kw):
    """발주를 timeout 시켜 in-flight 로 만든다."""
    with pytest.raises(OrderTimeoutError):
        client._place_order(_order(client_order_id=client_order_id, **order_kw))


def test_day_reconcile_confirms_single_match():
    # 접수 발주 뒤 지정가 매수 1계약 @400.00 지문과 정확히 1건(nmpr_type_cd=01) -> 확정.
    from kis_trader import ExecutionReport, OrderStatus
    fake = FakeTransport(on_post=_ACCEPTED, on_get=_ccnl([_row()]))
    client = _client(fake)
    _place_accepted(client, "day-1")
    report = client.orders.reconcile("day-1", refresh=True)
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    assert report.filled_quantity == Decimal(1)
    assert report.status is OrderStatus.FILLED
    get = fake.calls[-1]
    assert get["path"] == _INQUIRY
    assert get["tr_id"] == "TTTO5201R"
    assert get["params"]["PDNO"] == "101S03"
    assert get["params"]["SLL_BUY_DVSN_CD"] == "00"
    assert get["params"]["CCLD_NCCS_DVSN"] == "00"
    assert get["params"]["MKET_ID_CD"] == "00"


def test_day_reconcile_matches_on_nmpr_not_ord_dvsn():
    # 시장가(nmpr_type_cd=02) 주문은 nmpr=02 행에만 맞는다 -- ord_dvsn_cd 부재에도 매칭 성립.
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=_ccnl([_row(nmpr="02", ord_idx="0")]))
    client = _client(fake)
    _place_accepted(client, "mkt-1", order_type="market", limit_price=None)
    report = client.orders.reconcile("mkt-1", refresh=True)
    assert report is not None and report.order_id == "0000007045"
    assert fo._filter_matching_day_rows([_row(nmpr="02", ord_idx="0")],
                                        client._store.fingerprint_for("mkt-1"))


def test_day_reconcile_wrong_nmpr_is_zero_match():
    # 지정가(nmpr=01) 지문인데 행이 시장가(nmpr=02)면 매칭 없음 -> None(미접수로 단정 안 함).
    fake = FakeTransport(on_post=TransportTimeoutError("t"),
                         on_get=_ccnl([_row(nmpr="02")]))
    client = _client(fake)
    _place_timeout(client, "z-nmpr")
    assert client.orders.reconcile("z-nmpr") is None
    assert fo._filter_matching_day_rows([_row(nmpr="02")], client._store.fingerprint_for("z-nmpr")) == []
    assert not any(call["method"] == "GET" for call in fake.calls)


def test_day_reconcile_zero_matches_stays_none():
    fake = FakeTransport(on_post=TransportTimeoutError("t"),
                         on_get=_ccnl([_row(pdno="201S03")]))
    client = _client(fake)
    _place_timeout(client, "z-pdno")
    assert client.orders.reconcile("z-pdno") is None
    assert fo._filter_matching_day_rows([_row(pdno="201S03")], client._store.fingerprint_for("z-pdno")) == []
    assert not any(call["method"] == "GET" for call in fake.calls)


def test_day_reconcile_excludes_modify_cancel_rows():
    # orgn_odno 가 원주문번호(!=0)인 정정/취소 행은 원주문 지문에 매칭하지 않는다 -> None.
    fake = FakeTransport(on_post=TransportTimeoutError("t"),
                         on_get=_ccnl([_row(orgn_odno="0000007045")]))
    client = _client(fake)
    _place_timeout(client, "mod-1")
    assert client.orders.reconcile("mod-1") is None
    assert fo._filter_matching_day_rows([_row(orgn_odno="0000007045")], client._store.fingerprint_for("mod-1")) == []
    assert not any(call["method"] == "GET" for call in fake.calls)


def test_day_reconcile_two_matches_raises():
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=_ccnl([_row(odno="0000007045"), _row(odno="0000007045")]))
    client = _client(fake)
    _place_accepted(client, "m2")
    with pytest.raises(KISError, match="2건"):
        client.orders.reconcile("m2", refresh=True)


def test_day_reconcile_error_response_fails_closed():
    # rt_cd != "0" (조회 실패)는 빈 결과로 오인하지 않고 KISError -- 미접수 단정 -> 이중체결 방지.
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=RawResponse(rt_cd="7", msg_cd="E", msg1="조회 실패", body={}))
    client = _client(fake)
    _place_accepted(client, "err")
    with pytest.raises(KISError):
        client.orders.reconcile("err", refresh=True)


def test_day_reconcile_page_cap_fails_closed():
    # 연속조회 커서가 끝없이 남으면(ctx_area_nk200 항상 존재) 페이지 상한에서 부분 스캔으로
    # 확정하지 않고 KISError.
    # 커서를 매 페이지 진전시켜(N0, N1, ...) 진짜 다음 페이지가 계속 있는 상황을 흉내낸다 -- 같은 키
    # 반복은 이제 비진전 커서로 보아 종료하므로, cap 을 치려면 커서가 실제로 진전해야 한다.
    pages = [RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                         body={"output1": [], "ctx_area_nk200": f"N{i}"}) for i in range(200)]
    fake = FakeTransport(on_post=_ACCEPTED, on_get=pages)
    client = _client(fake)
    _place_accepted(client, "cap")
    with pytest.raises(KISError, match="페이지 상한"):
        client.orders.reconcile("cap", refresh=True)


def test_day_reconcile_non_finite_numeric_fails_closed():
    # 수량 필드가 "nan" 이면 비유한값이라 비교가 무너져 오확정을 부른다 -> KISError.
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=_ccnl([_row(ord_qty="nan")]))
    client = _client(fake)
    _place_accepted(client, "nan")
    with pytest.raises(KISError):
        client.orders.reconcile("nan", refresh=True)


def test_day_reconcile_output1_not_a_list_raises():
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                                            body={"output1": {"odno": "x"}}))
    client = _client(fake)
    _place_accepted(client, "notlist")
    with pytest.raises(KISError):
        client.orders.reconcile("notlist", refresh=True)


def test_day_reconcile_mixed_list_non_mapping_raises():
    # 리스트 안에 Mapping 아닌 원소가 섞이면 부분/오응답으로 확정하지 않는다.
    fake = FakeTransport(on_post=_ACCEPTED,
                         on_get=_ccnl([_row(), "not-a-mapping"]))
    client = _client(fake)
    _place_accepted(client, "mixed")
    with pytest.raises(KISError):
        client.orders.reconcile("mixed", refresh=True)


def test_day_reconcile_paginates_across_pages():
    page1 = RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                        body={"output1": [_row(pdno="201S03", odno="sibling")], "ctx_area_nk200": "NEXT"})
    page2 = _ccnl([_row()])
    fake = FakeTransport(on_post=_ACCEPTED, on_get=[page1, page2])
    client = _client(fake)
    _place_accepted(client, "pg")
    report = client.orders.reconcile("pg", refresh=True)
    assert report is not None and report.order_id == "0000007045"
    gets = [c for c in fake.calls if c["method"] == "GET"]
    assert len(gets) == 2
    assert gets[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_day_reconcile_timeout_raises_order_timeout():
    fake = FakeTransport(on_post=_ACCEPTED, on_get=TransportTimeoutError("scan"))
    client = _client(fake)
    _place_accepted(client, "to")
    with pytest.raises(OrderTimeoutError):
        client.orders.reconcile("to", refresh=True)


def test_day_reconcile_date_window_is_deterministic():
    # 날짜창은 주입 시각(now) 기준 -- 벽시계 비의존. 국내(KST) 당일 하루.
    store = OrderStore()
    order = _order(client_order_id="d1")
    store.try_claim("d1", order.fingerprint)
    _record_prior(store, order, status=OrderStatus.NEW)
    fake = FakeTransport(on_get=_ccnl([]))
    fo.reconcile(fake, store, "d1", cano="1", product_code="03", environment="real",
                 now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST), force_refresh=True)
    params = fake.calls[0]["params"]
    assert params["STRT_ORD_DT"] == "20240315"
    assert params["END_ORD_DT"] == "20240315"


def test_day_reconcile_paper_uses_paper_tr():
    store = OrderStore()
    order = _order(client_order_id="pp")
    store.try_claim("pp", order.fingerprint)
    _record_prior(store, order, status=OrderStatus.NEW)
    fake = FakeTransport(on_get=_ccnl([]))
    fo.reconcile(fake, store, "pp", cano="1", product_code="03", environment="paper", force_refresh=True)
    assert fake.calls[0]["tr_id"] == "VTTO5201R"


def test_day_reconcile_night_session_uses_union_scan():
    # 야간(STTN) 재조회는 두 테이블(야간 + 주간)의 union 스캔이다 -- 양측 0건이면 None(미접수 단정
    # 금지, in-flight 유지)이고, 야간 전용 조회 경로를 함께 훑는다.
    store = OrderStore()
    order = _order(client_order_id="nite", session="night")
    store.try_claim("nite", order.fingerprint)
    _record_prior(store, order, status=OrderStatus.NEW)
    fake = FakeTransport(on_get=_ccnl([]))
    assert fo.reconcile(fake, store, "nite", cano="1", product_code="03", environment="real", force_refresh=True) is None
    paths = {c["path"] for c in fake.calls if c["method"] == "GET"}
    assert "/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl" in paths
    assert _INQUIRY in paths


def _record_prior(store, order, *, status, order_id="0000007045"):
    """저장소에 종료/비종료 prior 리포트를 심는다(force_refresh 재조회의 대상)."""
    from kis_trader import ExecutionReport, OrderStatus
    qty = Decimal(1) if status is OrderStatus.FILLED else Decimal(0)
    store.try_claim(order.client_order_id, order.fingerprint)
    report = ExecutionReport(
        client_order_id=order.client_order_id, order_id=order_id, symbol=order.symbol,
        side=order.side, status=status, filled_quantity=qty, average_price=None,
        recorded_at=datetime(2024, 3, 15, 10, 0, tzinfo=_KST),
    )
    store.record(report, order.fingerprint)


def test_day_refresh_terminal_prior_zero_match_returns_prior():
    # force_refresh 재조회가 당일창 밖 완료주문을 0건으로 긁어도 종료 prior 를 '미해소'로 퇴행시키지
    # 않는다 -- FILLED prior + 0건 -> 그 확정 리포트 그대로.
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-term")
    _record_prior(store, order, status=OrderStatus.FILLED, order_id="0000007045")
    fake = FakeTransport(on_get=_ccnl([]))
    report = fo.reconcile(fake, store, "rf-term", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    assert report is not None and report.status is OrderStatus.FILLED
    assert report.order_id == "0000007045"


def test_day_refresh_nonterminal_prior_zero_match_stays_none():
    # 비종료(NEW) prior 는 0건이면 미확정 유지(None) -- 접수직후 스냅샷을 체결로 둔갑시키지 않는다.
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-new")
    _record_prior(store, order, status=OrderStatus.NEW, order_id="0000007045")
    fake = FakeTransport(on_get=_ccnl([]))
    assert fo.reconcile(fake, store, "rf-new", cano="1", product_code="03",
                        environment="real", force_refresh=True,
                        now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST)) is None


def test_day_refresh_matches_stored_odno_not_sibling_fingerprint():
    # prior ODNO 가 있으면 force_refresh 는 지문이 아니라 그 ODNO 로 자기 행을 집는다 -- 동일 종목·
    # 수량·가격의 형제 ODNO 행(9999)을 이 주문으로 오귀속하지 않는다(phantom 확정 방지).
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-sib")
    _record_prior(store, order, status=OrderStatus.FILLED, order_id="0000007045")
    sibling = _row(odno="0000009999")  # 같은 지문, 다른 ODNO
    fake = FakeTransport(on_get=_ccnl([sibling]))
    report = fo.reconcile(fake, store, "rf-sib", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    # 형제(9999)로 재바인딩되지 않고, 종료 prior(7045)가 그대로 유지된다.
    assert report is not None and report.order_id == "0000007045"


def test_day_refresh_confirms_on_stored_odno_match():
    # prior ODNO 와 같은 ODNO 행이 있으면 그 행으로 현재 상태를 갱신한다(정상 refresh).
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-hit")
    _record_prior(store, order, status=OrderStatus.NEW, order_id="0000007045")
    fake = FakeTransport(on_get=_ccnl([_row(odno="0000007045", tot_ccld_qty="1")]))
    report = fo.reconcile(fake, store, "rf-hit", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    assert report is not None and report.order_id == "0000007045"


def test_day_refresh_blank_stored_odno_does_not_match_blank_row():
    # 저장 ODNO 가 전부-0("0000000000")으로 정규화돼 비면 prior 가 있으므로 else 분기(matches=[])로 가
    # (지문 폴백 금지), odno 가 빈 무관한 행("" -> "")을 이 주문으로 오매칭하지 않는다 -> 종료 prior 유지.
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-zero")
    _record_prior(store, order, status=OrderStatus.FILLED, order_id="0000000000")
    fake = FakeTransport(on_get=_ccnl([_row(odno="", pdno="201S03")]))  # 다른 종목 + 빈 odno
    report = fo.reconcile(fake, store, "rf-zero", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    assert report is None
    assert store.report_for("rf-zero").order_id == "0000000000"
    assert fake.calls == []


def test_day_refresh_excludes_modify_cancel_row_with_same_odno():
    # ODNO 로 집더라도 정정/취소 행(orgn_odno != 0)이 같은 odno 로 돌아오면 원주문 체결로 오확정하지
    # 않는다 -- 그 행 제외 -> 0건 -> 종료 prior 유지.
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-mod")
    _record_prior(store, order, status=OrderStatus.FILLED, order_id="0000007045")
    # 같은 odno 이지만 정정/취소 행(orgn_odno != 0)이고 상태는 미체결(NEW) -- 이 행이 채택되면 종료
    # prior(FILLED)가 NEW 로 뒤집혀 구별된다(오매칭이면 FILLED != NEW 로 드러난다).
    fake = FakeTransport(on_get=_ccnl([_row(odno="0000007045", orgn_odno="0000007045", tot_ccld_qty="0")]))
    report = fo.reconcile(fake, store, "rf-mod", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    assert report is not None and report.status is OrderStatus.FILLED  # 변경 행 제외 -> 종료 prior 유지


def test_day_refresh_unusable_odno_prior_does_not_fingerprint_confirm_sibling():
    # prior 는 있으나 쓸 수 있는 ODNO 가 없으면(order_id None/전부-0) 지문 폴백으로 내려가 동일지문 형제를
    # 오확정하지 않는다 -- 지문 폴백은 prior 없는 타임아웃 복구 전용이므로, prior 가 있는 손상 상태에선
    # 0건 처리(비종료 -> None). 형제 행(같은 종목·수량·가격, 다른 ODNO)이 와도 확정하지 않는다.
    from kis_trader import OrderStatus
    store = OrderStore()
    order = _order(client_order_id="rf-noid")
    _record_prior(store, order, status=OrderStatus.NEW, order_id=None)  # 쓸 수 없는 ODNO
    sibling = _row(odno="0000009999")  # 같은 지문의 형제 (지문 폴백이면 오매칭됐을 행)
    fake = FakeTransport(on_get=_ccnl([sibling]))
    assert fo.reconcile(fake, store, "rf-noid", cano="1", product_code="03",
                        environment="real", force_refresh=True,
                        now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST)) is None


def test_day_reconcile_blank_odno_nonterminal_row_is_not_recorded():
    # 지문 폴백(타임아웃 복구)이 odno 가 빈 **비종료**(미체결 NEW) 행을 물면 order_id=None 리포트를
    # 만들지 않는다 -- 그런 비종료 리포트는 이후 refresh 재조회가 ODNO 로 자기 행을 못 집어 영구 교착
    # (None)에 빠지므로, 확정하지 않고 in-flight 유지(None, 저장소에 리포트 없음).
    fake = FakeTransport(on_post=TransportTimeoutError("t"), on_get=_ccnl([_row(odno="", tot_ccld_qty="0")]))
    client = _client(fake)
    _place_timeout(client, "blank-nt")
    assert client.orders.reconcile("blank-nt") is None
    assert client._store.report_for("blank-nt") is None       # order_id=None 비종료 리포트 미기록
    assert client._store.is_in_flight("blank-nt")             # in-flight 유지(수동 확인)


def test_day_reconcile_blank_odno_terminal_fill_remains_unknown():
    fake = FakeTransport(on_post=TransportTimeoutError("t"),
                         on_get=_ccnl([_row(odno="", tot_ccld_qty="1")]))  # 전량체결
    client = _client(fake)
    _place_timeout(client, "blank-term")
    report = client.orders.reconcile("blank-term")
    assert report is None
    store = client._store
    cid = "blank-term"
    assert store.report_for(cid) is None and store.is_in_flight(cid)
    assert not any(call["method"] == "GET" for call in fake.calls)


def test_day_refresh_does_not_overwrite_concurrent_modify_rebind():
    # reconcile 이 스냅샷을 읽은 뒤 조회(I/O) 중에 동시 modify 가 이 id 를 새 ODNO(B)로 재바인딩하면,
    # reconcile 의 옛 세대(A) 스캔 결과가 그 B 바인딩을 덮어쓰지 않는다(CAS 실패 -> 현재 B 반환) --
    # 안 그러면 이후 cancel 이 죽은 ODNO A 를 겨냥한다.
    from kis_trader import ExecutionReport, OrderStatus
    store = OrderStore()
    order = _order(client_order_id="race")
    _record_prior(store, order, status=OrderStatus.NEW, order_id="0000007045")   # 원 세대 A
    rebound = ExecutionReport(
        client_order_id="race", order_id="0000008888", symbol=order.symbol, side=order.side,
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))

    class _RebindOnGet:
        def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
            if method == "GET":
                store.record(rebound, order.fingerprint)          # 조회 중 동시 modify 재바인딩(B)
                return _ccnl([_row(odno="0000007045")])           # 옛 세대 A 행
            raise AssertionError(f"unexpected {method}")

    result = fo.reconcile(_RebindOnGet(), store, "race", cano="1", product_code="03",
                          environment="real", force_refresh=True,
                          now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    assert result is not None and result.order_id == "0000008888"        # 새 세대 B 반환
    assert store.report_for("race").order_id == "0000008888"             # B 유지(A 로 롤백 안 함)


def test_record_if_unchanged_rejects_stale_expected():
    # store CAS 단위 검증: 기대 스냅샷과 현재 바인딩이 다르면 기록하지 않고 False.
    from kis_trader import ExecutionReport, OrderStatus
    store = OrderStore()
    order = _order(client_order_id="cas")
    _record_prior(store, order, status=OrderStatus.NEW, order_id="0000000001")   # 현재 = A
    snap = store.snapshot_for("cas")                                             # (A, fp)
    newer = ExecutionReport(client_order_id="cas", order_id="0000000002", symbol=order.symbol,
                            side=order.side, status=OrderStatus.NEW, filled_quantity=Decimal(0),
                            average_price=None, recorded_at=datetime(2024, 3, 15, tzinfo=_KST))
    store.record(newer, order.fingerprint)                                       # 재바인딩 -> 현재 = B
    stale = ExecutionReport(client_order_id="cas", order_id="0000000009", symbol=order.symbol,
                            side=order.side, status=OrderStatus.NEW, filled_quantity=Decimal(0),
                            average_price=None, recorded_at=datetime(2024, 3, 15, tzinfo=_KST))
    assert store.record_if_unchanged(stale, order.fingerprint, expected=snap) is False
    assert store.report_for("cas").order_id == "0000000002"                      # B 유지
    assert store.record_if_unchanged(stale, order.fingerprint,
                                     expected=(newer, order.fingerprint)) is True
    assert store.report_for("cas").order_id == "0000000009"


def test_day_reconcile_night_session_on_paper_fails_closed():
    # 야간은 실전 전용 -- paper 야간 지문 도달은 손상 신호라 KISUsageError(조회 미접촉).
    from kis_trader.errors import KISUsageError
    store = OrderStore()
    order = _order(client_order_id="nite-pp", session="night")
    store.try_claim("nite-pp", order.fingerprint)
    _record_prior(store, order, status=OrderStatus.NEW)
    fake = FakeTransport(on_get=_ccnl([]))
    with pytest.raises(KISUsageError):
        fo.reconcile(fake, store, "nite-pp", cano="1", product_code="03", environment="paper", force_refresh=True)
    assert not any(c["method"] == "GET" for c in fake.calls)

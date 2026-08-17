"""국내 선물·옵션 주간(DAY) 보수적 재조회 -- kis.orders.reconcile 의 XKFE 경로.

미확인(in-flight) 파생 주문을 일별 체결내역(inquire-ccnl, TTTO5201R)으로 재조회해 지문과
정확히 1건이면 확정, 0건이면 None(재전송 금지 유지), 2건 이상/스캔 실패는 KISError 로
fail-closed 하는지 검증한다. 매칭은 원장 output1 에 실재하는 ``nmpr_type_cd`` 기반이다
(그 행에는 ``ord_dvsn_cd`` 가 없다). 픽스처는 원장 응답예시 실값을 쓴다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order
from kis_trader.domestic._engine import derivative_orders as fo
from kis_trader.errors import KISError, OrderTimeoutError
from kis_trader.store import OrderStore
from kis_trader.transport import RawResponse, TransportTimeout

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
            "limit_price": Decimal("400.00"), "exchange": "XKFE", "session": "regular",
            "derivative_item": "01"}
    base.update(kw)
    return Order(**base)


def _client(transport):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=transport)


def _place_timeout(client, client_order_id, **order_kw):
    """발주를 timeout 시켜 in-flight 로 만든다."""
    with pytest.raises(OrderTimeoutError):
        client._place_order(_order(client_order_id=client_order_id, **order_kw))


def test_day_reconcile_confirms_single_match():
    # timeout 발주 뒤 지정가 매수 1계약 @400.00 지문과 정확히 1건(nmpr_type_cd=01) -> 확정.
    from kis_trader import ExecutionReport, OrderStatus
    fake = FakeTransport(on_post=TransportTimeout("t"), on_get=_ccnl([_row()]))
    client = _client(fake)
    _place_timeout(client, "day-1")
    report = client.orders.reconcile("day-1")
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
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(nmpr="02", ord_idx="0")]))
    client = _client(fake)
    _place_timeout(client, "mkt-1", order_type="market", limit_price=None)
    report = client.orders.reconcile("mkt-1")
    assert report is not None and report.order_id == "0000007045"


def test_day_reconcile_wrong_nmpr_is_zero_match():
    # 지정가(nmpr=01) 지문인데 행이 시장가(nmpr=02)면 매칭 없음 -> None(미접수로 단정 안 함).
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(nmpr="02")]))
    client = _client(fake)
    _place_timeout(client, "z-nmpr")
    assert client.orders.reconcile("z-nmpr") is None


def test_day_reconcile_zero_matches_stays_none():
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(pdno="201S03")]))
    client = _client(fake)
    _place_timeout(client, "z-pdno")
    assert client.orders.reconcile("z-pdno") is None


def test_day_reconcile_excludes_modify_cancel_rows():
    # orgn_odno 가 원주문번호(!=0)인 정정/취소 행은 원주문 지문에 매칭하지 않는다 -> None.
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(orgn_odno="0000007045")]))
    client = _client(fake)
    _place_timeout(client, "mod-1")
    assert client.orders.reconcile("mod-1") is None


def test_day_reconcile_two_matches_raises():
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(odno="1"), _row(odno="2")]))
    client = _client(fake)
    _place_timeout(client, "m2")
    with pytest.raises(KISError, match="2건"):
        client.orders.reconcile("m2")


def test_day_reconcile_output1_not_a_list_raises():
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                                            body={"output1": {"odno": "x"}}))
    client = _client(fake)
    _place_timeout(client, "notlist")
    with pytest.raises(KISError):
        client.orders.reconcile("notlist")


def test_day_reconcile_mixed_list_non_mapping_raises():
    # 리스트 안에 Mapping 아닌 원소가 섞이면 부분/오응답으로 확정하지 않는다(BC-3).
    fake = FakeTransport(on_post=TransportTimeout("t"),
                         on_get=_ccnl([_row(), "not-a-mapping"]))
    client = _client(fake)
    _place_timeout(client, "mixed")
    with pytest.raises(KISError):
        client.orders.reconcile("mixed")


def test_day_reconcile_paginates_across_pages():
    page1 = RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                        body={"output1": [_row(pdno="201S03")], "ctx_area_nk200": "NEXT"})
    page2 = _ccnl([_row()])
    fake = FakeTransport(on_post=TransportTimeout("t"), on_get=[page1, page2])
    client = _client(fake)
    _place_timeout(client, "pg")
    report = client.orders.reconcile("pg")
    assert report is not None and report.order_id == "0000007045"
    gets = [c for c in fake.calls if c["method"] == "GET"]
    assert len(gets) == 2
    assert gets[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_day_reconcile_timeout_raises_order_timeout():
    fake = FakeTransport(on_post=TransportTimeout("t"), on_get=TransportTimeout("scan"))
    client = _client(fake)
    _place_timeout(client, "to")
    with pytest.raises(OrderTimeoutError):
        client.orders.reconcile("to")


def test_day_reconcile_date_window_is_deterministic():
    # 날짜창은 주입 시각(now) 기준 -- 벽시계 비의존. 국내(KST) 당일 하루.
    store = OrderStore()
    order = _order(client_order_id="d1")
    store.try_claim("d1", order.fingerprint)
    fake = FakeTransport(on_get=_ccnl([]))
    fo.reconcile(fake, store, "d1", cano="1", product_code="03", environment="real",
                 now=datetime(2024, 3, 15, 10, 0, tzinfo=_KST))
    params = fake.calls[0]["params"]
    assert params["STRT_ORD_DT"] == "20240315"
    assert params["END_ORD_DT"] == "20240315"


def test_day_reconcile_paper_uses_paper_tr():
    store = OrderStore()
    order = _order(client_order_id="pp")
    store.try_claim("pp", order.fingerprint)
    fake = FakeTransport(on_get=_ccnl([]))
    fo.reconcile(fake, store, "pp", cano="1", product_code="03", environment="paper")
    assert fake.calls[0]["tr_id"] == "VTTO5201R"


def test_day_reconcile_night_session_uses_union_scan():
    # 야간(STTN) 재조회는 두 테이블(야간 + 주간)의 union 스캔이다 -- 양측 0건이면 None(미접수 단정
    # 금지, in-flight 유지)이고, 야간 전용 조회 경로를 함께 훑는다.
    store = OrderStore()
    order = _order(client_order_id="nite", session="night")
    store.try_claim("nite", order.fingerprint)
    fake = FakeTransport(on_get=_ccnl([]))
    assert fo.reconcile(fake, store, "nite", cano="1", product_code="03", environment="real") is None
    paths = {c["path"] for c in fake.calls if c["method"] == "GET"}
    assert "/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl" in paths
    assert _INQUIRY in paths


def test_day_reconcile_night_session_on_paper_fails_closed():
    # 야간은 실전 전용 -- paper 야간 지문 도달은 손상 신호라 KISUsageError(조회 미접촉).
    from kis_trader.errors import KISUsageError
    store = OrderStore()
    order = _order(client_order_id="nite-pp", session="night")
    store.try_claim("nite-pp", order.fingerprint)
    fake = FakeTransport(on_get=_ccnl([]))
    with pytest.raises(KISUsageError):
        fo.reconcile(fake, store, "nite-pp", cano="1", product_code="03", environment="paper")
    assert not any(c["method"] == "GET" for c in fake.calls)

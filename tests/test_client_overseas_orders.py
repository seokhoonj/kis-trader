"""해외 주문 -- kis.overseas.stock(symbol, exchange=...).buy()/sell().

해외 주문이 공유 안전 코어(이중체결 방지·재시도 금지)를 거쳐 해외 와이어(TR/EXCD)로 나가는지,
지정가 필수, 해외 timeout 재조회 게이트, risk 세션 거부, 접수 응답(ODNO) 처리를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import ExecutionReport, KISClient, RiskLimits
from kis_trader.errors import KISError, KISUsageError, OrderTimeoutError
from kis_trader.transport import RawResponse, TransportTimeoutError

_ORDER = "/uapi/overseas-stock/v1/trading/order"
_ORDER_CHANGE = "/uapi/overseas-stock/v1/trading/order-rvsecncl"


class FakeTransport:
    def __init__(self, *, response=None, raises=None, on_post=None, on_get=None):
        self.response = response
        self.raises = raises
        self.on_post = on_post      # 주문(POST) 전용 응답/예외
        self.on_get = on_get        # 재조회(GET) 전용 응답
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body,
                               "params": params, "idempotent": idempotent})
        if method == "POST" and self.on_post is not None:
            # 리스트면 POST 별 순차 응답(place -> modify -> cancel 시퀀스).
            outcome = self.on_post.pop(0) if isinstance(self.on_post, list) else self.on_post
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if method == "GET" and self.on_get is not None:
            # 리스트면 페이지별 순차 응답(연속조회 테스트).
            return self.on_get.pop(0) if isinstance(self.on_get, list) else self.on_get
        if self.raises is not None:
            raise self.raises
        return self.response


def _posts(fake):
    return [c for c in fake.calls if c["method"] == "POST"]


def _ccnl(rows):
    return RawResponse(rt_cd="0", msg_cd="0", msg1="정상", body={"output": rows})


def _ccnl_row(*, pdno="AAPL", side="02", qty="1", ord_unpr="150.00", ccld_qty="1",
              ccld_unpr="150.10", odno="0000123456", rjct=""):
    return {"pdno": pdno, "sll_buy_dvsn_cd": side, "ft_ord_qty": qty, "ft_ord_unpr3": ord_unpr,
            "ft_ccld_qty": ccld_qty, "ft_ccld_unpr3": ccld_unpr, "odno": odno, "rjct_rson": rjct}


def _ack(odno="0000123456"):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": odno,
                                        "ORD_TMD": "093015"}})


def _client(transport, **kw):
    kw.setdefault("environment", "real")
    return KISClient(app_key="k", app_secret="s", account="12345678-01", transport=transport, **kw)


def test_overseas_buy_routes_to_overseas_wire():
    fake = FakeTransport(response=_ack())
    report = _client(fake).overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.25", client_order_id="oid-1"
    )
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000123456"             # ODNO 처리(도메스틱과 동일)
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER
    assert call["tr_id"] == "TTTT1002U"                # 미국 매수 실전
    assert call["body"]["OVRS_EXCG_CD"] == "NASD"
    assert call["body"]["ORD_QTY"] == "3"
    assert call["body"]["OVRS_ORD_UNPR"] == "150.25"
    assert "SLL_TYPE" not in call["body"]


def test_overseas_sell_sets_sll_type_and_tr():
    fake = FakeTransport(response=_ack())
    _client(fake).overseas.stock("AAPL", exchange="NAS").sell(
        quantity=1, limit_price="151.00", client_order_id="oid-2"
    )
    assert fake.calls[0]["tr_id"] == "TTTT1006U"       # 미국 매도 실전
    assert fake.calls[0]["body"]["SLL_TYPE"] == "00"


def test_overseas_cancel_maps_exchange_and_deduplicates():
    fake = FakeTransport(response=_ack())
    kis = _client(fake)
    kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.25", client_order_id="original-overseas-1"
    )
    # 해외는 전량 취소만 지원한다(부분 취소 미지원) -- quantity 를 생략해 잔량 전체를 취소한다.
    first = kis.orders.cancel(
        "original-overseas-1", request_id="cancel-overseas-1"
    )
    second = kis.orders.cancel(
        "original-overseas-1", request_id="cancel-overseas-1"
    )

    from kis_trader import OrderStatus
    assert first.status is OrderStatus.PENDING_CANCEL
    assert second == first
    assert len(_posts(fake)) == 2
    call = _posts(fake)[1]
    assert call["path"] == _ORDER_CHANGE
    assert call["tr_id"] == "TTTT1004U"
    assert call["idempotent"] is False
    assert call["body"]["OVRS_EXCG_CD"] == "NASD"
    assert call["body"]["PDNO"] == "AAPL"
    assert call["body"]["ORGN_ODNO"] == "0000123456"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert call["body"]["ORD_QTY"] == "3"          # 잔량 전체(3)
    assert call["body"]["OVRS_ORD_UNPR"] == "0"


def test_overseas_replace_uses_demo_tr_and_new_price():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, environment="paper")
    kis.overseas.stock("0700", exchange="HKS").sell(
        quantity=4, limit_price="410.00", client_order_id="original-overseas-2"
    )
    # 해외는 전량 정정만 지원한다(부분 수량 정정 미지원) -- quantity 를 생략해 잔량 전체를 정정한다.
    report = kis.orders.modify(
        "original-overseas-2", limit_price="412.50",
        request_id="modify-overseas-1",
    )

    from kis_trader import OrderStatus
    assert report.status is OrderStatus.PENDING_REPLACE
    call = _posts(fake)[1]
    # 홍콩 정정은 홍콩 전용 TR(모의 VTTS1003U) -- 미국 TR(VTTT1004U)이 아니다(KIS 공식: 거래소별 TR).
    assert call["tr_id"] == "VTTS1003U"
    assert call["body"]["OVRS_EXCG_CD"] == "SEHK"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert call["body"]["ORD_QTY"] == "4"          # 잔량 전체(4)
    assert call["body"]["OVRS_ORD_UNPR"] == "412.50"


# 거래소코드(EXCD) -> (취소 TR 실전, 모의). KIS 공식 '해외주식 정정취소주문'(v1_해외주식-003).
# 미국만 단일 TTTT1004U 가 전 미국 거래소를 포괄하고 아시아권은 거래소별로 다르다.
_CHANGE_TR_BY_EXCHANGE = {
    "NAS": ("TTTT1004U", "VTTT1004U"), "NYS": ("TTTT1004U", "VTTT1004U"),
    "HKS": ("TTTS1003U", "VTTS1003U"),
    "TSE": ("TTTS0309U", "VTTS0309U"),
    "SHS": ("TTTS0302U", "VTTS0302U"),
    "SZS": ("TTTS0306U", "VTTS0306U"),
    "HNX": ("TTTS0312U", "VTTS0312U"),
}


@pytest.mark.parametrize("environment,env_index", [("real", 0), ("paper", 1)])
@pytest.mark.parametrize("exchange", list(_CHANGE_TR_BY_EXCHANGE))
def test_overseas_cancel_uses_per_exchange_tr(exchange, environment, env_index):
    """취소는 거래소별 공식 TR 로 나가야 한다 -- 비미국이 미국 TR 로 새면 리젝/오라우팅된다(과거 버그)."""
    fake = FakeTransport(response=_ack())
    kis = _client(fake, environment=environment)
    kis.overseas.stock("SYM", exchange=exchange).buy(
        quantity=2, limit_price="100.00", client_order_id=f"orig-{exchange}-{environment}"
    )
    kis.orders.cancel(f"orig-{exchange}-{environment}", request_id=f"cancel-{exchange}-{environment}")
    call = _posts(fake)[1]
    assert call["path"] == _ORDER_CHANGE
    assert call["tr_id"] == _CHANGE_TR_BY_EXCHANGE[exchange][env_index]


@pytest.mark.parametrize("exchange", ["SHS", "SZS", "HNX"])
def test_overseas_modify_rejected_for_cancel_only_markets(exchange):
    """상해/심천/베트남은 KIS 공식상 취소만 지원 -- 정정은 fail-closed(와이어로 나가지 않는다)."""
    fake = FakeTransport(response=_ack())
    kis = _client(fake)
    kis.overseas.stock("SYM", exchange=exchange).buy(
        quantity=2, limit_price="100.00", client_order_id=f"orig-mod-{exchange}"
    )
    with pytest.raises(KISUsageError, match="취소만"):
        kis.orders.modify(f"orig-mod-{exchange}", limit_price="101.00", request_id=f"mod-{exchange}")
    assert len(_posts(fake)) == 1   # 정정 시도는 접수(place) 외 추가 POST 가 없어야 한다


def test_overseas_modify_rebinds_client_order_id_to_new_odno():
    """해외 정정도 국내와 같은 안전코어(submit_change)를 공유한다 -- 정정이 새 ODNO 를 부여하면
    원 client_order_id 를 그 주문으로 재바인딩해, 이어지는 취소가 원 id 로 정정된 주문(새 ODNO)을
    지목한다(낡은 ODNO 미사용).

    원장 검증('해외주식 정정취소주문' output): 응답 output = {KRX_FWDG_ORD_ORGNO, ODNO, ORD_TMD}
    로 국내와 동일 구조이며 ODNO 는 "채번된 주문번호"(정정 시 새 번호)다. 아래 _ack() 픽스처가
    그 실필드(KRX_FWDG_ORD_ORGNO/ODNO/ORD_TMD)를 그대로 쓴다."""
    from kis_trader import OrderStatus
    fake = FakeTransport(on_post=[_ack("0000123456"), _ack("0000123499"), _ack("0000123499")])
    kis = _client(fake)
    kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.25", client_order_id="ov-1"
    )
    report = kis.orders.modify("ov-1", limit_price="151.00", request_id="ov-modify-1")
    assert report.status is OrderStatus.PENDING_REPLACE
    assert report.order_id == "0000123499"                     # 정정 응답의 새 ODNO

    kis.orders.cancel("ov-1", request_id="ov-cancel-1")
    cancel_call = _posts(fake)[2]
    assert cancel_call["path"] == _ORDER_CHANGE
    assert cancel_call["body"]["ORGN_ODNO"] == "0000123499"    # 낡은 ...456 아닌 새 ...499


def test_overseas_change_timeout_is_not_resent():
    fake = FakeTransport(response=_ack())
    kis = _client(fake)
    kis.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=3, limit_price="150.25", client_order_id="original-overseas-3"
    )
    fake.raises = TransportTimeoutError()
    with pytest.raises(OrderTimeoutError):
        kis.orders.cancel("original-overseas-3", request_id="cancel-overseas-timeout")
    fake.raises = None
    with pytest.raises(KISUsageError, match="재전송하지"):
        kis.orders.cancel("original-overseas-3", request_id="cancel-overseas-timeout")
    assert len(_posts(fake)) == 2


def test_overseas_market_order_rejected():
    fake = FakeTransport(response=_ack())
    with pytest.raises(KISUsageError, match="지정가"):
        _client(fake).overseas.stock("AAPL", exchange="NAS").buy(quantity=1)  # price 없음


def test_overseas_order_dedup_replays_report():
    # 같은 client_order_id 재전송은 와이어에 다시 안 나가고 이전 리포트를 돌려준다(이중체결 방지).
    fake = FakeTransport(response=_ack())
    handle = _client(fake).overseas.stock("AAPL", exchange="NAS")
    first = handle.buy(quantity=1, limit_price="150.00", client_order_id="dup")
    second = handle.buy(quantity=1, limit_price="150.00", client_order_id="dup")
    assert first.order_id == second.order_id
    assert len(fake.calls) == 1                         # 두 번째는 와이어 미접촉


def test_overseas_order_timeout_then_reconcile_confirms():
    # 주문 POST 는 timeout, 재조회 GET 은 지문과 맞는 체결 1건 -> 확정.
    fake = FakeTransport(on_post=TransportTimeoutError("timeout"), on_get=_ccnl([_ccnl_row()]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="to")
    report = client.orders.reconcile("to")                    # 해외 체결내역으로 확정
    assert report is not None
    assert report.order_id == "0000123456"
    assert report.filled_quantity == Decimal(1)
    assert fake.calls[-1]["path"] == "/uapi/overseas-stock/v1/trading/inquire-ccnl"
    assert fake.calls[-1]["tr_id"] == "TTTS3035R"


def test_overseas_reconcile_zero_matches_stays_none():
    # 지문과 맞는 체결이 없으면 미접수로 단정하지 않고 None(재전송 금지 유지).
    fake = FakeTransport(on_post=TransportTimeoutError("timeout"),
                         on_get=_ccnl([_ccnl_row(pdno="MSFT")]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="z")
    assert client.orders.reconcile("z") is None


def test_overseas_refresh_matches_stored_odno_not_sibling():
    # 접수된 주문(ODNO 123456)을 refresh 재조회할 때 체결내역에 동일 지문의 형제 ODNO(9999999) 행만
    # 있으면 그 형제로 재바인딩하지 않는다 -- prior ODNO 로 자기 행을 집으므로 0건 -> None(비종료 유지,
    # phantom 확정 방지). 지문 매칭이면 형제를 이 주문으로 오귀속했을 것이다.
    fake = FakeTransport(on_post=_ack("0000123456"),
                         on_get=_ccnl([_ccnl_row(odno="0009999999")]))
    client = _client(fake)
    client.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="150.00", client_order_id="sib")
    assert client.orders.reconcile("sib", refresh=True) is None


def test_overseas_refresh_excludes_cancel_row_with_same_odno():
    # ODNO 로 집더라도 취소/정정 행(rvse_cncl_dvsn 01/02)이 같은 odno 로 오면 원주문 확정으로 오인하지
    # 않는다 -- 그 행 제외 -> 0건 -> None(비종료 유지, phantom 확정 방지).
    cancel_row = {**_ccnl_row(odno="0000123456"), "rvse_cncl_dvsn": "02"}
    fake = FakeTransport(on_post=_ack("0000123456"), on_get=_ccnl([cancel_row]))
    client = _client(fake)
    client.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="150.00", client_order_id="cxl")
    assert client.orders.reconcile("cxl", refresh=True) is None


def test_overseas_refresh_confirms_on_stored_odno():
    # prior ODNO 와 같은 ODNO 행이 있으면 그 행으로 현재 상태를 갱신한다(정상 refresh).
    fake = FakeTransport(on_post=_ack("0000123456"),
                         on_get=_ccnl([_ccnl_row(odno="0000123456", ccld_qty="1")]))
    client = _client(fake)
    client.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="150.00", client_order_id="hit")
    report = client.orders.reconcile("hit", refresh=True)
    assert report is not None and report.order_id == "0000123456"


def test_overseas_reconcile_two_matches_raises():
    # 지문과 맞는 체결이 2건이면 자동 확정하지 않고 에러(보수적 -- 오확정 방지).
    fake = FakeTransport(on_post=TransportTimeoutError("timeout"),
                         on_get=_ccnl([_ccnl_row(odno="1"), _ccnl_row(odno="2")]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="m2")
    with pytest.raises(KISError, match="2건"):
        client.orders.reconcile("m2")


@pytest.mark.parametrize(("ccld_qty", "rjct", "expected_status", "expected_filled"), [
    ("0", "주문거부", "REJECTED", 0),          # 거부 + 미체결
    ("3", "", "PARTIALLY_FILLED", 3),          # 일부 체결
    ("0", "", "NEW", 0),                        # 접수만(working)
])
def test_overseas_reconcile_classifies_ccnl_status(ccld_qty, rjct, expected_status, expected_filled):
    # 재조회가 체결내역 행을 REJECTED/PARTIALLY_FILLED/NEW 로 정확히 분류하는지(FILLED 외 분기).
    from kis_trader import OrderStatus
    row = _ccnl_row(qty="10", ord_unpr="150.00", ccld_qty=ccld_qty, rjct=rjct)
    fake = FakeTransport(on_post=TransportTimeoutError("timeout"), on_get=_ccnl([row]))
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(
            quantity=10, limit_price="150.00", client_order_id="cls",
        )
    report = client.orders.reconcile("cls")
    assert report is not None
    assert report.status is getattr(OrderStatus, expected_status)
    assert report.filled_quantity == Decimal(expected_filled)


def test_overseas_write_timeout_does_not_retry():
    # POST timeout 이면 정확히 1회만 전송(재전송 금지)하고 idempotent=False 로 나간다.
    fake = FakeTransport(on_post=TransportTimeoutError("timeout"))
    with pytest.raises(OrderTimeoutError):
        _client(fake).overseas.stock("AAPL", exchange="NAS").buy(
            quantity=1, limit_price="150.00", client_order_id="nr")
    posts = _posts(fake)
    assert len(posts) == 1                              # 재전송 없음
    assert posts[0]["idempotent"] is False              # 쓰기라 재시도 불가 표시


def test_overseas_reconcile_date_window_is_deterministic():
    # reconcile 날짜창은 주입 시각(now) 기준으로 결정적 -- 벽시계에 의존하지 않는다.
    from datetime import datetime, timezone

    from kis_trader.overseas._engine import orders as engine
    from kis_trader.store import OrderStore
    kst = timezone(__import__("datetime").timedelta(hours=9))
    store = OrderStore()
    order = _client(FakeTransport(response=_ack())).overseas.stock(
        "AAPL", exchange="NAS")._make_order(
            "buy", quantity=1, limit_price="150.00", time_in_force="day", client_order_id="d1")
    store.try_claim("d1", order.fingerprint)            # in-flight 로 만든다
    fake = FakeTransport(on_get=_ccnl([]))
    engine.reconcile(fake, store, "d1", cano="1", product_code="01", environment="real",
                     now=datetime(2024, 3, 15, 10, 0, tzinfo=kst))
    params = fake.calls[0]["params"]
    assert params["ORD_END_DT"] == "20240315"
    assert params["ORD_STRT_DT"] == "20240314"          # today-1 (해외 현지일 ±1 여유)


def test_overseas_reconcile_paginates_ccnl():
    # 재조회가 연속조회(ctx_area_nk200)를 소진하며 모든 페이지를 스캔한다.
    page1 = RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                        body={"output": [_ccnl_row(pdno="MSFT")], "ctx_area_nk200": "NEXT"})
    page2 = _ccnl([_ccnl_row()])                        # 원하는 체결은 2페이지에
    fake = FakeTransport(on_post=TransportTimeoutError("t"), on_get=[page1, page2])
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="pg")
    report = client.orders.reconcile("pg")
    assert report is not None and report.order_id == "0000123456"
    gets = [c for c in fake.calls if c["method"] == "GET"]
    assert len(gets) == 2
    assert gets[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_overseas_reconcile_keeps_scanning_when_cursor_advances():
    # 안전: tr_cont 가 연속(F/M)이고 커서가 진전하면(비어 있지도 반복되지도 않음) 계속 스캔한다 --
    # 조기 종료로 체결을 놓쳐 '미접수'로 오판하면 이중체결 위험. 원하는 체결은 2페이지에 있다.
    page1 = RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                        body={"output": [_ccnl_row(pdno="MSFT")],
                              "ctx_area_nk200": "NEXT1", "ctx_area_fk200": "FK1"},
                        tr_cont="M")                    # 진전하는 커서 + 더 있음
    page2 = _ccnl([_ccnl_row()])                        # 원하는 체결은 2페이지에(tr_cont 기본 "", 커서 없음)
    fake = FakeTransport(on_post=TransportTimeoutError("t"), on_get=[page1, page2])
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="tc")
    report = client.orders.reconcile("tc")
    assert report is not None and report.order_id == "0000123456"
    assert len([c for c in fake.calls if c["method"] == "GET"]) == 2


@pytest.mark.parametrize("tr_cont", ["F", "M"])
@pytest.mark.parametrize("cursor", ["empty", "repeated"])
def test_overseas_reconcile_raises_when_cursor_cannot_advance_but_tr_cont_says_more(cursor, tr_cont):
    # fail-closed: tr_cont 가 '더 있음'(F/M)인데 커서가 진전 불가면 모순이다 -- 빈 커서(다음 요청이
    # CTX 리셋->1페이지 재적재) 또는 반복 커서(서버가 커서를 안 진전; 무한 재요청/이중집계). 부분/모순으로
    # 확정하지 않고 KISError 로 올린다. 두 arm(빈/반복) x tr_cont(F/M) 모두 검증.
    row = _ccnl_row(pdno="MSFT")
    if cursor == "empty":
        pages = [RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                             body={"output": [row], "ctx_area_nk200": ""}, tr_cont=tr_cont)]
    else:  # repeated: 1페이지가 커서를 세우고 2페이지가 같은 커서를 반복 -> 진전 불가
        pages = [RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                             body={"output": [row], "ctx_area_nk200": "SAME"}, tr_cont=tr_cont),
                 RawResponse(rt_cd="0", msg_cd="0", msg1="정상",
                             body={"output": [row], "ctx_area_nk200": "SAME"}, tr_cont=tr_cont)]
    fake = FakeTransport(on_post=TransportTimeoutError("t"), on_get=pages)
    client = _client(fake)
    with pytest.raises(OrderTimeoutError):
        client.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price="150.00", client_order_id="tc")
    with pytest.raises(KISError, match="진전하지 않는"):
        client.orders.reconcile("tc")


def test_overseas_non_day_tif_rejected_before_wire():
    # 해외는 day 만 -- IOC 를 조용히 day 로 바꾸지 않고 와이어 전에 거부한다(fail-closed).
    fake = FakeTransport(response=_ack())
    with pytest.raises(KISUsageError, match="day"):
        _client(fake).overseas.stock("AAPL", exchange="NAS").buy(
            quantity=1, limit_price="150.00", time_in_force="ioc", client_order_id="ioc")
    assert _posts(fake) == []


def test_overseas_unknown_exchange_rejected_before_wire():
    # 알 수 없는 거래소코드는 도메스틱 빌더로 흘러 와이어 전에 거부된다(오라우팅 방지).
    fake = FakeTransport(response=_ack())
    with pytest.raises(Exception):  # noqa: B017 -- NotImplementedError/KISUsageError, 어느 쪽이든 와이어 전
        _client(fake).overseas.stock("BOGUS", exchange="XXX").buy(
            quantity=1, limit_price="150.00", client_order_id="x")
    assert _posts(fake) == []


def test_overseas_full_demo_tr_matrix():
    # 실전에 이어 모의 TR 도 시장 x 매수/매도 전수 검증(원장 [모의투자]).
    from kis_trader.overseas._engine.orders import make_order_request_from_fields
    demo = {
        ("NAS", "buy"): "VTTT1002U", ("NAS", "sell"): "VTTT1001U",
        ("TSE", "buy"): "VTTS0308U", ("TSE", "sell"): "VTTS0307U",
        ("SHS", "buy"): "VTTS0202U", ("SHS", "sell"): "VTTS1005U",
        ("HKS", "buy"): "VTTS1002U", ("HKS", "sell"): "VTTS1001U",
        ("SZS", "buy"): "VTTS0305U", ("SZS", "sell"): "VTTS0304U",
        ("HSX", "buy"): "VTTS0311U", ("HSX", "sell"): "VTTS0310U",
    }
    for (exchange, side), tr in demo.items():
        wire = make_order_request_from_fields(side=side, symbol="X", quantity=Decimal(1),
                                              limit_price=Decimal(1), exchange=exchange, cano="1",
                                              product_code="01", environment="paper")
        assert wire.tr_id == tr


def test_overseas_order_with_risk_session_now_gated():
    # 해외주식도 이제 사전 리스크 게이트를 탄다(수량·금액·collar). 결제통화별 금액 한도를 주면
    # (NAS->USD) 통과해 와이어로 나간다 -- 예전의 "해외 risk 미지원 거부" 는 사라졌다.
    fake = FakeTransport(response=_ack())
    client = _client(fake, risk=RiskLimits(max_order_quantity=10,
                                           max_order_notional_by_currency={"USD": 10_000}))
    report = client.overseas.stock("AAPL", exchange="NAS").buy(
        quantity=1, limit_price="150.00", client_order_id="r1")   # 150 <= 10000, 1 <= 10
    assert isinstance(report, ExecutionReport)
    assert [c["path"] for c in fake.calls] == [_ORDER]             # collar 없음 -> 참조 조회 없이 전송

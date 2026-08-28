"""주문 실행 안전 재검증 (새 API) -- kis.domestic.stock(...).buy/sell + kis.reconcile.

멱등 dedup(replay/충돌/in-flight), 쓰기 타임아웃 재시도 금지, 접수 거부, ODNO 부재, 퇴직연금
차단, 시장가/지정가 와이어, 보수적 재조회를 네트워크 없이 가짜 전송으로 검증한다. 안전 엔진은
검증된 코어를 새 구조로 이관한 것이라, 여기서 새 진입점(ticker.buy/sell, kis.orders.reconcile)이 그
불변식을 그대로 구동하는지 확인한다.
"""

from __future__ import annotations

import errno
import threading
from decimal import Decimal

import pytest

from kis_trader import ExecutionReport, KISClient, OrderStatus, OrderStore
from kis_trader.errors import (
    AccountNotOrderableError,
    KISError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
    UnsupportedSchemaVersionError,
)
from kis_trader.transport import RawResponse, TransportTimeout

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_ORDER_CHANGE = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_DAILY_CCLD = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


class FakeTransport:
    """가짜 전송. 경로별 순차 응답/예외(by_path 리스트), 기본 응답, 예외 지원. 호출 기록."""

    def __init__(self, *, response=None, raises=None, by_path=None):
        self.response = response
        self.raises = raises
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id,
                               "params": params, "body": body, "idempotent": idempotent})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
        elif self.raises is not None:
            outcome = self.raises
        else:
            outcome = self.response
        if isinstance(outcome, BaseException) or (isinstance(outcome, type) and issubclass(outcome, BaseException)):
            raise outcome
        assert outcome is not None, "FakeTransport 에 응답을 줘야 한다"
        return outcome


_ACCEPTED_ORDER_RESPONSE = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057",
                     "ORD_TMD": "121052"}},
)
_REJECTED_ORDER_RESPONSE = RawResponse(rt_cd="1", msg_cd="APBK1234", msg1="주문가능금액 부족", body={})


def _daily_orders_response(rows):
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="조회 완료", body={"output1": rows})


def _daily_order_row(*, odno="0000117057", symbol="005930", side_code="02", order_division="00",
                     order_quantity="10", order_unit_price="70000", filled_quantity="10",
                     average_price="70000", rejected_quantity="0", canceled="N", excg="KRX", org=""):
    return {"odno": odno, "pdno": symbol, "sll_buy_dvsn_cd": side_code,
            "ord_dvsn_cd": order_division, "ord_qty": order_quantity, "ord_unpr": order_unit_price,
            "tot_ccld_qty": filled_quantity, "avg_prvs": average_price, "ord_gno_brno": org,
            "rjct_qty": rejected_quantity, "cncl_yn": canceled, "excg_id_dvsn_cd": excg}


def _client(transport, *, environment="real", account="12345678-01", store=None, orderable=True,
            allow_credit=False):
    return KISClient(app_key="k", app_secret="s", account=account, environment=environment,
                     transport=transport, store=store, orderable=orderable, allow_credit=allow_credit)


# --- 정상 전송 -------------------------------------------------------------
def test_buy_limit_places_order():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake).domestic.stock("005930").buy(quantity=10, limit_price=70000)
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000117057"
    assert report.symbol == "005930"
    assert report.side == "buy"
    assert report.status is OrderStatus.NEW
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _ORDER_CASH
    assert call["idempotent"] is False              # 주문은 타임아웃 재시도 금지
    assert call["tr_id"] == "TTTC0012U"             # 실전 매수
    assert call["body"]["ORD_DVSN"] == "00"         # 지정가
    assert call["body"]["ORD_UNPR"] == "70000"


@pytest.mark.parametrize("quantity", ["0", "-1"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_immediate_order_rejects_nonpositive_quantity(side, quantity):
    # 수량<=0 가드는 즉시 매수/매도 경로에서도 걸려야 하고, 거부는 와이어 전(전송 미호출)에 일어난다.
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    ticker = _client(fake).domestic.stock("005930")
    with pytest.raises(KISUsageError):
        getattr(ticker, side)(quantity=quantity, limit_price=70000)
    assert fake.calls == []


def test_buy_market_uses_market_division():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(fake).domestic.stock("005930").buy(quantity=10)
    assert fake.calls[0]["body"]["ORD_DVSN"] == "01"    # 시장가
    assert fake.calls[0]["body"]["ORD_UNPR"] == "0"


def test_sell_uses_sell_tr():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake).domestic.stock("005930").sell(quantity=10, limit_price=70000)
    assert report.side == "sell"
    assert fake.calls[0]["tr_id"] == "TTTC0011U"        # 실전 매도


def test_demo_uses_demo_tr():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(fake, environment="paper").domestic.stock("005930").buy(quantity=10, limit_price=70000)
    assert fake.calls[0]["tr_id"] == "VTTC0012U"


def test_cancel_domestic_order_uses_original_identifiers_and_deduplicates():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.domestic.stock("005930").buy(
        quantity=10, limit_price=70000, client_order_id="original-1"
    )
    first = kis.orders.cancel("original-1", request_id="cancel-1")
    second = kis.orders.cancel("original-1", request_id="cancel-1")

    assert first.status is OrderStatus.PENDING_CANCEL
    assert second == first
    assert len(fake.calls) == 2
    call = fake.calls[1]
    assert call["path"] == _ORDER_CHANGE
    assert call["tr_id"] == "TTTC0013U"
    assert call["idempotent"] is False
    assert call["body"]["KRX_FWDG_ORD_ORGNO"] == "01790"
    assert call["body"]["ORGN_ODNO"] == "0000117057"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "02"
    assert call["body"]["ORD_QTY"] == "10"
    assert call["body"]["ORD_UNPR"] == "70000"
    assert call["body"]["QTY_ALL_ORD_YN"] == "Y"


def test_replace_domestic_order_maps_new_quantity_and_price():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake, environment="paper")
    kis.domestic.stock("005930").sell(
        quantity=10, limit_price=70000, client_order_id="original-2"
    )
    report = kis.orders.modify(
        "original-2", quantity=4, limit_price=71000, request_id="modify-1"
    )

    assert report.status is OrderStatus.PENDING_REPLACE
    call = fake.calls[1]
    assert call["tr_id"] == "VTTC0013U"
    assert call["body"]["RVSE_CNCL_DVSN_CD"] == "01"
    assert call["body"]["ORD_QTY"] == "4"
    assert call["body"]["ORD_UNPR"] == "71000"
    assert call["body"]["QTY_ALL_ORD_YN"] == "N"


def test_modify_rebinds_client_order_id_to_new_odno_so_cancel_targets_it():
    """정정하면 KIS 가 원주문에 **새 ODNO** 를 부여한다. 원 client_order_id 는 이후에도
    그 살아있는 주문을 가리켜야 한다 -- 낡은 ODNO 로 취소하면 '정정취소 가능수량 없음'
    으로 실패한다(실서버에서 실증). 정정 응답의 새 ODNO 와 조직번호를 원 id 의 표준
    리포트에 반영해, 이어지는 cancel/modify 가 정정된 주문을 지목하게 한다."""
    place_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "090000"}})
    modify_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "02880", "ODNO": "0000117061", "ORD_TMD": "090100"}})
    cancel_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "02880", "ODNO": "0000117061", "ORD_TMD": "090200"}})
    fake = FakeTransport(by_path={_ORDER_CASH: place_response,
                                  _ORDER_CHANGE: [modify_response, cancel_response]})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    report = kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")

    # 정정 응답의 새 ODNO 가 원 id 의 표준 상태로 반영된다.
    assert report.order_id == "0000117061"
    assert store.report_for("orig-1").order_id == "0000117061"

    # 이어지는 취소가 원 id 로 새 ODNO(와 그 조직번호)를 지목한다.
    kis.orders.cancel("orig-1", request_id="cancel-1")
    cancel_call = fake.calls[2]
    assert cancel_call["body"]["ORGN_ODNO"] == "0000117061"
    assert cancel_call["body"]["KRX_FWDG_ORD_ORGNO"] == "02880"


def test_modify_of_partially_filled_order_resets_filled_and_avg_price():
    """부분체결된 주문을 정정하면 새 ODNO 는 정정 수량만큼의 신규 대기주문이라 rebound 리포트의
    체결량은 0 이어야 하고, 그에 맞춰 평균가도 None 이어야 한다(코드 규약: filled==0 => avg None).
    지문 수량은 정정 잔량으로 재바인딩된다."""
    import dataclasses
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    # 부분체결 시뮬레이션: 저장된 리포트를 3/10 체결·평균가로 갱신(지문은 유지).
    placed = store.report_for("orig-1")
    store.record(
        dataclasses.replace(placed, status=OrderStatus.PARTIALLY_FILLED,
                            filled_quantity=Decimal(3), average_price=Decimal(70000)),
        store.fingerprint_for("orig-1"))
    kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    rebound = store.report_for("orig-1")
    assert rebound.filled_quantity == Decimal(0)              # 신규 대기주문 = 0 체결
    assert rebound.average_price is None                      # filled==0 => 평균가 없음
    assert store.fingerprint_for("orig-1").quantity == "7"    # 잔량 7 로 재바인딩


def test_modify_with_missing_odno_fails_closed_and_does_not_rebind():
    """정정이 접수(rt_cd=0)됐는데 새 ODNO 가 없으면 재조회 불가 -- place 와 같이 fail-closed
    (OrderError, 변경요청 in-flight 유지)하고, 원 id 를 낡은 ODNO 에 재바인딩하지 않는다
    (낡은 ODNO 로의 조용한 재무장·은폐 금지)."""
    place = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "090000"}})
    modify_no_odno = RawResponse(rt_cd="0", msg_cd="A", msg1="주문 전송 완료", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ORD_TMD": "090100"}})  # ODNO 누락
    fake = FakeTransport(by_path={_ORDER_CASH: place, _ORDER_CHANGE: modify_no_odno})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    with pytest.raises(OrderError):
        kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    # 원 id 는 여전히 원 ODNO -- 낡은 값에 재바인딩하지 않는다.
    assert store.report_for("orig-1").order_id == "0000117057"
    assert store.report_for("orig-1").status is OrderStatus.NEW
    # 변경 요청은 in-flight 유지(재조회 요구), 완료로 기록되지 않는다.
    assert store.is_in_flight("modify-1")
    assert store.report_for("modify-1") is None


def test_modify_replay_same_request_id_returns_prior_not_conflict():
    """정정 성공 뒤 원 id 가 새 ODNO 로 재바인딩돼도, 같은 request_id 로 정정을 재요청하면
    (멱등 재시도) CONFLICT 가 아니라 그 결과를 replay 해야 한다. action 지문이 재바인딩된
    report.order_id 에 의존하면 재계산 값이 달라져 조용히 CONFLICT 로 깨진다."""
    place = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "090000"}})
    modify_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117061", "ORD_TMD": "090100"}})
    fake = FakeTransport(by_path={_ORDER_CASH: place, _ORDER_CHANGE: modify_response})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    first = kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    second = kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    assert second == first
    assert len([c for c in fake.calls if c["path"] == _ORDER_CHANGE]) == 1   # 한 번만 전송


def test_modify_quantity_rebinds_remaining_for_next_change():
    """수량을 바꾸는 정정 뒤에는 원 id 의 지문 수량도 새 수량으로 재바인딩돼야, 이어지는
    cancel/modify(수량 생략)가 올바른 잔량을 계산한다 -- 원 수량으로 계산하면 살아있는
    주문에 틀린 ORD_QTY 를 보낸다."""
    place = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "090000"}})
    modify_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "02880", "ODNO": "0000117061", "ORD_TMD": "090100"}})
    change_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "03970", "ODNO": "0000117062", "ORD_TMD": "090200"}})
    fake = FakeTransport(by_path={_ORDER_CASH: place,
                                  _ORDER_CHANGE: [modify_response, change_response]})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    kis.orders.modify("orig-1", quantity=4, limit_price=71000, request_id="modify-1")
    kis.orders.modify("orig-1", limit_price=72000, request_id="modify-2")
    change_calls = [c for c in fake.calls if c["path"] == _ORDER_CHANGE]
    # 2차 정정은 1차 정정이 부여한 새 ODNO·조직번호를 지목한다(원 주문번호가 아니라).
    assert change_calls[1]["body"]["ORGN_ODNO"] == "0000117061"
    assert change_calls[1]["body"]["KRX_FWDG_ORD_ORGNO"] == "02880"
    assert change_calls[1]["body"]["ORD_QTY"] == "4"          # 원 10 아닌 정정 후 4
    # 2차 정정 뒤에는 원 id 가 2차의 ODNO 를 가리킨다(재바인딩 연쇄).
    assert store.report_for("orig-1").order_id == "0000117062"


def test_modify_rebind_persists_in_a_single_store_write(monkeypatch):
    """정정의 두 상태전이(변경요청 기록 + 원 id 재바인딩)는 한 번의 영속 쓰기로 원자적이어야
    한다 -- 두 번 나눠 쓰면 그 사이 크래시 시 request_id 는 완료로 남고 원 id 는 낡은 ODNO 에
    고착돼(재시도는 COMPLETED 조기반환) 복구 불가해진다."""
    place = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117057", "ORD_TMD": "090000"}})
    modify_response = RawResponse(rt_cd="0", msg_cd="A", msg1="", body={"output": {
        "KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0000117061", "ORD_TMD": "090100"}})
    fake = FakeTransport(by_path={_ORDER_CASH: place, _ORDER_CHANGE: modify_response})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    saves = {"n": 0}
    original_save = store._save_locked
    monkeypatch.setattr(store, "_save_locked",
                        lambda: (saves.__setitem__("n", saves["n"] + 1), original_save())[1])
    kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    assert saves["n"] == 2          # claim 1회 + 결과기록(변경요청+재바인딩) 단일 save 1회
    # 그 단일 save 가 두 전이를 모두 담았는지(횟수만이 아니라 내용) 확인.
    assert store.report_for("orig-1").order_id == "0000117061"           # 재바인딩됨
    assert store.report_for("modify-1").status is OrderStatus.PENDING_REPLACE  # 변경요청 기록됨


def test_cancel_survives_store_restart_via_persisted_org_number(tmp_path):
    """path-backed store 재기동(재로드) 후에도 정정·취소가 원주문을 지목한다 -- 조직번호
    (KRX_FWDG_ORD_ORGNO)는 미영속 ``_raw`` 가 아니라 리포트 필드로 영속되므로 재시작에도
    살아남는다. (재기동 후엔 ``_raw`` 가 비어, 조직번호를 거기서만 읽으면 취소가 거부된다.)"""
    path = str(tmp_path / "orders.json")
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with OrderStore(path=path) as store:
        _client(fake, store=store).domestic.stock("005930").buy(
            quantity=10, limit_price=70000, client_order_id="orig-1")
    with OrderStore(path=path) as store2:                 # 재기동 시뮬레이션(새 인스턴스)
        assert not store2.report_for("orig-1")._raw       # _raw 는 재로드에서 비어있음(설계)
        _client(fake, store=store2).orders.cancel("orig-1", request_id="cancel-1")
    call = fake.calls[-1]
    assert call["path"] == _ORDER_CHANGE
    assert call["body"]["KRX_FWDG_ORD_ORGNO"] == "01790"  # 조직번호 영속 -> 취소가 지목
    assert call["body"]["ORGN_ODNO"] == "0000117057"


def test_reconcile_of_change_request_is_explicitly_rejected():
    """변경요청(정정/취소)이 in-flight 로 남아도(타임아웃 등) 그 request_id 는 자동 reconcile
    대상이 아니다 -- action 지문의 종목이 실제 종목코드가 아니라 원 주문 식별이라 일별체결 조회로
    맞출 수 없다. 조용히 무한 in-flight 로 두지 않고 명확히 거부해 수동 확인을 안내한다."""
    fake = FakeTransport(by_path={_ORDER_CASH: _ACCEPTED_ORDER_RESPONSE,
                                  _ORDER_CHANGE: TransportTimeout()})
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-1")
    with pytest.raises(OrderTimeoutError):
        kis.orders.modify("orig-1", limit_price=71000, request_id="modify-1")
    assert store.is_in_flight("modify-1")
    with pytest.raises(KISUsageError, match="자동 reconcile"):
        kis.orders.reconcile("modify-1")


def test_domestic_change_timeout_stays_in_flight_and_is_not_resent():
    fake = FakeTransport(
        by_path={
            _ORDER_CASH: _ACCEPTED_ORDER_RESPONSE,
            _ORDER_CHANGE: TransportTimeout(),
        }
    )
    kis = _client(fake)
    kis.domestic.stock("005930").buy(
        quantity=10, limit_price=70000, client_order_id="original-3"
    )
    with pytest.raises(OrderTimeoutError):
        kis.orders.cancel("original-3", request_id="cancel-timeout")
    with pytest.raises(KISUsageError, match="재전송하지"):
        kis.orders.cancel("original-3", request_id="cancel-timeout")
    assert len([call for call in fake.calls if call["path"] == _ORDER_CHANGE]) == 1


# --- 멱등 dedup ------------------------------------------------------------
def test_same_client_order_id_replays_without_resend():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    first = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    second = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert second.order_id == first.order_id
    assert len(fake.calls) == 1                          # 재전송 안 함(replay)


def test_same_id_different_order_conflicts():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISUsageError):                   # 같은 id 다른 주문 -> 충돌 거부
        kis.domestic.stock("005930").buy(quantity=99, limit_price=70000, client_order_id="ID-1")


# --- 타임아웃 = 재시도 금지, in-flight 유지 --------------------------------
def test_write_timeout_raises_and_does_not_retry():
    fake = FakeTransport(raises=TransportTimeout("timeout"))
    with pytest.raises(OrderTimeoutError) as excinfo:
        _client(fake).domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert excinfo.value.client_order_id == "ID-1"
    assert len(fake.calls) == 1                          # 재시도 없음


def test_in_flight_after_timeout_refuses_resend():
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t"), _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISUsageError):                   # in-flight -> 재전송 거부(재조회 요구)
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")


# --- 접수 거부 / ODNO 부재 -------------------------------------------------
def test_rejected_raises_and_clears_in_flight():
    fake = FakeTransport(by_path={_ORDER_CASH: [_REJECTED_ORDER_RESPONSE, _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderRejectedError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    # 거부는 in-flight 를 해제하므로 같은 id 재전송이 허용된다(이번엔 접수).
    report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert report.order_id == "0000117057"


def test_accepted_without_odno_raises():
    resp = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok", body={"output": {}})
    with pytest.raises(KISError):
        _client(FakeTransport(response=resp)).domestic.stock("005930").buy(quantity=10, limit_price=70000)


# --- 계좌 가드 -------------------------------------------------------------
def test_retirement_account_blocked():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(AccountNotOrderableError):
        _client(fake, orderable=False).domestic.stock("005930").buy(quantity=10, limit_price=70000)
    assert fake.calls == []                              # 와이어에 닿기 전 차단


def test_order_requires_account():
    kis = KISClient(app_key="k", app_secret="s", transport=FakeTransport(response=_ACCEPTED_ORDER_RESPONSE))
    with pytest.raises(KISUsageError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)


# --- 재조회(reconcile) -----------------------------------------------------
def test_reconcile_replays_completed():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    placed = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    again = kis.orders.reconcile("ID-1")
    assert again is not None
    assert again.order_id == placed.order_id
    assert len(fake.calls) == 1                          # 완료 replay -- 추가 호출 없음


def test_reconcile_unknown_id_raises():
    with pytest.raises(KISUsageError):
        _client(FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)).orders.reconcile("never-sent")


def test_timeout_then_reconcile_confirms_fill():
    # 매수가 타임아웃 -> in-flight -> 재조회가 일별체결조회에서 지문 일치 1건 발견 -> 체결 확정.
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row()])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.orders.reconcile("ID-1")
    assert report is not None
    assert report.status is OrderStatus.FILLED
    assert report.filled_quantity == Decimal(10)


def test_cancel_after_reconcile_uses_org_from_daily_row():
    """재조회(일별체결)로 복원한 리포트도 조직번호(ord_gno_brno)를 담아, 이후 취소가 그 값으로
    원주문을 지목한다 -- 접수 응답 없이 재조회로만 확정된 주문도 정정취소 가능해야 한다."""
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(filled_quantity="0", org="01790")])],
        _ORDER_CHANGE: _ACCEPTED_ORDER_RESPONSE,
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.orders.reconcile("ID-1")
    assert report is not None and report.status is OrderStatus.NEW    # 미체결 -> 취소 가능
    kis.orders.cancel("ID-1", request_id="cancel-1")
    call = [c for c in fake.calls if c["path"] == _ORDER_CHANGE][-1]
    assert call["body"]["KRX_FWDG_ORD_ORGNO"] == "01790"             # 일별체결행의 조직번호로 지목
    assert call["body"]["ORGN_ODNO"] == "0000117057"


def test_reconcile_empty_scan_stays_in_flight():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert kis.orders.reconcile("ID-1") is None                 # 0건 -> 미접수로 단정 안 함(재전송 금지 유지)


def test_reconcile_non_list_output1_fails_closed():
    # 성공 응답인데 output1 이 리스트가 아니면(오/부분응답) 빈 결과로 오인하지 않고 fail-closed.
    malformed = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="조회 완료",
                            body={"output1": {"unexpected": "object"}})
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [malformed],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                        # output1 비리스트 -> 미접수로 단정 금지
        kis.orders.reconcile("ID-1")


def test_reconcile_ambiguous_multiple_matches_raises():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(odno="A"), _daily_order_row(odno="B")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                         # 지문 일치 2건 -> 자동 확정 불가
        kis.orders.reconcile("ID-1")


# --- 세션 store 공유 -------------------------------------------------------
def test_dedup_shared_across_tickers_via_client_store():
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")   # 같은 세션 store
    assert len(fake.calls) == 1


def test_injected_store_used():
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake, store=store)
    report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert store.report_for("ID-1") is not None
    assert store.report_for("ID-1").order_id == report.order_id


# --- 미매핑/부적합 주문구분 fail-closed -------------------------------------
@pytest.mark.parametrize("kwargs", [
    {"quantity": 10, "limit_price": 70000, "division": "conditional_limit", "time_in_force": "ioc"},
    {"quantity": 10, "division": "priority_limit", "time_in_force": "fok"},
    {"quantity": 10, "limit_price": 70000, "time_in_force": "gtc"},          # gtc 미지원
])
def test_unmapped_division_tif_rejected_before_wire(kwargs):
    """조건부/최우선엔 IOC/FOK 가 없고 gtc 는 미지원 -- 조용히 day 로 안 바꾸고 fail-closed."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError, match="지원하지 않는 주문구분"):
        _client(fake).domestic.stock("005930").buy(**kwargs)
    assert fake.calls == []


@pytest.mark.parametrize("division", ["immediate_limit", "priority_limit"])
def test_priceless_division_rejects_price(division):
    """최유리/최우선은 시장이 가격을 정한다 -- price 를 주면 조용히 무시하지 않고 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError, match="price 를 줄 수 없다"):
        _client(fake).domestic.stock("005930").buy(quantity=10, limit_price=70000, division=division)
    assert fake.calls == []


def test_division_requires_domestic_exchange():
    """division 은 국내 현금주문 전용 -- 해외 거래소와 조합하면 생성 시점에 거부."""
    from kis_trader.order import Order
    with pytest.raises(KISUsageError, match="국내 현금주문"):
        Order.market("AAPL", side="buy", quantity=10, division="immediate_limit", exchange="NASD")


# --- Tier2 division DATA invariants (midpoint / after-hours) ----------------
@pytest.mark.parametrize("division", ["midpoint", "pre_market_close", "post_market_close"])
def test_priceless_tier2_division_constructs_as_market(division):
    """중간가·장전/장후 시간외는 가격 없는 시장가 기반으로 구성된다."""
    from kis_trader.order import Order
    order = Order.market("005930", side="buy", quantity=10, division=division)
    assert order.order_type == "market"
    assert order.limit_price is None


@pytest.mark.parametrize("division", ["midpoint", "pre_market_close", "post_market_close"])
def test_priceless_tier2_division_rejects_price(division):
    """가격 없는 Tier2 division 에 limit_price 를 주면 구성 시점에 거부."""
    from kis_trader.order import Order
    with pytest.raises(KISUsageError, match="시장가 기반"):
        Order.limit("005930", side="buy", quantity=10, limit_price=70000, division=division)


def test_after_hours_single_requires_price():
    """시간외 단일가(07)는 지정가 필수 -- limit 없이 구성하면 거부."""
    from kis_trader.order import Order
    with pytest.raises(KISUsageError, match="지정가"):
        Order.market("005930", side="buy", quantity=10, division="after_hours_single")


def test_after_hours_single_constructs_as_limit():
    from kis_trader.order import Order
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000,
                        division="after_hours_single")
    assert order.order_type == "limit"
    assert order.limit_price == Decimal(70000)


@pytest.mark.parametrize("board", ["NXT", "UN"])
@pytest.mark.parametrize("division", ["pre_market_close", "post_market_close", "after_hours_single"])
def test_after_hours_divisions_rejected_off_krx(board, division):
    """시간외 3종은 KRX 전용 -- NXT/UN 보드에서는 구성 시점에 거부."""
    from kis_trader.order import Order
    ctor = Order.limit if division == "after_hours_single" else Order.market
    kwargs = {"limit_price": 70000} if division == "after_hours_single" else {}
    with pytest.raises(KISUsageError, match="보드는 이 주문구분"):
        ctor("005930", side="buy", quantity=10, division=division, board=board, **kwargs)


@pytest.mark.parametrize("board", ["KRX", "NXT", "UN"])
def test_midpoint_allowed_on_every_board(board):
    from kis_trader.order import Order
    order = Order.market("005930", side="buy", quantity=10, division="midpoint", board=board)
    assert order.board == board


@pytest.mark.parametrize(
    "division", ["priority_limit", "midpoint", "pre_market_close",
                 "post_market_close", "after_hours_single"])
def test_tier2_divisions_rejected_on_derivatives(division):
    """파생(XKFE)은 조건부/최유리지정가만 -- Tier2 4종과 최우선지정가는 거부."""
    from kis_trader.order import Order
    ctor = Order.limit if division in ("conditional_limit", "after_hours_single") else Order.market
    kwargs = {"limit_price": 70000} if division == "after_hours_single" else {}
    with pytest.raises(KISUsageError, match="파생"):
        ctor("101W09", side="buy", quantity=1, division=division, exchange="XKFE", **kwargs)


def test_tier2_division_name_round_trips_through_fingerprint():
    """신규 division 이름은 지문에 실려 encode/decode 왕복한다(스키마 무변경)."""
    from kis_trader.order import Order, decode_fingerprint, encode_fingerprint
    order = Order.market("005930", side="buy", quantity=10, division="midpoint")
    restored = decode_fingerprint(encode_fingerprint(order.fingerprint))
    assert restored.division == "midpoint"
    assert restored == order.fingerprint


# --- 주문 와이어(수량/구분/TR) 전수 ----------------------------------------
@pytest.mark.parametrize(
    ("side", "price", "expected_tr", "expected_dvsn", "expected_unpr"),
    [("buy", 70000, "TTTC0012U", "00", "70000"), ("buy", None, "TTTC0012U", "01", "0"),
     ("sell", 70000, "TTTC0011U", "00", "70000"), ("sell", None, "TTTC0011U", "01", "0")],
)
def test_order_wire_quantity_and_division(side, price, expected_tr, expected_dvsn, expected_unpr):
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    getattr(_client(fake).domestic.stock("005930"), side)(quantity=17, limit_price=price)
    body = fake.calls[0]["body"]
    assert body["ORD_QTY"] == "17"
    assert body["ORD_DVSN"] == expected_dvsn
    assert body["ORD_UNPR"] == expected_unpr
    assert fake.calls[0]["tr_id"] == expected_tr


# --- Tier2 division wire (ORD_DVSN / ORD_UNPR) -----------------------------
@pytest.mark.parametrize(
    ("kwargs", "expected_dvsn", "expected_unpr"),
    [
        ({"quantity": 10, "division": "midpoint"}, "21", "0"),
        ({"quantity": 10, "division": "midpoint", "time_in_force": "ioc"}, "23", "0"),
        ({"quantity": 10, "division": "midpoint", "time_in_force": "fok"}, "24", "0"),
        ({"quantity": 10, "division": "pre_market_close"}, "05", "0"),
        ({"quantity": 10, "division": "post_market_close"}, "06", "0"),
        ({"quantity": 10, "limit_price": 70000, "division": "after_hours_single"}, "07", "70000"),
    ],
)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_tier2_division_wire(side, kwargs, expected_dvsn, expected_unpr):
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    getattr(_client(fake).domestic.stock("005930"), side)(**kwargs)
    body = fake.calls[0]["body"]
    assert body["ORD_DVSN"] == expected_dvsn
    assert body["ORD_UNPR"] == expected_unpr


@pytest.mark.parametrize("kwargs", [
    {"quantity": 10, "division": "after_hours_single", "limit_price": 70000, "time_in_force": "ioc"},
    {"quantity": 10, "division": "pre_market_close", "time_in_force": "fok"},
    {"quantity": 10, "division": "midpoint", "time_in_force": "gtc"},
])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_tier2_unmapped_tif_rejected_before_wire(side, kwargs):
    """day 전용 시간외에 IOC/FOK, midpoint 에 gtc 는 미매핑 -> 조용히 day 로 안 바꾸고 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError, match="지원하지 않는 주문구분"):
        getattr(_client(fake).domestic.stock("005930"), side)(**kwargs)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("kwargs", "expected_dvsn", "expected_unpr"),
    [
        # 가격기준 division (Tier 1)
        ({"quantity": 10, "division": "immediate_limit"}, "03", "0"),
        ({"quantity": 10, "limit_price": 70000, "division": "conditional_limit"}, "02", "70000"),
        ({"quantity": 10, "division": "priority_limit"}, "04", "0"),
        # IOC/FOK 는 time_in_force 재사용
        ({"quantity": 10, "limit_price": 70000, "time_in_force": "ioc"}, "11", "70000"),
        ({"quantity": 10, "limit_price": 70000, "time_in_force": "fok"}, "12", "70000"),
        ({"quantity": 10, "time_in_force": "ioc"}, "13", "0"),
        ({"quantity": 10, "time_in_force": "fok"}, "14", "0"),
        ({"quantity": 10, "division": "immediate_limit", "time_in_force": "ioc"}, "15", "0"),
        ({"quantity": 10, "division": "immediate_limit", "time_in_force": "fok"}, "16", "0"),
    ],
)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_domestic_order_sends_expected_order_division_and_price(side, kwargs, expected_dvsn, expected_unpr):
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    getattr(_client(fake).domestic.stock("005930"), side)(**kwargs)
    body = fake.calls[0]["body"]
    assert body["ORD_DVSN"] == expected_dvsn
    assert body["ORD_UNPR"] == expected_unpr


def test_reconcile_matches_division_order_own_row_not_sibling_market():
    """타임아웃된 최유리(03) 주문은 자기 03 행에 매칭돼야 한다 -- order_type=market 이라고 01 로 찾아
    무관한 시장가(01) 주문 행을 오확정하면 안 된다(보수적 reconcile)."""
    store = OrderStore()
    cid = "20240101-imm-rc01"
    place_t = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")]})
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").buy(
            quantity=10, division="immediate_limit", client_order_id=cid)
    rows = [_daily_order_row(odno="MKT01", order_division="01", order_unit_price="0"),
            _daily_order_row(odno="IMM03", order_division="03", order_unit_price="0")]
    recon_t = FakeTransport(by_path={_DAILY_CCLD: [_daily_orders_response(rows)]})
    report = _client(recon_t, store=store).orders.reconcile(cid)
    assert report is not None
    assert report.order_id == "IMM03"        # 자기 03 행, 시장가 01 행 아님


def test_modify_division_order_sends_its_order_division():
    """최유리(03) 주문의 정정 와이어는 ORD_DVSN 03 을 실어야 한다(order_type=market 이라고 01 아님)."""
    store = OrderStore()
    cid = "20240101-imm-mod01"
    place_t = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(place_t, store=store).domestic.stock("005930").buy(
        quantity=10, division="immediate_limit", client_order_id=cid)
    change_t = FakeTransport(response=RawResponse(
        rt_cd="0", msg_cd="A", msg1="", body={"output": {"ODNO": "0030000999"}}))
    _client(change_t, store=store).orders.modify(cid, limit_price=71000, request_id="mod-1")
    assert change_t.calls[0]["body"]["ORD_DVSN"] == "03"


def test_same_division_and_id_replays_without_resend():
    """같은 division + 같은 client_order_id 는 replay -- 두 번째 호출은 와이어를 다시 때리지 않는다."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    store = OrderStore()
    kis = _client(fake, store=store)
    first = kis.domestic.stock("005930").buy(quantity=10, division="immediate_limit", client_order_id="ID-1")
    second = kis.domestic.stock("005930").buy(quantity=10, division="immediate_limit", client_order_id="ID-1")
    assert second == first
    assert len(fake.calls) == 1


def test_division_persists_across_store_reopen(tmp_path):
    """store v4 라운드트립 -- 최유리 주문의 division 이 닫고 다시 열어도 지문에 보존돼야 dedup 이 유지된다."""
    path = tmp_path / "orders.json"
    cid = "20240101-imm-persist01"
    store1 = OrderStore(path=path)
    _client(FakeTransport(response=_ACCEPTED_ORDER_RESPONSE), store=store1).domestic.stock(
        "005930").buy(quantity=10, division="immediate_limit", client_order_id=cid)
    store1.close()
    assert OrderStore(path=path).fingerprint_for(cid).division == "immediate_limit"


@pytest.mark.parametrize("kwargs", [
    {"symbol": "005930", "side": "buy", "quantity": 10, "division": "conditional_limit"},  # 조건부인데 market 기반
    {"symbol": "005930", "side": "buy", "quantity": 10, "division": "immediate_limit", "limit_price": 70000},  # 최유리인데 가격
])
def test_order_construction_enforces_division_price_coupling(kwargs):
    """Order.* 생성자도 division↔order_type↔price 결합을 강제해야 한다(와이어 fail-open 방지)."""
    from kis_trader.order import Order
    with pytest.raises(KISUsageError):
        (Order.limit if "limit_price" in kwargs else Order.market)(**kwargs)


@pytest.mark.parametrize("market,expected_excg", [("KRX", "KRX"), ("NXT", "NXT"), ("UN", "SOR")])
def test_board_routes_exchange_id(market, expected_excg):
    """보드(KRX/NXT/UN)가 EXCG_ID_DVSN_CD(KRX/NXT/SOR)로 라우팅돼야 한다(현재 KRX 하드코딩 버그)."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    _client(fake).domestic.stock("005930", market=market).buy(quantity=10, division="immediate_limit")
    assert fake.calls[0]["body"]["EXCG_ID_DVSN_CD"] == expected_excg


@pytest.mark.parametrize("market,kwargs", [
    ("NXT", {"quantity": 10}),                                          # NXT 시장가 미지원
    ("NXT", {"quantity": 10, "limit_price": 70000, "division": "conditional_limit"}),  # NXT 조건부 미지원
    ("UN", {"quantity": 10, "limit_price": 70000, "division": "conditional_limit"}),   # SOR 조건부 미지원
])
def test_board_rejects_unsupported_division(market, kwargs):
    """보드별 미지원 주문구분은 와이어 전 거부(NXT=시장가·조건부, SOR=조건부)."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930", market=market).buy(**kwargs)
    assert fake.calls == []


def test_non_krx_board_rejected_in_demo():
    """모의투자는 KRX만 -- NXT/UN 주문은 demo 에서 와이어 전 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").domestic.stock("005930", market="NXT").buy(
            quantity=10, division="immediate_limit")
    assert fake.calls == []


def test_board_distinguishes_dedup_fingerprint():
    """같은 종목·수량·주문구분이라도 KRX vs NXT 는 다른 주문 -- 같은 id 재사용은 CONFLICT."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930", market="KRX").buy(quantity=10, division="immediate_limit", client_order_id="ID-1")
    with pytest.raises(KISUsageError):
        kis.domestic.stock("005930", market="NXT").buy(
            quantity=10, division="immediate_limit", client_order_id="ID-1")


def test_reconcile_matches_board_not_other_board():
    """타임아웃된 NXT 주문은 자기 NXT 행에 매칭돼야 한다(같은 종목·수량의 KRX 행 오확정 금지)."""
    store = OrderStore()
    cid = "20240101-nxt-rc01"
    place_t = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")]})
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930", market="NXT").buy(
            quantity=10, division="immediate_limit", client_order_id=cid)
    rows = [_daily_order_row(odno="KRXROW", order_division="03", order_unit_price="0", excg="KRX"),
            _daily_order_row(odno="NXTROW", order_division="03", order_unit_price="0", excg="NXT")]
    recon_t = FakeTransport(by_path={_DAILY_CCLD: [_daily_orders_response(rows)]})
    report = _client(recon_t, store=store).orders.reconcile(cid)
    assert report is not None
    assert report.order_id == "NXTROW"


def test_old_schema_store_loads_with_empty_division(tmp_path):
    """v3(division 없는 11필드) 저장소도 division='' 로 하위호환 로드된다 -- 기존 영속 dedup 저장소가
    깨지면 재시작 후 이중체결 장벽이 사라지므로, 구버전 읽기는 안전상 반드시 보존돼야 한다."""
    import json
    path = tmp_path / "orders.json"
    fp_v3 = ["005930", "buy", "market", "10", "", "", "day", "XKRX", "", "", "regular"]  # 11필드(division 없음)
    path.write_text(json.dumps({
        "schema_version": 3, "in_flight": ["ID-old"],
        "fingerprints": {"ID-old": fp_v3}, "reports": {},
    }), encoding="utf-8")
    fp = OrderStore(path=path).fingerprint_for("ID-old")
    assert fp is not None
    assert fp.division == ""
    assert fp.session == "regular"
    assert fp.board == "KRX"          # v5 신규 필드도 기본값으로 하위호환


def test_old_schema_report_loads_with_none_org_number(tmp_path):
    """v5 이하 리포트(organization_number 키 없음)도 org=None 으로 하위호환 로드된다 -- 그 주문은
    재기동 후 정정취소 불가(종전과 동일)이나, 저장소는 깨지지 않고 dedup 이 유지돼야 한다."""
    import json
    path = tmp_path / "orders.json"
    report_v5 = {                     # organization_number 키 없음(구버전)
        "client_order_id": "ID-old", "order_id": "0000117057", "symbol": "005930",
        "side": "buy", "status": "new", "filled_quantity": "0", "average_price": None,
        "submitted_at": "2026-08-11T09:00:00+09:00",
    }
    fp_v3 = ["005930", "buy", "limit", "10", "70000", "", "day", "XKRX", "", "", "regular"]
    path.write_text(json.dumps({
        "schema_version": 5, "in_flight": [],
        "fingerprints": {"ID-old": fp_v3}, "reports": {"ID-old": report_v5},
    }), encoding="utf-8")
    report = OrderStore(path=path).report_for("ID-old")
    assert report is not None
    assert report.organization_number is None      # 누락 키 -> None(하위호환)
    assert report.order_id == "0000117057"          # 나머지 필드는 정상 로드


def test_corrupt_json_store_fails_closed(tmp_path):
    """파싱 불가한(손상) 저장소는 빈 상태로 시작하지 않고 KISError 로 거부한다 -- 빈 시작은 dedup
    장벽을 지워 재시작 후 이중체결 위험이므로 자동 복구하지 않는다."""
    path = tmp_path / "orders.json"
    path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(KISError):
        OrderStore(path=path)


def test_corrupt_record_store_fails_closed(tmp_path):
    """스키마 버전은 맞지만 레코드 값이 손상된(잘못된 status) 저장소도 fail-closed -- 조용히 버리지 않는다."""
    import json
    path = tmp_path / "orders.json"
    bad_report = {
        "client_order_id": "ID", "order_id": "1", "symbol": "005930", "side": "buy",
        "status": "not_a_real_status", "filled_quantity": "0", "average_price": None,
        "submitted_at": "2026-08-11T09:00:00+09:00",
    }
    path.write_text(json.dumps({
        "schema_version": 6, "in_flight": [], "fingerprints": {}, "reports": {"ID": bad_report},
    }), encoding="utf-8")
    with pytest.raises(KISError):
        OrderStore(path=path)


def test_unsupported_schema_version_rejected(tmp_path):
    """미래/미지원 스키마 버전은 손상이 아니라 미지원으로 명확히 거부한다(구 바이너리가 신버전 파일을
    손상으로 오진단하지 않게)."""
    import json
    path = tmp_path / "orders.json"
    path.write_text(json.dumps({
        "schema_version": 999, "in_flight": [], "fingerprints": {}, "reports": {},
    }), encoding="utf-8")
    with pytest.raises(UnsupportedSchemaVersionError):
        OrderStore(path=path)


def test_second_opener_rejected_until_first_releases_flock(tmp_path):
    """같은 경로의 두 번째 OrderStore 는 단일라이터 락(flock)에 막혀 열리지 않고, 첫 스토어를 닫으면
    다시 열 수 있다 -- 프로세스 간(및 같은 프로세스의 두 인스턴스) 이중전송 방지."""
    path = tmp_path / "orders.json"
    first = OrderStore(path=path)
    try:
        with pytest.raises(KISError):
            OrderStore(path=path)          # 두 번째 라이터는 거부된다
    finally:
        first.close()
    reopened = OrderStore(path=path)       # 첫 락 해제 후 재오픈 가능
    reopened.close()


def test_irp_account_is_auto_read_only():
    """IRP(상품코드 29)는 주문불가 계좌(공식 FAQ) -- 계좌 상품코드로 자동 유도해 주문을 막는다."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(AccountNotOrderableError):
        _client(fake, account="12345678-29").domestic.stock("005930").buy(quantity=1, limit_price=70000)


def test_irp_manual_orderable_true_still_blocked():
    """수동 orderable=True 여도 IRP(29)는 막힌다 -- 자동유도가 우선(더 제약만 가능)."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(AccountNotOrderableError):
        _client(fake, account="12345678-29", orderable=True).domestic.stock(
            "005930").buy(quantity=1, limit_price=70000)


def test_dc_account_rejected_at_construction():
    """DC가입자(55)는 Open API 이용 자체가 불가(공식 FAQ) -- 세션 생성 시 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError, match="DC|이용|55"):
        _client(fake, account="12345678-55")


def test_pension_savings_22_is_orderable():
    """연금저축(22)은 주문 가능 -- IRP(29)와 달리 막지 않는다(혼동 주의)."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake, account="12345678-22").domestic.stock("005930").buy(
        quantity=1, limit_price=70000)
    assert report.order_id == "0000117057"


def test_ordinary_code_and_no_account_are_not_special_cased():
    """29/55 만 특수처리 -- 일반 위탁(01)은 주문 가능, account 미설정은 생성 성공(상품코드 유도
    우회, DC/IRP 오탐 없음)."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    report = _client(fake, account="12345678-01").domestic.stock("005930").buy(
        quantity=1, limit_price=70000)
    assert report.order_id == "0000117057"                       # 01 은 주문 가능
    kis = _client(fake, account=None)                            # 생성 성공(DC 거부 아님)
    with pytest.raises(KISUsageError):                           # 주문은 계좌 필요(orderable 거부 아님)
        kis.domestic.stock("005930").buy(quantity=1, limit_price=70000)


@pytest.mark.parametrize("market", ["NXT", "UN"])
def test_credit_order_rejects_non_krx_board(market):
    """신용주문은 보드 배선이 아직 없어 KRX 만 -- NXT/UN 종목의 신용주문은 조용히 KRX 로 안 보내고 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError):
        _client(fake, allow_credit=True).domestic.stock("005930", market=market).credit_buy(
            quantity=10, credit_type="21")
    assert fake.calls == []


@pytest.mark.parametrize("market", ["NXT", "UN"])
def test_reserved_order_rejects_non_krx_board(market):
    """예약주문도 보드 배선이 아직 없어 KRX 만 -- NXT/UN 종목의 예약주문은 거부."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    with pytest.raises(KISUsageError):
        _client(fake).domestic.stock("005930", market=market).reserve_buy(quantity=10, limit_price=70000)
    assert fake.calls == []


def test_board_persists_across_store_reopen(tmp_path):
    """store v5 라운드트립 -- NXT 주문의 board 가 닫고 다시 열어도 지문에 보존돼야 dedup 이 유지된다."""
    path = tmp_path / "orders.json"
    cid = "20240101-nxt-persist01"
    store1 = OrderStore(path=path)
    _client(FakeTransport(response=_ACCEPTED_ORDER_RESPONSE), store=store1).domestic.stock(
        "005930", market="NXT").buy(quantity=10, division="immediate_limit", client_order_id=cid)
    store1.close()
    assert OrderStore(path=path).fingerprint_for(cid).board == "NXT"


def test_division_distinguishes_dedup_fingerprint():
    """같은 종목·수량이라도 시장가 vs 최유리는 다른 주문 -- 같은 client_order_id 재사용은 지문 충돌."""
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    store = OrderStore()
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, client_order_id="ID-1")             # 시장가(01)
    with pytest.raises(KISUsageError):     # 같은 id, 다른 주문(최유리 03) -> CONFLICT, replay 아님
        kis.domestic.stock("005930").buy(
            quantity=10, division="immediate_limit", client_order_id="ID-1"
        )


def test_rejected_then_retry_sends_twice():
    fake = FakeTransport(by_path={_ORDER_CASH: [_REJECTED_ORDER_RESPONSE, _ACCEPTED_ORDER_RESPONSE]})
    kis = _client(fake)
    with pytest.raises(OrderRejectedError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert report.order_id == "0000117057"
    assert len(fake.calls) == 2                   # 거부 후 재전송이 실제로 한 번 더 나감


# --- 재조회 지문 매칭 정확성 -----------------------------------------------
@pytest.mark.parametrize(
    "mismatch",
    [{"order_unit_price": "70001"}, {"symbol": "000660"}, {"side_code": "01"},
     {"order_quantity": "11"}],
    ids=["wrong-price", "wrong-symbol", "wrong-side", "wrong-quantity"],
)
def test_reconcile_ignores_mismatched_fingerprint(mismatch):
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(**mismatch)])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    assert kis.orders.reconcile("ID-1") is None          # 지문 불일치 -> 0건 -> 오귀속 안 함


@pytest.mark.parametrize(
    ("row_updates", "expected_status", "expected_filled"),
    [({"filled_quantity": "4"}, OrderStatus.PARTIALLY_FILLED, Decimal(4)),
     ({"filled_quantity": "0", "rejected_quantity": "10", "average_price": "0"}, OrderStatus.REJECTED, Decimal(0)),
     ({"filled_quantity": "3", "canceled": "Y"}, OrderStatus.CANCELED, Decimal(3))],
    ids=["partial", "rejected", "canceled"],
)
def test_reconcile_maps_daily_row_status(row_updates, expected_status, expected_filled):
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(**row_updates)])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.orders.reconcile("ID-1")
    assert report is not None
    assert report.status is expected_status
    assert report.filled_quantity == expected_filled


def test_reconcile_daily_ccld_failure_fails_closed():
    failed = RawResponse(rt_cd="1", msg_cd="APBK9999", msg1="조회 실패", body={})
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")], _DAILY_CCLD: [failed]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                 # 조회 실패를 빈 결과(미접수)로 오인하지 않음
        kis.orders.reconcile("ID-1")


def test_reconcile_scans_all_daily_ccld_pages():
    page1 = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok",
                        body={"output1": [_daily_order_row(odno="A")],
                              "ctx_area_fk100": "FK2", "ctx_area_nk100": "NK2"})
    page2 = _daily_orders_response([_daily_order_row(odno="B")])
    fake = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")], _DAILY_CCLD: [page1, page2]})
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):                 # 두 페이지 걸쳐 2건 -> 모호 -> 자동확정 불가
        kis.orders.reconcile("ID-1")
    assert fake.calls[2]["params"]["CTX_AREA_NK100"] == "NK2"   # 2페이지째에 연속키 전달


def test_reconcile_full_report_semantics():
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(filled_quantity="10", average_price="69950")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.orders.reconcile("ID-1")
    assert report is not None
    assert report.client_order_id == "ID-1"
    assert report.order_id == "0000117057"
    assert report.symbol == "005930"
    assert report.side == "buy"
    assert report.status is OrderStatus.FILLED
    assert report.average_price == Decimal(69950)


# --- fail-closed 가드 회귀(비유한 수치 / 날짜 엄격성 / 재조회 경계 / store) --------------
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "nan", "inf", "-inf", "Infinity"])
def test_coerce_decimal_rejects_non_finite(bad):
    """A-11: coerce_decimal 은 NaN/Infinity(및 "nan"/"inf" 문자열)를 지문·와이어 전에 거부한다."""
    from kis_trader.order import coerce_decimal
    with pytest.raises(KISUsageError):
        coerce_decimal(bad, "quantity")


def test_order_construction_rejects_non_finite_price():
    """A-11: 비유한 가격은 Order 생성 시점에 fail-closed -- 와이어에 NaN 이 실릴 수 없다."""
    from kis_trader.order import Order
    with pytest.raises(KISUsageError):
        Order.limit("005930", side="buy", quantity=10, limit_price=float("nan"))


def test_modify_rejects_non_finite_price_before_wire():
    """A-03: 정정 경로도 coerce_decimal 을 거쳐 비유한 가격을 와이어 전에 거부한다(무전송)."""
    store = OrderStore()
    fake = FakeTransport(response=_ACCEPTED_ORDER_RESPONSE)
    kis = _client(fake, store=store)
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="orig-nf")
    calls_before = len(fake.calls)
    with pytest.raises(KISUsageError):
        kis.orders.modify("orig-nf", limit_price=float("inf"), request_id="mod-nf")
    assert len(fake.calls) == calls_before          # 정정 와이어에 닿기 전 거부


def test_reconcile_non_finite_row_quantity_fails_closed():
    """A-01: 벤더 행의 비유한 수량은 비교로 흘러가지 않고 재조회가 fail-closed 한다(오확정 방지)."""
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(order_quantity="nan")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    with pytest.raises(KISError):
        kis.orders.reconcile("ID-1")


def test_reconcile_blank_division_row_does_not_match_known_division_order():
    """A-02: 빈 ord_dvsn_cd 행은 division 명시 주문(최유리 03)을 확정하지 못한다(어떤 구분일지 몰라 오확정 위험)."""
    store = OrderStore()
    cid = "20240101-imm-blank01"
    place_t = FakeTransport(by_path={_ORDER_CASH: [TransportTimeout("t")]})
    with pytest.raises(OrderTimeoutError):
        _client(place_t, store=store).domestic.stock("005930").buy(
            quantity=10, division="immediate_limit", client_order_id=cid)
    rows = [_daily_order_row(odno="BLANK", order_division="", order_unit_price="0")]
    recon_t = FakeTransport(by_path={_DAILY_CCLD: [_daily_orders_response(rows)]})
    assert _client(recon_t, store=store).orders.reconcile(cid) is None    # 빈 구분 행은 매칭 안 함


def test_reconcile_blank_division_row_still_matches_plain_order():
    """A-02: division 미지정 일반 주문은 종전대로 빈 ord_dvsn_cd 행을 관용한다(happy path 불변)."""
    fake = FakeTransport(by_path={
        _ORDER_CASH: [TransportTimeout("t")],
        _DAILY_CCLD: [_daily_orders_response([_daily_order_row(order_division="")])],
    })
    kis = _client(fake)
    with pytest.raises(OrderTimeoutError):
        kis.domestic.stock("005930").buy(quantity=10, limit_price=70000, client_order_id="ID-1")
    report = kis.orders.reconcile("ID-1")
    assert report is not None       # 빈 구분 관용(일반 주문) -- 종전 동작 유지


@pytest.mark.parametrize("bad", ["1234567", "123456789", "2026010 ", " 2026010",
                                 "2026-01-01", "２０２５０１０１"])
def test_validate_yyyymmdd_requires_exactly_8_ascii_digits(bad):
    """A-19: 7/9자리·공백·비-ASCII 숫자는 거부(정확히 8자리 ASCII 숫자만)."""
    from kis_trader.order import validate_yyyymmdd
    with pytest.raises(KISUsageError):
        validate_yyyymmdd(bad, "date")


def test_validate_yyyymmdd_accepts_valid_8_digit():
    """A-19: 정상 8자리 날짜는 통과(happy path 불변)."""
    from kis_trader.order import validate_yyyymmdd
    validate_yyyymmdd("20260101", "date")           # 예외 없음


def test_load_rejects_non_object_root(tmp_path):
    """A-21: 루트가 JSON object 가 아니면(배열/스칼라) AttributeError 로 새지 않고 손상으로 fail-closed."""
    import json
    path = tmp_path / "orders.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(KISError):
        OrderStore(path=path)


def test_fsync_dir_propagates_real_io_error(tmp_path, monkeypatch):
    """A-20: 디렉터리 fsync 의 진짜 IO 실패(EIO)는 삼키지 않고 올린다(거짓 '저장됨' 방지)."""
    from kis_trader import store as store_mod

    def boom(fd):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(store_mod.os, "fsync", boom)
    with pytest.raises(OSError):
        store_mod._fsync_dir(tmp_path)


def test_fsync_dir_ignores_unsupported_platform(tmp_path, monkeypatch):
    """A-20: 디렉터리 fsync 미지원(EINVAL)은 종전대로 무시한다 -- 플랫폼 한계는 실패로 보지 않는다."""
    from kis_trader import store as store_mod

    def unsupported(fd):
        raise OSError(errno.EINVAL, "not supported")

    monkeypatch.setattr(store_mod.os, "fsync", unsupported)
    store_mod._fsync_dir(tmp_path)                    # 예외 없이 반환

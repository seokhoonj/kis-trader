"""해외선물옵션(08) 발주 -- kis.overseas.futures(srs_cd).buy/sell (OTFM3001U, 실전 전용).

국내주식과 같은 안전 코어(place)를 공유하되 와이어 조립기와 엄격 output 파서만 해외선물옵션용이다.
이중체결 방지·재시도 금지·dedup 지문(exchange 슬롯의 "OSFO" 값으로 구분)을 네트워크 없이 가짜
전송으로 검증한다. 해외선물옵션은 실전 전용(모의 미지원)이고 실주문이라 라이브 검증은 불가하며,
여기선 목킹 전송으로만 확인한다. 응답 output(``{ORD_DT, ODNO}``)에서 ODNO->order_id,
ORD_DT->receipt_date 가 리포트에 실려 이후 정정·취소(원주문일자 지목)가 가능해진다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order, OrderStore
from kis_trader.errors import KISUsageError, OrderError
from kis_trader.overseas._engine import derivative_orders as osfo
from kis_trader.report import ExecutionReport
from kis_trader.transport import RawResponse

_PLACE = "/uapi/overseas-futureoption/v1/trading/order"
_ACCEPTED = RawResponse(
    rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
    body={"output": {"ORD_DT": "20260819", "ODNO": "0000007045"}},
)


class FakeTransport:
    """모든 호출을 기록하고 고정 응답을 주는 가짜 전송."""

    def __init__(self, response=_ACCEPTED):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        return self.response

    @property
    def request_count(self) -> int:
        return len(self.calls)


def _client(transport, *, environment="real", store=None, risk=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment=environment, transport=transport, store=store, risk=risk)


def _order(**kw):
    base = {
        "symbol": "6BZ22", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
        "limit_price": Decimal("1.17"), "exchange": "OSFO", "client_order_id": "c",
    }
    base.update(kw)
    return Order(**base)


# --- exchange marker ------------------------------------------------------
def test_is_overseas_fo_exchange():
    assert osfo.is_overseas_fo_exchange("OSFO") is True
    assert osfo.is_overseas_fo_exchange("XKFE") is False
    assert osfo.is_overseas_fo_exchange("BOND") is False


# --- 와이어 골든바디 ------------------------------------------------------
def test_make_order_request_limit_buy_body():
    req = osfo.make_order_request(_order(), cano="81012345", product_code="08",
                                  environment="real")
    assert req.method == "POST"
    assert req.path == osfo._PLACE_PATH
    assert req.tr_id == "OTFM3001U"
    assert req.body == {
        "CANO": "81012345", "ACNT_PRDT_CD": "08", "OVRS_FUTR_FX_PDNO": "6BZ22",
        "SLL_BUY_DVSN_CD": "02",
        "FM_LQD_USTL_CCLD_DT": "", "FM_LQD_USTL_CCNO": "",
        "PRIC_DVSN_CD": "1", "FM_LIMIT_ORD_PRIC": "1.17", "FM_STOP_ORD_PRIC": "",
        "FM_ORD_QTY": "1", "FM_LQD_LMT_ORD_PRIC": "", "FM_LQD_STOP_ORD_PRIC": "",
        "CCLD_CNDT_CD": "6", "CPLX_ORD_DVSN_CD": "0", "ECIS_RSVN_ORD_YN": "N",
        "FM_HDGE_ORD_SCRN_YN": "N",
    }


def test_make_order_request_market_uses_price_division_2():
    req = osfo.make_order_request(_order(order_type="market", limit_price=None),
                                  cano="8", product_code="08", environment="real")
    assert req.body["PRIC_DVSN_CD"] == "2"
    assert req.body["FM_LIMIT_ORD_PRIC"] == ""
    assert req.body["FM_STOP_ORD_PRIC"] == ""


def test_make_order_request_stop_uses_price_division_3():
    order = _order(order_type="stop", limit_price=None, stop_price=Decimal("1.20"))
    req = osfo.make_order_request(order, cano="8", product_code="08", environment="real")
    assert req.body["PRIC_DVSN_CD"] == "3"
    assert req.body["FM_STOP_ORD_PRIC"] == "1.20"
    assert req.body["FM_LIMIT_ORD_PRIC"] == ""


def test_make_order_request_sell_side():
    req = osfo.make_order_request(_order(side="sell"), cano="8", product_code="08",
                                  environment="real")
    assert req.body["SLL_BUY_DVSN_CD"] == "01"


def test_make_order_request_rejects_non_osfo():
    order = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
                  limit_price=Decimal(70000), exchange="XKRX", client_order_id="c")
    with pytest.raises(OrderError):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


def test_make_order_request_paper_rejected():
    with pytest.raises(KISUsageError, match="모의"):
        osfo.make_order_request(_order(), cano="8", product_code="08", environment="paper")


def test_make_order_request_rejects_stop_limit():
    # 스탑지정가는 해외선물옵션 v1 미지원 -- 조용히 지정가로 나가지 않게 거부.
    order = _order(order_type="stop_limit", stop_price=Decimal("1.10"))
    with pytest.raises(KISUsageError, match="지정가/시장가/STOP"):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


def test_make_order_request_rejects_fractional_quantity():
    order = _order(quantity=Decimal("1.5"))
    with pytest.raises(KISUsageError, match="계약 단위 정수"):
        osfo.make_order_request(order, cano="8", product_code="08", environment="real")


# --- 엄격 output 파서 -----------------------------------------------------
def test_extract_output_rejects_missing_output_key():
    with pytest.raises(OrderError):
        osfo.extract_output({"rt_cd": "0", "ODNO": "top-level", "ORD_DT": "20260819"})


def test_extract_output_rejects_non_mapping():
    with pytest.raises(OrderError):
        osfo.extract_output({"output": ["not", "a", "mapping"]})


def test_extract_output_accepts_mapping():
    assert osfo.extract_output({"output": {"ODNO": "0000007045", "ORD_DT": "20260819"}}) == {
        "ODNO": "0000007045", "ORD_DT": "20260819",
    }


# --- end-to-end: handle.buy -> _place_order -> 전송 -> ExecutionReport -----
def test_os_fo_buy_wire():
    fake = FakeTransport()
    report = _client(fake).overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    assert report.receipt_date == "20260819"      # ORD_DT 가 receipt_date 로 영속(정정·취소 대비)
    assert fake.request_count == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == _PLACE
    assert call["tr_id"] == "OTFM3001U"
    assert call["body"]["OVRS_FUTR_FX_PDNO"] == "6BZ22"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "02"
    assert call["body"]["PRIC_DVSN_CD"] == "1"
    assert call["body"]["FM_LIMIT_ORD_PRIC"] == "1.17"
    assert call["body"]["FM_ORD_QTY"] == "1"


def test_os_fo_sell_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").sell(quantity=2, limit_price="1.20")
    assert fake.calls[0]["body"]["SLL_BUY_DVSN_CD"] == "01"
    assert fake.calls[0]["body"]["FM_ORD_QTY"] == "2"


def test_os_fo_market_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1)
    assert fake.calls[0]["body"]["PRIC_DVSN_CD"] == "2"
    assert fake.calls[0]["body"]["FM_LIMIT_ORD_PRIC"] == ""


def test_os_fo_stop_wire():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1, stop_price="1.30")
    assert fake.calls[0]["body"]["PRIC_DVSN_CD"] == "3"
    assert fake.calls[0]["body"]["FM_STOP_ORD_PRIC"] == "1.30"


def test_os_fo_routes_to_overseas_fo_builder_not_stock_tr():
    fake = FakeTransport()
    _client(fake).overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert fake.calls[0]["tr_id"] == "OTFM3001U"
    assert fake.calls[0]["path"] == _PLACE


def test_os_fo_buy_dedup_idempotent():
    store = OrderStore()
    fake = FakeTransport()
    cid = "20260819-osfobuy000000001"
    handle = _client(fake, store=store).overseas.futures("6BZ22")
    r1 = handle.buy(quantity=1, limit_price="1.17", client_order_id=cid)
    r2 = handle.buy(quantity=1, limit_price="1.17", client_order_id=cid)
    assert r1.order_id == r2.order_id
    assert fake.request_count == 1              # 공유 안전 코어가 재전송을 막는다


def test_os_fo_buy_paper_fails_closed():
    fake = FakeTransport()
    with pytest.raises(KISUsageError):
        _client(fake, environment="paper").overseas.futures("6BZ22").buy(
            quantity=1, limit_price="1.17")
    assert fake.request_count == 0              # 모의 세션은 와이어 미접촉


def test_os_fo_buy_rejects_risk_gate():
    from kis_trader.risk import RiskLimits
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(KISUsageError):
        client.overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert fake.request_count == 0

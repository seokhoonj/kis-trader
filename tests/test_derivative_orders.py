"""국내 선물·옵션 주간(DAY) 발주 -- 조합표 리졸버·와이어 골든바디·엄격 output 파서, 그리고
client._place_order 의 XKFE 라우팅과 파생 리스크 게이팅(수량만 허용).

핸들 buy/sell 은 뒤 슬라이스 산출물이라, 여기선 :class:`Order` 를 직접 만들어 빌더와
client 라우팅/게이팅을 네트워크 없이 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order
from kis_trader.domestic._engine import derivative_orders as fo
from kis_trader.errors import KISUsageError, OrderError, PreTradeRiskError
from kis_trader.report import ExecutionReport
from kis_trader.risk import RiskLimits
from kis_trader.transport import RawResponse

_PLACE = "/uapi/domestic-futureoption/v1/trading/order"
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                        body={"output": {"ODNO": "0000007045"}})


def _order(**kw):
    base = {
        "symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
        "limit_price": Decimal("400.00"), "exchange": "XKFE", "session": "regular",
        "derivative_item": "01", "client_order_id": "c",
    }
    base.update(kw)
    return Order(**base)


# --- Step 1: 조합표 리졸버 -------------------------------------------------
@pytest.mark.parametrize("order_type,division,tif,expected", [
    ("limit", None, "day", ("01", "01", "0")),
    ("limit", None, "ioc", ("10", "01", "3")),
    ("limit", None, "fok", ("11", "01", "4")),
    ("market", None, "day", ("02", "02", "0")),
    ("market", None, "ioc", ("12", "02", "3")),
    ("market", None, "fok", ("13", "02", "4")),
    ("limit", "conditional_limit", "day", ("03", "03", "0")),
    ("market", "immediate_limit", "day", ("04", "04", "0")),
    ("market", "immediate_limit", "ioc", ("14", "04", "3")),
    ("market", "immediate_limit", "fok", ("15", "04", "4")),
])
def test_resolve_fo_codes(order_type, division, tif, expected):
    assert fo._resolve_fo_codes(order_type=order_type, division=division, time_in_force=tif) == expected


def test_resolve_fo_codes_rejects_unmapped():
    with pytest.raises(KISUsageError):
        fo._resolve_fo_codes(order_type="limit", division="conditional_limit", time_in_force="ioc")


def test_is_derivative_exchange():
    assert fo.is_derivative_exchange("XKFE") is True
    assert fo.is_derivative_exchange("XKRX") is False


# --- Step 4: 발주 골든바디(T1 주간) ---------------------------------------
def test_make_order_request_day_limit_buy():
    req = fo.make_order_request(_order(), cano="81012345", product_code="03", environment="real")
    assert req.method == "POST"
    assert req.path == fo._PLACE_PATH
    assert req.tr_id == "TTTO1101U"
    assert req.body == {
        "ORD_PRCS_DVSN_CD": "02", "CANO": "81012345", "ACNT_PRDT_CD": "03",
        "SLL_BUY_DVSN_CD": "02", "SHTN_PDNO": "101S03", "ORD_QTY": "1",
        "UNIT_PRICE": "400.00", "NMPR_TYPE_CD": "01", "KRX_NMPR_CNDT_CD": "0",
        "CTAC_TLNO": "", "FUOP_ITEM_DVSN_CD": "", "ORD_DVSN_CD": "01",
    }


def test_make_order_request_market_uses_zero_price():
    req = fo.make_order_request(_order(order_type="market", limit_price=None),
                               cano="8", product_code="03", environment="real")
    assert req.body["UNIT_PRICE"] == "0"
    assert req.body["ORD_DVSN_CD"] == "02"
    assert req.tr_id == "TTTO1101U"


def test_make_order_request_sell_side():
    req = fo.make_order_request(_order(side="sell"), cano="8", product_code="03", environment="real")
    assert req.body["SLL_BUY_DVSN_CD"] == "01"


def test_make_order_request_paper_uses_paper_tr():
    req = fo.make_order_request(_order(), cano="8", product_code="03", environment="paper")
    assert req.tr_id == "VTTO1101U"


def test_make_order_request_rejects_non_xkfe():
    order = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
                  limit_price=Decimal(70000), exchange="XKRX", client_order_id="c")
    with pytest.raises(OrderError):
        fo.make_order_request(order, cano="8", product_code="03", environment="real")


# --- Step 6: 엄격 output 파서(BC-3) ---------------------------------------
def test_extract_fo_output_rejects_missing_output_key():
    with pytest.raises(OrderError):
        fo._extract_fo_output({"rt_cd": "0", "ODNO": "top-level"})


def test_extract_fo_output_rejects_non_mapping():
    with pytest.raises(OrderError):
        fo._extract_fo_output({"output": ["not", "a", "mapping"]})


def test_extract_fo_output_accepts_mapping():
    assert fo._extract_fo_output({"output": {"ODNO": "0000007045"}}) == {"ODNO": "0000007045"}


# --- Step 8: client._place_order XKFE 라우팅 + 리스크 게이팅 ---------------
class FakeTransport:
    """모든 호출을 기록하고 고정 응답을 주는 가짜 전송."""

    def __init__(self, response=_ACCEPTED):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        return self.response

    @property
    def request_count(self) -> int:
        return len(self.calls)


def _client(transport, *, risk=None):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=transport, risk=risk)


def test_place_routes_xkfe_to_derivative_builder():
    fake = FakeTransport()
    report = _client(fake)._place_order(_order())
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    assert fake.request_count == 1
    call = fake.calls[0]
    assert call["path"] == _PLACE
    assert call["tr_id"] == "TTTO1101U"
    assert call["body"]["SHTN_PDNO"] == "101S03"


def test_place_order_rejects_notional_risk_on_xkfe():
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_notional=1_000_000))
    with pytest.raises(KISUsageError, match="max_order_quantity"):
        client._place_order(_order())
    assert fake.request_count == 0


def test_place_order_rejects_collar_risk_on_xkfe():
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(price_collar_percent=10))
    with pytest.raises(KISUsageError, match="max_order_quantity"):
        client._place_order(_order())
    assert fake.request_count == 0


def test_place_order_rejects_tick_risk_on_xkfe():
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(enforce_tick_size=True))
    with pytest.raises(KISUsageError, match="max_order_quantity"):
        client._place_order(_order())
    assert fake.request_count == 0


def test_place_order_allows_quantity_risk_on_xkfe_without_reference_quote():
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    client._place_order(_order())
    # 수량 한도만 -> 참조가 조회 없이 발주 1회(파생 발주 경로만 탄다).
    assert fake.request_count == 1
    assert fake.calls[0]["path"] == _PLACE


def test_place_order_quantity_cap_blocks_over_limit_on_xkfe():
    fake = FakeTransport()
    client = _client(fake, risk=RiskLimits(max_order_quantity=10))
    with pytest.raises(PreTradeRiskError):
        client._place_order(_order(quantity=Decimal(11)))
    assert fake.request_count == 0


def test_place_order_uses_strict_output_parser_on_xkfe():
    # top-level ODNO 만 있고 output(object) 없음 -> 엄격 파서가 재조회 불가로 fail-closed.
    bad = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="ok", body={"ODNO": "top-level"})
    fake = FakeTransport(response=bad)
    with pytest.raises(OrderError):
        _client(fake)._place_order(_order())

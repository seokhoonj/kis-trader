"""파생 계약 핸들의 발주 -- kis.domestic.futures(code).buy(...) / option(code, right=...).sell(...).

계약코드 길이 형상검증(선물 6 / 옵션 9), right(call/put) -> derivative_item(02/03) 매핑,
그리고 handle.buy -> client._place_order -> 가짜 전송 -> ExecutionReport 의 end-to-end 를
검증한다. night 세션의 파생(XKFE) 전용 정합성 가드(공유 Order 타입)도 함께 확인한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, Order
from kis_trader.errors import KISUsageError
from kis_trader.report import ExecutionReport
from kis_trader.transport import RawResponse

_PLACE = "/uapi/domestic-futureoption/v1/trading/order"
_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                        body={"output": {"ODNO": "0000007045"}})


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


@pytest.fixture
def fake_transport():
    return FakeTransport()


@pytest.fixture
def fake_client(fake_transport):
    return KISClient(app_key="k", app_secret="s", account="81012345-03",
                     environment="real", transport=fake_transport)


# --- 계약코드 길이 형상검증 -----------------------------------------------
def test_futures_symbol_must_be_6(fake_client):
    with pytest.raises(KISUsageError):
        fake_client.domestic.futures("101S0300").buy(quantity=1, limit_price=Decimal(400))  # 8자리


def test_option_symbol_must_be_9(fake_client):
    with pytest.raises(KISUsageError):
        fake_client.domestic.option("201S03", right="call").buy(quantity=1, limit_price=Decimal("2.5"))  # 6자리


# --- 수량 형상검증 -- 계약 단위 정수(fat-finger 방지) ---------------------
def test_futures_fractional_quantity_rejected_no_wire(fake_client, fake_transport):
    # P1-4: 소수 계약수량(1.5)은 와이어 전에 KISUsageError -- 전송에 닿지 않는다.
    with pytest.raises(KISUsageError):
        fake_client.domestic.futures("101S03").buy(quantity=Decimal("1.5"), limit_price=Decimal(400))
    assert fake_transport.request_count == 0


# --- right -> derivative_item 매핑 -----------------------------------------
def test_option_call_maps_to_02(fake_client):
    opt = fake_client.domestic.option("201S03370", right="call")
    order = opt._make_order("buy", quantity=1, limit_price=Decimal("2.5"),
                            time_in_force="day", division=None, night=False)
    assert order.derivative_item == "02"


def test_option_put_maps_to_03(fake_client):
    opt = fake_client.domestic.option("201S03370", right="put")
    order = opt._make_order("sell", quantity=1, limit_price=Decimal("2.5"),
                            time_in_force="day", division=None, night=False)
    assert order.derivative_item == "03"


def test_futures_maps_to_01(fake_client):
    fut = fake_client.domestic.futures("101S03")
    order = fut._make_order("buy", quantity=1, limit_price=Decimal(400),
                            time_in_force="day", division=None, night=False)
    assert order.derivative_item == "01"


def test_option_order_without_right_is_rejected(fake_client, fake_transport):
    # right 없이 조회 핸들로는 살아있지만(quote 등), 발주에는 right 가 필요하다 -- fail-closed.
    opt = fake_client.domestic.option("201S03370")
    with pytest.raises(KISUsageError):
        opt.buy(quantity=1, limit_price=Decimal("2.5"))
    assert fake_transport.request_count == 0            # 와이어 미접촉(pre-wire 거부)


def test_option_query_handle_needs_no_right(fake_client):
    # 옵션 조회 핸들은 계약코드만으로 충분하다 -- right 를 강요하지 않는다(구성은 right 없이 가능).
    opt = fake_client.domestic.option("201W09335")
    assert opt.market == "O"


# --- end-to-end: handle.buy/sell -> _place_order -> 전송 -> ExecutionReport ---
def test_futures_buy_end_to_end(fake_client, fake_transport):
    report = fake_client.domestic.futures("101S03").buy(quantity=2, limit_price=Decimal("400.00"))
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    assert fake_transport.request_count == 1
    call = fake_transport.calls[0]
    assert call["path"] == _PLACE
    assert call["tr_id"] == "TTTO1101U"                # 주간(정규) 실전
    assert call["body"]["SHTN_PDNO"] == "101S03"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "02"     # 매수
    assert call["body"]["ORD_QTY"] == "2"
    assert call["body"]["UNIT_PRICE"] == "400.00"
    assert call["body"]["ORD_DVSN_CD"] == "01"         # 지정가
    assert call["body"]["FUOP_ITEM_DVSN_CD"] == ""     # 주간은 상품구분 공란


def test_option_call_sell_end_to_end(fake_client, fake_transport):
    report = fake_client.domestic.option("201S03370", right="call").sell(
        quantity=1, limit_price=Decimal("2.50"),
    )
    assert isinstance(report, ExecutionReport)
    assert report.order_id == "0000007045"
    call = fake_transport.calls[0]
    assert call["path"] == _PLACE
    assert call["body"]["SHTN_PDNO"] == "201S03370"
    assert call["body"]["SLL_BUY_DVSN_CD"] == "01"     # 매도


def test_option_night_put_carries_derivative_item(fake_client, fake_transport):
    # 야간(STTN)은 실전 전용 -- FUOP_ITEM_DVSN_CD 에 상품구분(풋=03)이 실려 나간다.
    fake_client.domestic.option("201S03370", right="put").buy(quantity=1, limit_price=Decimal("2.50"), night=True)
    call = fake_transport.calls[0]
    assert call["tr_id"] == "STTN1101U"
    assert call["body"]["FUOP_ITEM_DVSN_CD"] == "03"


def test_futures_market_buy_uses_zero_price(fake_client, fake_transport):
    # limit_price 없으면 시장가로 해석 -> UNIT_PRICE 0.
    fake_client.domestic.futures("101S03").buy(quantity=1)
    call = fake_transport.calls[0]
    assert call["body"]["UNIT_PRICE"] == "0"
    assert call["body"]["ORD_DVSN_CD"] == "02"          # 시장가


# --- 공유 Order: night 세션은 파생(XKFE) 전용 정합성 가드 --------------------
def test_night_session_requires_derivative_exchange():
    # KRX 주식 거래소 + night 세션은 표현 불가능한 조합 -- 생성 시점에 fail-closed.
    with pytest.raises(KISUsageError):
        Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
              limit_price=Decimal(70000), exchange="XKRX", session="night", client_order_id="c")


def test_night_session_accepts_derivative_exchange():
    order = Order(symbol="101S03", side="buy", order_type="limit", quantity=Decimal(1),
                  limit_price=Decimal("400.00"), exchange="XKFE", session="night",
                  derivative_item="01", client_order_id="c")
    assert order.session == "night"
    assert order.exchange == "XKFE"

"""사전 리스크 한도(오주문 방지) -- KISClient(risk=RiskLimits(...)) 로 buy/sell 을 게이트.

KIS 가 서버에서 막지 않는 fat-finger(과대 수량/금액, 현재가 대비 % 이탈, 호가단위)를 와이어에
닿기 전에 잡는지, 그리고 참조가가 필요한 검사(collar/시장가 notional)가 시세를 조회하고 조회
실패 시 fail-closed 로 주문을 멈추는지, 리스크 거부가 client_order_id 를 소비하지 않는지를
네트워크 없이 가짜 전송으로 검증한다. 국내주식 정수 수량은 리스크 설정과 무관하게 항상 강제한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import ExecutionReport, KISClient, Order, RiskLimits
from kis_openapi.errors import KISError, KISUsageError, PreTradeRiskError
from kis_openapi.transport import RawResponse

_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
_QUOTE = "/uapi/domestic-stock/v1/quotations/inquire-price"

_QUOTE_OUTPUT = {
    "stck_prpr": "71500", "stck_oprc": "70800", "stck_hgpr": "71800", "stck_lwpr": "70600",
    "stck_sdpr": "70900", "prdy_vrss": "600", "prdy_vrss_sign": "2", "prdy_ctrt": "0.85",
    "acml_vol": "12345678", "w52_hgpr": "88000", "w52_lwpr": "49900",
}
_CURRENT_PRICE = Decimal(71500)   # stck_prpr -- collar/시장가 notional 참조가

_ACCEPTED = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                        body={"output": {"ODNO": "0000117057", "ORD_TMD": "121052"}})
_QUOTE_OK = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": dict(_QUOTE_OUTPUT)})
_QUOTE_FAIL = RawResponse(rt_cd="1", msg_cd="MCA05918", msg1="종목코드 오류", body={})


class FakeTransport:
    """경로별 순차 응답(by_path)과 기본 응답을 주는 가짜 전송. 모든 호출을 기록."""

    def __init__(self, *, response=None, by_path=None):
        self.response = response
        self.by_path = by_path or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"method": method, "path": path, "tr_id": tr_id, "body": body})
        if path in self.by_path:
            outcome = self.by_path[path]
            if isinstance(outcome, list):
                outcome = outcome.pop(0)
            return outcome
        assert self.response is not None, "FakeTransport 에 응답을 줘야 한다"
        return self.response


def _client(transport, *, risk=None):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     environment="real", transport=transport, risk=risk)


def _paths(fake):
    return [call["path"] for call in fake.calls]


# --- RiskLimits 자체 검증 --------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [{"max_order_quantity": 0}, {"max_order_quantity": -5}, {"max_order_notional": 0},
     {"max_order_notional": "-1"}, {"price_collar_percent": 0}, {"price_collar_percent": -1}],
)
def test_risk_limits_rejects_non_positive_config(kwargs):
    with pytest.raises(KISUsageError):
        RiskLimits(**kwargs)


@pytest.mark.parametrize("kwargs", [{"max_order_notional": "abc"}, {"price_collar_percent": "x%"}])
def test_risk_limits_rejects_nonnumeric_config(kwargs):
    with pytest.raises(KISUsageError):
        RiskLimits(**kwargs)


def test_risk_limits_coerces_decimal():
    limits = RiskLimits(max_order_notional="1000000", price_collar_percent=10)
    assert limits.max_order_notional == Decimal(1_000_000)
    assert limits.price_collar_percent == Decimal(10)


@pytest.mark.parametrize(
    "kwargs",
    [{"max_order_notional": "inf"}, {"max_order_notional": "nan"},
     {"max_order_notional": Decimal("Infinity")}, {"max_order_notional": Decimal("NaN")},
     {"price_collar_percent": "inf"}, {"price_collar_percent": "nan"},
     {"price_collar_percent": Decimal("-Infinity")}],
)
def test_risk_limits_rejects_non_finite_config(kwargs):
    # 무한/NaN 한도는 그 한도가 켜는 검사 자체를 무력화한다 -- fail-closed 로 거부해야 한다.
    with pytest.raises(KISUsageError):
        RiskLimits(**kwargs)


def test_risk_limits_rejects_bool_quantity():
    # bool 은 int 서브클래스라 True 가 수량 1 로 새어 들면 안 된다.
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_quantity=True)
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_quantity=False)


def test_risk_limits_accepts_genuine_int_quantity():
    # 진짜 정수 수량은 그대로 통과(parity).
    assert RiskLimits(max_order_quantity=100).max_order_quantity == 100


# --- 수량 한도 -------------------------------------------------------------
def test_quantity_cap_blocks_before_wire():
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=101, price=70000)
    assert fake.calls == []                              # 와이어에 닿기 전 차단


def test_quantity_within_cap_passes():
    fake = FakeTransport(response=_ACCEPTED)
    report = _client(fake, risk=RiskLimits(max_order_quantity=100)).domestic.stock("005930").buy(quantity=100, price=70000)
    assert isinstance(report, ExecutionReport)
    assert _paths(fake) == [_ORDER_CASH]                # 참조 조회 없이 바로 전송


# --- 금액(notional) 한도 ---------------------------------------------------
def test_notional_cap_on_limit_uses_own_price_no_quote():
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=20, price=70000)   # 20 x 70000 = 1.4M > 1M
    assert fake.calls == []                              # 지정가는 자체 단가로 계산 -- 시세 조회 안 함


def test_notional_cap_on_limit_within_passes():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, risk=RiskLimits(max_order_notional=1_000_000)).domestic.stock("005930").buy(quantity=10, price=70000)
    assert _paths(fake) == [_ORDER_CASH]                # 700k <= 1M, 시세 조회 없이 전송


def test_notional_cap_on_market_fetches_reference_and_blocks():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=20)           # 시장가: 71500 x 20 = 1.43M > 1M
    assert _paths(fake) == [_QUOTE]                      # 참조가 조회 후 거부, 주문 미전송


def test_notional_cap_on_market_within_passes():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    _client(fake, risk=RiskLimits(max_order_notional=1_000_000)).domestic.stock("005930").buy(quantity=10)
    assert _paths(fake) == [_QUOTE, _ORDER_CASH]        # 715k <= 1M -> 참조 조회 후 전송


# --- 가격 collar (현재가 대비 % 이탈) --------------------------------------
def test_collar_blocks_far_limit_price():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=10, price=90000)   # 현재가 71500 대비 +25.9% > 10%
    assert _paths(fake) == [_QUOTE]                      # 조회 후 거부, 미전송


def test_collar_allows_price_within_band():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    _client(fake, risk=RiskLimits(price_collar_percent=10)).domestic.stock("005930").buy(quantity=10, price=75000)
    assert _paths(fake) == [_QUOTE, _ORDER_CASH]        # +4.9% <= 10% -> 전송


def test_collar_does_not_apply_to_market_order():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, risk=RiskLimits(price_collar_percent=10)).domestic.stock("005930").buy(quantity=10)
    assert _paths(fake) == [_ORDER_CASH]                # 시장가는 지정 가격이 없어 collar 대상 아님(조회도 안 함)


def test_collar_fails_closed_when_quote_unavailable():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_FAIL], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10))
    with pytest.raises(KISError):                        # 참조 시세 조회 실패 -> 한도 확인 불가 -> 주문 중단
        kis.domestic.stock("005930").buy(quantity=10, price=72000)
    assert _paths(fake) == [_QUOTE]                      # 시세 1회만 조회하고 주문은 나가지 않는다


# --- 호가단위(tick) -- opt-in ---------------------------------------------
def test_tick_size_blocks_misaligned_price():
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(enforce_tick_size=True))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=10, price=70050)   # 5만~20만 구간 호가단위 100, 70050 은 위반
    assert fake.calls == []


def test_tick_size_allows_aligned_price():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake, risk=RiskLimits(enforce_tick_size=True)).domestic.stock("005930").buy(quantity=10, price=70000)
    assert _paths(fake) == [_ORDER_CASH]                # 70000 은 호가단위 100 의 배수 -- 통과


@pytest.mark.parametrize(
    ("price", "expected_tick_ok"),
    [(1999, True), (2001, False), (2005, True), (4990, True), (19990, True),
     (20050, True), (20010, False), (150000, True), (150050, False), (500500, False), (501000, True),
     # 정확한 구간 경계값(하한은 포함, 새 호가단위의 배수라 통과) + 바로 위 위반값
     (2000, True), (5000, True), (20000, True), (50000, True), (200000, True), (500000, True),
     (5005, False), (50050, False), (200100, False)],
)
def test_tick_size_table_boundaries(price, expected_tick_ok):
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(enforce_tick_size=True))
    if expected_tick_ok:
        kis.domestic.stock("005930").buy(quantity=10, price=price)
        assert _paths(fake) == [_ORDER_CASH]
    else:
        with pytest.raises(PreTradeRiskError):
            kis.domestic.stock("005930").buy(quantity=10, price=price)
        assert fake.calls == []


# --- 정수 수량(구조적, 항상 ON) -------------------------------------------
def test_fractional_quantity_rejected_without_risk_config():
    fake = FakeTransport(response=_ACCEPTED)
    with pytest.raises(KISUsageError):                  # 리스크 설정 없이도 소수 수량은 거부
        _client(fake).domestic.stock("005930").buy(quantity=Decimal("10.5"), price=70000)
    assert fake.calls == []


def test_whole_quantity_passes_without_risk_config():
    fake = FakeTransport(response=_ACCEPTED)
    _client(fake).domestic.stock("005930").buy(quantity=Decimal("10.0"), price=70000)
    assert _paths(fake) == [_ORDER_CASH]               # 10.0 은 정수 -- 통과


# --- 매도측도 동일하게 게이트된다 -----------------------------------------
def test_sell_quantity_over_cap_is_rejected_before_wire():
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").sell(quantity=101, price=70000)
    assert fake.calls == []


# --- 경계값(등호는 통과: 초과일 때만 거부) ---------------------------------
def test_collar_allows_limit_price_at_exact_boundary():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    # 현재가 71500 의 +10% = 78650 -- 정확히 collar 경계, 초과 아님 -> 통과.
    _client(fake, risk=RiskLimits(price_collar_percent=10)).domestic.stock("005930").buy(quantity=10, price=78650)
    assert _paths(fake) == [_QUOTE, _ORDER_CASH]


def test_limit_notional_equal_to_cap_passes_without_quote():
    fake = FakeTransport(response=_ACCEPTED)
    # 10 x 100000 = 1_000_000 == 한도 -- 초과 아님 -> 통과(지정가라 시세 조회도 없음).
    _client(fake, risk=RiskLimits(max_order_notional=1_000_000)).domestic.stock("005930").buy(quantity=10, price=100000)
    assert _paths(fake) == [_ORDER_CASH]


# --- 시장가 notional 은 참조가에 의존 -> 실패/0 이면 fail-closed -----------
def test_market_notional_cap_fails_closed_when_quote_unavailable():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_FAIL], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000))
    with pytest.raises(KISError):                       # 참조 조회 실패 -> 한도 확인 불가 -> 중단
        kis.domestic.stock("005930").buy(quantity=20)
    assert _paths(fake) == [_QUOTE]                     # 시세만 시도, 주문 미전송


def test_market_notional_cap_fails_closed_when_reference_is_zero():
    # 거래정지/무거래 종목은 현재가 stck_prpr="0" 로 온다(조회는 성공). 0을 유효가로 오인해
    # notional=0 으로 통과시키면 캡이 뚫린다 -> fail-closed 로 거부해야 한다.
    quote_zero = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                             body={"output": dict(_QUOTE_OUTPUT, stck_prpr="0")})
    fake = FakeTransport(by_path={_QUOTE: [quote_zero], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=20)
    assert _paths(fake) == [_QUOTE]


# --- 여러 한도 동시 -- 통과 시 시세는 한 번만 -----------------------------
def test_combined_limits_fetch_one_quote_and_submit_when_all_pass():
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    risk = RiskLimits(max_order_quantity=100, max_order_notional=10_000_000,
                      price_collar_percent=10, enforce_tick_size=True)
    _client(fake, risk=risk).domestic.stock("005930").buy(quantity=10, price=70000)
    assert _paths(fake) == [_QUOTE, _ORDER_CASH]        # collar 참조 1회만, 나머지는 무-조회 검사


# --- 리스크 거부는 client_order_id 를 소비하지 않는다 ----------------------
def test_risk_rejection_does_not_consume_client_order_id():
    fake = FakeTransport(response=_ACCEPTED)
    kis = _client(fake, risk=RiskLimits(max_order_quantity=5))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=10, price=70000, client_order_id="ID-1")
    # 리스크로 막힌 주문은 전송되지 않았으니 같은 id 로 정상 주문을 다시 낼 수 있다.
    report = kis.domestic.stock("005930").buy(quantity=3, price=70000, client_order_id="ID-1")
    assert report.order_id == "0000117057"
    assert _paths(fake) == [_ORDER_CASH]


def test_quote_dependent_rejection_does_not_consume_client_order_id():
    # 참조 조회를 거친 뒤(collar) 거부돼도 id 는 소비되지 않아야 한다 -- 즉시 거부와 동일 불변식.
    fake = FakeTransport(by_path={_QUOTE: [_QUOTE_OK, _QUOTE_OK], _ORDER_CASH: [_ACCEPTED]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=10, price=90000, client_order_id="ID-1")   # +25.9% 거부
    report = kis.domestic.stock("005930").buy(quantity=10, price=75000, client_order_id="ID-1")  # +4.9% 통과
    assert report.order_id == "0000117057"
    assert _paths(fake) == [_QUOTE, _QUOTE, _ORDER_CASH]


# --- 스탑/스탑지정가 분기(ticker 로는 못 만들어 RiskLimits.check 를 직접 검증) ---
def test_stop_notional_uses_stop_price_without_quote():
    order = Order.stop("005930", side="buy", quantity=10, stop_price=200000)   # 2M > 1M
    limits = RiskLimits(max_order_notional=1_000_000)
    assert limits._needs_reference_price(order) is False   # 자체 가격(stop_price) 있음 -> 조회 불필요
    with pytest.raises(PreTradeRiskError):
        limits.check(order)


def test_stop_limit_notional_prefers_limit_price_without_quote():
    order = Order.stop_limit("005930", side="buy", quantity=10, limit_price=200000, stop_price=190000)
    limits = RiskLimits(max_order_notional=1_000_000)
    assert limits._needs_reference_price(order) is False
    with pytest.raises(PreTradeRiskError):                 # limit_price 200000 x 10 = 2M > 1M
        limits.check(order)


def test_tick_check_catches_misaligned_stop_price():
    order = Order.stop_limit("005930", side="buy", quantity=10, limit_price=70000, stop_price=70050)
    with pytest.raises(PreTradeRiskError):                 # limit 은 정렬, stop 70050 은 호가단위 100 위반
        RiskLimits(enforce_tick_size=True).check(order)

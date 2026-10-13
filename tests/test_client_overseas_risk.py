"""해외주식 사전 리스크 한도 -- RiskLimits 통화 인지 확장(스펙 C).

해외주식 주문도 국내와 같은 fat-finger 게이트(수량·금액·collar)를 받는지, 금액 한도가 결제통화별
맵으로 집행되고 그 통화의 한도가 없으면 fail-closed 로 거부되는지(STRICT), 거래소->통화 판별과
참조가 조회가 거래소별 해외 현재가로 이뤄지는지, 국내 경로가 완전히 불변인지를 네트워크 없이 가짜
전송으로 검증한다. 채권·해외선물옵션은 risk 세션에서 발주/정정 모두 여전히 거부된다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import ExecutionReport, KISClient, Order, RiskLimits
from kis_trader.errors import KISError, KISUsageError, PreTradeRiskError
from kis_trader.overseas._engine._parse import _MARKETS
from kis_trader.overseas._engine.orders import (
    _ORDER_EXCHANGE,
    _RISK_CURRENCY_BY_MARKET,
    resolve_risk_currency,
)
from kis_trader.store import OrderStore
from kis_trader.transport import RawResponse

_ORDER = "/uapi/overseas-stock/v1/trading/order"
_ORDER_CHANGE = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_CURRENT_PRICE = "/uapi/overseas-price/v1/quotations/price"
_BOND_BUY = "/uapi/domestic-bond/v1/trading/buy"


def _cp_output(last: str = "100") -> dict:
    # fetch_current_price 가 required_decimal/required_int 로 읽는 필드를 모두 채운다.
    return {"last": last, "base": last, "diff": "0", "rate": "0", "sign": "3",
            "pvol": "1000", "tvol": "1000", "tamt": "100000", "zdiv": "4", "ordy": "매수가능"}


def _cp(last: str = "100", rt_cd: str = "0") -> RawResponse:
    body = {"output": _cp_output(last)} if rt_cd == "0" else {}
    return RawResponse(rt_cd=rt_cd, msg_cd="0", msg1="정상" if rt_cd == "0" else "종목코드 오류", body=body)


def _ack(odno: str = "0000123456") -> RawResponse:
    return RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": odno,
                                        "ORD_TMD": "093015"}})


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


def _client(transport, *, risk=None, store=None, account="12345678-01"):
    return KISClient(app_key="k", app_secret="s", account=account,
                     environment="real", transport=transport, risk=risk, store=store)


def _paths(fake):
    return [call["path"] for call in fake.calls]


def _overseas(order_type="limit", *, quantity, limit_price=None, exchange="NAS", side="buy"):
    return Order(symbol="AAPL", side=side, order_type=order_type, quantity=Decimal(quantity),
                 limit_price=None if limit_price is None else Decimal(limit_price),
                 region="overseas", asset_class="stock", exchange=exchange)


# --- 거래소 -> 통화 resolver (T-U1/U2/U3) ----------------------------------
@pytest.mark.parametrize(
    ("exchange", "currency"),
    [("NAS", "USD"), ("NYS", "USD"), ("AMS", "USD"), ("HKS", "HKD"), ("SHS", "CNY"),
     ("SZS", "CNY"), ("TSE", "JPY"), ("HNX", "VND"), ("HSX", "VND")],
)
def test_resolve_risk_currency_every_exchange(exchange, currency):  # T-U1
    assert resolve_risk_currency(exchange) == currency


def test_market_currency_covers_every_order_exchange_group():  # T-U2 (완전성)
    # 주문 가능한 모든 거래소의 시장 그룹은 반드시 통화를 가진다 -- 새 거래소가 통화 없이 들어오면
    # 여기서 실패해 PR 시점에 잡힌다(런타임 KeyError 로 새지 않는다).
    groups = {market for _, market in _ORDER_EXCHANGE.values()}
    assert groups <= set(_RISK_CURRENCY_BY_MARKET), groups - set(_RISK_CURRENCY_BY_MARKET)
    for exchange in _ORDER_EXCHANGE:
        assert resolve_risk_currency(exchange) is not None


def test_resolve_risk_currency_unknown_is_none():  # T-U3
    assert resolve_risk_currency("ZZZ") is None
    assert resolve_risk_currency("") is None


#: 리스크 시장 그룹(_RISK_CURRENCY_BY_MARKET 의 키) <-> 권한 소스인 _parse._MARKETS 의 시장명 브리지.
#: 두 계층이 시장명 어휘가 달라(그룹 SH vs 시장명 CN_SH, 그룹 VN vs CN/HCM 분할) 통화 값 비교 전에
#: 이름을 연결한다.
_GROUP_TO_PARSE_MARKETS = {
    "US": ["US"], "HK": ["HK"], "SH": ["CN_SH"], "SZ": ["CN_SZ"],
    "JP": ["JP"], "VN": ["VN_HN", "VN_HCM"],
}


def test_market_currency_values_agree_with_parse_markets():  # T-U5 (값 일치)
    # T-U2 가 그룹 완전성을 고정한다면, 여기선 통화 *값* 이 권한 소스(_parse._MARKETS 의 TR_CRCY_CD)와
    # 어긋나지 않게 고정한다 -- 리스크 맵과 파서 계층 사이에서 통화가 drift 하면 PR 시점에 잡힌다.
    assert set(_GROUP_TO_PARSE_MARKETS) == set(_RISK_CURRENCY_BY_MARKET)   # 브리지가 전 그룹 커버
    for group, markets in _GROUP_TO_PARSE_MARKETS.items():
        for market in markets:
            assert _MARKETS[market][1] == _RISK_CURRENCY_BY_MARKET[group], (group, market)


# --- RiskLimits 통화맵 생성 검증 -------------------------------------------
def test_by_currency_normalizes_keys_and_values():
    limits = RiskLimits(max_order_notional_by_currency={" usd ": "10000", "JPY": 1_500_000})
    assert limits.max_order_notional_by_currency == {"USD": Decimal(10000), "JPY": Decimal(1_500_000)}
    assert isinstance(limits.max_order_notional_by_currency["USD"], Decimal)


def test_by_currency_is_read_only_after_construction():
    limits = RiskLimits(max_order_notional_by_currency={"USD": 10000})
    with pytest.raises(TypeError):                      # MappingProxyType -- 사후 변이 불가
        limits.max_order_notional_by_currency["USD"] = Decimal(1)  # type: ignore[index]


def test_by_currency_rejects_empty_map():
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_notional_by_currency={})


def test_by_currency_rejects_duplicate_after_normalize():
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_notional_by_currency={"usd": 1, "USD": 2})


@pytest.mark.parametrize("bad_key", [{"   ": 100}, {1: 100}, {None: 100}])
def test_by_currency_rejects_blank_or_nonstring_key(bad_key):
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_notional_by_currency=bad_key)


@pytest.mark.parametrize("bad_value", [0, -5, "inf", "nan", Decimal("Infinity"), Decimal("NaN")])
def test_by_currency_rejects_non_positive_or_non_finite_value(bad_value):
    with pytest.raises(KISUsageError):
        RiskLimits(max_order_notional_by_currency={"USD": bad_value})


def test_risklimits_with_currency_map_is_hashable_and_eq_hash_consistent():
    # MappingProxyType 캡 필드가 frozen dataclass 자동 해시를 깨뜨리던 회귀 -- 명시 __hash__ 로 복원.
    # 내용이 같은 두 한도는 == 이면서 해시도 같아야 한다(hash/eq 불변식). 키 입력 순서는 무관.
    a = RiskLimits(max_order_notional_by_currency={"USD": 10_000, "JPY": 1_500_000})
    b = RiskLimits(max_order_notional_by_currency={"JPY": 1_500_000, "USD": 10_000})
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1                             # set 에 넣을 수 있다(해시 가능)
    assert isinstance(hash(RiskLimits(max_order_quantity=5)), int)   # 맵 없는 한도도 여전히 해시 가능


# --- RiskLimits.check() 해외 직접 검증 -------------------------------------
def test_check_overseas_within_currency_cap_passes():
    order = _overseas(quantity=10, limit_price=150)     # 1500
    RiskLimits(max_order_notional_by_currency={"USD": 10_000}).check(order, currency="USD")


def test_check_overseas_over_currency_cap_refuses():
    order = _overseas(quantity=100, limit_price=150)    # 15000 > 10000
    with pytest.raises(PreTradeRiskError):
        RiskLimits(max_order_notional_by_currency={"USD": 10_000}).check(order, currency="USD")


def test_check_overseas_currency_none_refuses():
    order = _overseas(quantity=10, limit_price=150)
    with pytest.raises(PreTradeRiskError):
        RiskLimits(max_order_notional_by_currency={"USD": 10_000}).check(order, currency=None)


@pytest.mark.parametrize("limits", [RiskLimits(max_order_quantity=100),
                                    RiskLimits(price_collar_percent=10)])
def test_check_overseas_without_currency_map_refuses(limits):
    # STRICT: 통화별 금액 한도가 없는 risk 세션(수량만·collar만)은 해외 주문을 거부한다.
    order = _overseas(quantity=10, limit_price=150)
    with pytest.raises(PreTradeRiskError, match="통화별 금액 한도"):
        limits.check(order, reference_price=Decimal(150), currency="USD")


def test_check_overseas_currency_not_in_map_names_it():
    order = _overseas(quantity=10, limit_price=150)
    with pytest.raises(PreTradeRiskError, match="JPY"):
        RiskLimits(max_order_notional_by_currency={"USD": 10_000}).check(order, currency="JPY")


def test_check_overseas_ignores_krw_max_order_notional():  # T-U4
    # KRW max_order_notional(1)은 해외에 적용되지 않는다 -- 적용됐다면 1500 이 거부됐을 것.
    order = _overseas(quantity=10, limit_price=150)       # 1500
    RiskLimits(max_order_notional=1, max_order_notional_by_currency={"USD": 10_000}).check(
        order, currency="USD")


@pytest.mark.parametrize("reference", [None, Decimal(0)])
def test_check_overseas_market_without_valid_reference_refuses(reference):
    order = _overseas("market", quantity=10)             # 자체 가격 없음 -> 참조가 필요
    with pytest.raises(PreTradeRiskError):
        RiskLimits(max_order_notional_by_currency={"USD": 10_000}).check(
            order, reference_price=reference, currency="USD")


# --- needs_reference_price 매트릭스 ----------------------------------------
def test_needs_reference_price_matrix():
    collar = RiskLimits(price_collar_percent=10, max_order_notional_by_currency={"USD": 1})
    assert collar.needs_reference_price(_overseas(quantity=1, limit_price=150)) is True   # 지정가+collar
    no_collar = RiskLimits(max_order_notional_by_currency={"USD": 1})
    assert no_collar.needs_reference_price(_overseas(quantity=1, limit_price=150)) is False
    # 해외 시장가: by_currency 있으면 notional 계산에 참조가 필요, 없으면 불필요(방어적 분기).
    assert no_collar.needs_reference_price(_overseas("market", quantity=1)) is True
    assert RiskLimits(max_order_quantity=5).needs_reference_price(_overseas("market", quantity=1)) is False


def test_needs_reference_price_domestic_unchanged():
    # 국내 분기는 등가 보존 -- 지정가+collar True, 지정가 no-collar+notional False.
    assert RiskLimits(price_collar_percent=10).needs_reference_price(
        Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
              limit_price=Decimal(70000))) is True
    assert RiskLimits(max_order_notional=1_000_000).needs_reference_price(
        Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
              limit_price=Decimal(70000))) is False


# --- 통합: 해외 발주 게이트 (T-O1..T-O14) ----------------------------------
def test_o1_quantity_over_cap_refuses_before_wire():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=101, limit_price=100)
    assert fake.calls == []


def test_o2_quantity_within_cap_submits_without_fetch():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    report = kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=100, limit_price=100)
    assert isinstance(report, ExecutionReport)
    assert _paths(fake) == [_ORDER]


def test_o3_usd_notional_over_cap_refuses_without_fetch():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=100, limit_price=150)  # 15000 > 10000
    assert fake.calls == []                              # 지정가 자체 단가 -- 시세 조회 없음


def test_o4_usd_notional_within_cap_submits_without_fetch():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=50, limit_price=150)       # 7500 <= 10000
    assert _paths(fake) == [_ORDER]


def test_o5_usd_only_map_refuses_jpy_order_no_calls():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(PreTradeRiskError, match="JPY"):
        kis.overseas.stock("7203", exchange="TSE").buy(quantity=1, limit_price=1000)
    assert fake.calls == []


def test_o6_quantity_only_risk_refuses_overseas_strict():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100))
    with pytest.raises(PreTradeRiskError, match="통화별 금액 한도"):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=150)
    assert fake.calls == []


def test_o7_collar_over_band_refuses_after_one_fetch():
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(last="100")], _ORDER: [_ack()]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=150)  # +50% > 10%
    assert _paths(fake) == [_CURRENT_PRICE]


def test_o8_collar_within_band_fetches_then_submits():
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(last="100")], _ORDER: [_ack()]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=105)      # +5% <= 10%
    assert _paths(fake) == [_CURRENT_PRICE, _ORDER]


def test_o9_collar_quote_error_fails_closed():
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(rt_cd="1")], _ORDER: [_ack()]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(KISError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=105)
    assert _paths(fake) == [_CURRENT_PRICE]              # 시세만 시도, 주문 미전송


def test_o10_collar_zero_reference_fails_closed():
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(last="0")], _ORDER: [_ack()]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=105)
    assert _paths(fake) == [_CURRENT_PRICE]


def test_o11_unknown_exchange_refuses_before_any_call():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(KISUsageError):
        kis.overseas.stock("AAPL", exchange="ZZZ").buy(quantity=1, limit_price=150)
    assert fake.calls == []


def test_o12_risk_refusal_does_not_consume_client_order_id():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=100, limit_price=150, client_order_id="ID-1")
    report = kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=10, limit_price=150, client_order_id="ID-1")
    assert report.order_id == "0000123456"
    assert _paths(fake) == [_ORDER]


def test_o13_all_limits_pass_fetch_once_then_submit():
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(last="100")], _ORDER: [_ack()]})
    risk = RiskLimits(max_order_quantity=100, price_collar_percent=10,
                      max_order_notional_by_currency={"USD": 10_000_000})
    kis = _client(fake, risk=risk)
    kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=10, limit_price=105)
    assert _paths(fake) == [_CURRENT_PRICE, _ORDER]


def test_o14_sell_quantity_over_cap_refuses_before_wire():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_quantity=100,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("AAPL", exchange="NAS").sell(quantity=101, limit_price=100)
    assert fake.calls == []


def test_o15_collar_only_no_currency_cap_refuses_without_fetch():
    # collar 만 켜고 통화별 금액 한도가 없으면 해외 주문은 어차피 금액 한도 단계에서 거부된다 --
    # 스로틀되는 현재가 조회를 낭비하지 않고 조회 전에 같은 거부를 올린다(calls==[], 시세 미조회).
    fake = FakeTransport(by_path={_CURRENT_PRICE: [_cp(last="100")], _ORDER: [_ack()]})
    kis = _client(fake, risk=RiskLimits(price_collar_percent=10))
    with pytest.raises(PreTradeRiskError, match="통화별 금액 한도"):
        kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=105)
    assert fake.calls == []
    assert _CURRENT_PRICE not in _paths(fake)


# --- 정정(modify)/취소(cancel) (T-M-OS1/OS2) -------------------------------
def test_m_os1_modify_beyond_cap_refused_via_overseas_runner():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=3, limit_price=150, client_order_id="m1")  # 450
    with pytest.raises(PreTradeRiskError):
        kis.orders.modify("m1", limit_price=5000, request_id="r1")                     # 3 x 5000 = 15000 > 10000
    assert _paths(fake) == [_ORDER]                     # 정정 와이어는 안 나갔다(발주 1건뿐)


def test_m_os2_cancel_is_not_gated():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 100_000}))
    kis.overseas.stock("AAPL", exchange="NAS").buy(quantity=1, limit_price=150, client_order_id="c1")
    report = kis.orders.cancel("c1", request_id="r1")   # 취소는 익스포저 감소 -- 게이트 안 함
    assert isinstance(report, ExecutionReport)
    assert _paths(fake) == [_ORDER, _ORDER_CHANGE]


# --- 여전히 거부: 채권·해외선물옵션 (T-R1/T-R2) ----------------------------
def test_r1_bond_place_with_risk_still_refused():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}),
                  account="81012345-03")
    with pytest.raises(KISUsageError):
        kis.domestic.bond("KR2033022D33").buy(quantity=10, limit_price=10000)
    assert fake.calls == []


def test_r2_overseas_derivative_place_with_risk_still_refused():
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(KISUsageError):
        kis.overseas.futures("6BZ22").buy(quantity=1, limit_price="1.17")
    assert fake.calls == []


def test_r1_bond_modify_with_risk_still_refused_via_shared_store():
    # risk 없는 세션이 낸 채권 주문을 risk 세션이 공유 store 로 정정하려 해도 거부돼야 한다
    # (place 와 동일하게 fail-closed -- else 분기가 채권·해외선물옵션만 좁게 거부).
    bond_ack = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                           body={"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0001234567",
                                            "ORD_TMD": "101530"}})
    store = OrderStore()
    placer = FakeTransport(response=bond_ack)
    _client(placer, store=store, account="81012345-03").domestic.bond("KR2033022D33").buy(
        quantity=10, limit_price=10000, client_order_id="b1")
    assert _paths(placer) == [_BOND_BUY]
    modifier_tx = FakeTransport(response=bond_ack)
    modifier = _client(modifier_tx, risk=RiskLimits(max_order_notional_by_currency={"USD": 10_000}),
                       store=store, account="81012345-03")
    with pytest.raises(KISUsageError, match="채권·해외선물옵션"):
        modifier.orders.modify("b1", limit_price=9900, request_id="r1")
    assert modifier_tx.calls == []                      # 정정 와이어는 안 나갔다


# --- 국내 회귀 (T-D2/T-D3) -------------------------------------------------
_DOM_ORDER = "/uapi/domestic-stock/v1/trading/order-cash"
_DOM_QUOTE = "/uapi/domestic-stock/v1/quotations/inquire-price"
_DOM_QUOTE_OK = RawResponse(
    rt_cd="0", msg_cd="MCA00000", msg1="정상",
    body={"output": {"stck_prpr": "71500"}},
)
_DOM_ACK = RawResponse(rt_cd="0", msg_cd="APBK0013", msg1="주문 전송 완료",
                       body={"output": {"ODNO": "0000117057", "ORD_TMD": "121052"}})


def test_d2_domestic_unaffected_by_currency_map_and_no_overseas_fetch():
    # by_currency 를 켜도 국내 경로는 전혀 영향 없다 -- KRW notional 만 보고, 해외 현재가 조회도 없다.
    fake = FakeTransport(by_path={_DOM_QUOTE: [_DOM_QUOTE_OK], _DOM_ORDER: [_DOM_ACK]})
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000,
                                        max_order_notional_by_currency={"USD": 1}))
    kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)   # 700k <= 1M, 지정가라 조회 없음
    assert _paths(fake) == [_DOM_ORDER]                 # 해외 현재가(_CURRENT_PRICE)는 호출되지 않는다
    assert _CURRENT_PRICE not in _paths(fake)


def test_d2_domestic_krw_notional_over_cap_still_refuses():
    fake = FakeTransport(response=_DOM_ACK)
    kis = _client(fake, risk=RiskLimits(max_order_notional=1_000_000,
                                        max_order_notional_by_currency={"USD": 10_000_000}))
    with pytest.raises(PreTradeRiskError):
        kis.domestic.stock("005930").buy(quantity=20, limit_price=70000)   # 1.4M > 1M
    assert fake.calls == []


def test_d3_domestic_derivative_quantity_only_passes_with_new_field_present():
    # 파생은 max_order_quantity 만 허용 -- 새 필드가 "존재"만 해도(unset) allowlist 가 트립하지 않는다.
    fake = FakeTransport(response=_DOM_ACK)
    kis = _client(fake, risk=RiskLimits(max_order_quantity=10))
    report = kis.domestic.futures("101W09").buy(quantity=1, limit_price="340.50")
    assert isinstance(report, ExecutionReport)


def test_d3_domestic_derivative_with_by_currency_refused_by_allowlist():
    # 파생에 통화맵을 켜면 allowlist 가 그 필드명을 들어 거부한다(파생은 수량만 지원).
    fake = FakeTransport(response=_DOM_ACK)
    kis = _client(fake, risk=RiskLimits(max_order_quantity=10,
                                        max_order_notional_by_currency={"USD": 10_000}))
    with pytest.raises(KISUsageError, match="max_order_notional_by_currency"):
        kis.domestic.futures("101W09").buy(quantity=1, limit_price="340.50")
    assert fake.calls == []


# --- T1: 공개 주문 경로에서 거래소->통화 매트릭스(전 시장) ----------------------
# 각 케이스는 그 거래소의 **매칭 통화에만** 캡을 둔다 -- 미스매핑(예: SHS->JPY)이면 under-cap 제출이
# 깨져(order 는 CNY 로 검사되는데 캡이 JPY 에만 있어 거부) 테스트가 실패한다.
_EXCHANGE_CURRENCY_MATRIX = [
    ("NAS", "USD"), ("NYS", "USD"), ("AMS", "USD"), ("HKS", "HKD"),
    ("SHS", "CNY"), ("SZS", "CNY"), ("TSE", "JPY"), ("HSX", "VND"), ("HNX", "VND"),
]


@pytest.mark.parametrize(("exchange", "currency"), _EXCHANGE_CURRENCY_MATRIX)
def test_t1_over_cap_refuses_zero_wire(exchange, currency):
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={currency: "1000"}))
    with pytest.raises(PreTradeRiskError):
        kis.overseas.stock("SYM", exchange=exchange).buy(quantity=100, limit_price="150")  # 15000 > 1000
    assert fake.calls == []                                 # 캡 초과 -- 와이어 0


@pytest.mark.parametrize(("exchange", "currency"), _EXCHANGE_CURRENCY_MATRIX)
def test_t1_under_cap_submits(exchange, currency):
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={currency: "1000"}))
    kis.overseas.stock("SYM", exchange=exchange).buy(quantity=1, limit_price="10")          # 10 <= 1000
    assert _paths(fake) == [_ORDER]                         # 매칭 통화 캡 아래 -- 제출(지정가라 시세 조회 없음)


# --- T4: 홍콩 USD-카운터 ~7.8x 알려진 한계(현재 동작 고정) ----------------------
def test_t4_hk_counter_checked_against_hkd_cap_known_loose():
    # HKS 주문은 종목의 실제 결제통화와 무관하게 HKD 로 검사된다(resolve_risk_currency 가 그룹 HK->HKD
    # 고정). 그래서 USD 결제 HK 카운터라도 USD 캡은 적용되지 않고, HKD 캡만 보인다 -- 1 USD~7.8 HKD 라
    # USD 로 치면 넘을 금액도 HKD 기준으론 통과할 수 있다(과소보호, 알려진 한계). 미래에 종목별 통화
    # 판별(시세상세 curr)이 들어오면 이 테스트를 의도적으로 바꾼다.
    assert resolve_risk_currency("HKS") == "HKD"
    # USD 캡만 있고 HKD 캡이 없으면 HK 주문은 HKD 로 검사되는데 캡이 없어 거부(USD 캡은 무시된다).
    fake = FakeTransport(response=_ack())
    kis = _client(fake, risk=RiskLimits(max_order_notional_by_currency={"USD": "10"}))
    with pytest.raises(PreTradeRiskError, match="HKD"):
        kis.overseas.stock("00700", exchange="HKS").buy(quantity=1, limit_price="100")
    assert fake.calls == []
    # HKD 캡을 넉넉히 두면 통과한다 -- USD 로 치면 작은 캡도 HKD 기준으론 느슨하게 통과(한계 고정).
    fake2 = FakeTransport(response=_ack())
    kis2 = _client(fake2, risk=RiskLimits(max_order_notional_by_currency={"HKD": "100000"}))
    kis2.overseas.stock("00700", exchange="HKS").buy(quantity=100, limit_price="100")   # 10000 HKD
    assert _paths(fake2) == [_ORDER]

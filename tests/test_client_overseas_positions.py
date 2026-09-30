"""해외 잔고 -- kis.account.overseas.positions(market=...).

inquire-balance 엔드포인트, 시장->OVRS_EXCG_CD/TR_CRCY_CD 매핑, 외화 금액을 Money(통화 포함)로,
CTX_AREA_FK200/NK200 연속조회, 실전/모의 TR, fail-closed 를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import (
    KISClient,
    Money,
    OverseasBalance,
    OverseasOpenOrder,
    OverseasPosition,
)
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse

_BALANCE = "/uapi/overseas-stock/v1/trading/inquire-balance"


def _holding(symbol="AAPL", name="APPLE", qty="10", sellable="10", avg="140.00", now="150.25",
             pchs="1400.00", evlu="1502.50", pnl="102.50", rate="7.32", crcy="USD"):
    return {"ovrs_pdno": symbol, "ovrs_item_name": name, "ovrs_cblc_qty": qty,
            "ord_psbl_qty": sellable, "pchs_avg_pric": avg, "now_pric2": now,
            "frcr_pchs_amt1": pchs, "ovrs_stck_evlu_amt": evlu, "frcr_evlu_pfls_amt": pnl,
            "evlu_pfls_rt": rate, "tr_crcy_cd": crcy}


def _resp(rows, *, nk="", fk="", tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": rows, "output2": {}, "ctx_area_nk200": nk,
                             "ctx_area_fk200": fk}, tr_cont=tr_cont)


class FakeTransport:
    def __init__(self, *, response=None, pages=None):
        self.response = response
        self.pages = pages
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
            if self.pages is not None:
                return self.pages.pop(0)
        return self.response


def _client(transport, *, environment="real"):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     transport=transport, environment=environment)


def test_overseas_positions_maps_money_and_params():
    fake = FakeTransport(response=_resp([_holding()]))
    positions = _client(fake).account.overseas.positions(market="US")
    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, OverseasPosition)
    assert pos.symbol == "AAPL"
    assert pos.exchange == "NASD"
    assert pos.quantity == 10
    assert pos.sellable_quantity == 10
    assert pos.average_purchase_price == Money(Decimal("140.00"), "USD")
    assert pos.current_price == Money(Decimal("150.25"), "USD")
    assert pos.market_value == Money(Decimal("1502.50"), "USD")
    assert pos.unrealized_pnl == Money(Decimal("102.50"), "USD")
    assert pos.unrealized_pnl_percent == Decimal("7.32")
    call = fake.calls[0]
    assert call["path"] == _BALANCE
    assert call["tr_id"] == "TTTS3012R"                # real
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"
    assert call["params"]["TR_CRCY_CD"] == "USD"


def test_overseas_positions_allow_fractional_shares():
    # 미국 미니스탁 등 소수점 보유수량 -- 정수 강제(required_int)면 0.x 주에서 throw 해 보유가 통째로
    # 사라졌다. Decimal 로 읽어 소수점을 보존한다(형제 OverseasBalancePosition 과 일관).
    fake = FakeTransport(response=_resp([_holding(qty="0.5", sellable="0.25")]))
    pos = _client(fake).account.overseas.positions(market="US")[0]
    assert pos.quantity == Decimal("0.5")
    assert pos.sellable_quantity == Decimal("0.25")


def test_overseas_positions_market_maps_exchange_and_currency():
    for market, excg, crcy in [("HK", "SEHK", "HKD"), ("JP", "TKSE", "JPY"),
                               ("CN_SH", "SHAA", "CNY"), ("VN_HCM", "VNSE", "VND")]:
        fake = FakeTransport(response=_resp([]))
        _client(fake).account.overseas.positions(market=market)
        assert fake.calls[0]["params"]["OVRS_EXCG_CD"] == excg
        assert fake.calls[0]["params"]["TR_CRCY_CD"] == crcy


def test_overseas_positions_demo_tr():
    fake = FakeTransport(response=_resp([]))
    _client(fake, environment="paper").account.overseas.positions(market="US")
    assert fake.calls[0]["tr_id"] == "VTTS3012R"


def test_overseas_positions_paginates_ctx_area():
    fake = FakeTransport(pages=[
        _resp([_holding(symbol="AAPL")], nk="NEXT", tr_cont="M"),
        _resp([_holding(symbol="MSFT")], nk=""),
    ])
    positions = _client(fake).account.overseas.positions(market="US")
    assert [p.symbol for p in positions] == ["AAPL", "MSFT"]
    assert fake.calls[1]["params"]["CTX_AREA_NK200"] == "NEXT"


def test_overseas_positions_rejects_bad_market():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).account.overseas.positions(market="XX")


def test_overseas_positions_non_list_output1_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": "oops"}))
    with pytest.raises(KISError):
        _client(fake).account.overseas.positions(market="US")


def test_overseas_positions_requires_account():
    fake = FakeTransport(response=_resp([]))
    client = KISClient(app_key="k", app_secret="s", transport=fake)  # 계좌 없음
    with pytest.raises(KISUsageError):
        client.account.overseas.positions(market="US")


def _summary():
    return {"frcr_pchs_amt1": "10000.00", "tot_evlu_pfls_amt": "502.50",
            "ovrs_rlzt_pfls_amt": "120.00", "ovrs_tot_pfls": "622.50", "tot_pftrt": "6.22"}


def _balance_resp(summary):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": [], "output2": summary, "ctx_area_nk200": ""})


def test_overseas_balance_maps_money_summary():
    fake = FakeTransport(response=_balance_resp(_summary()))
    bal = _client(fake).account.overseas.balance(market="US")
    assert isinstance(bal, OverseasBalance)
    assert bal.exchange == "NASD"
    assert bal.purchase_amount == Money(Decimal("10000.00"), "USD")
    assert bal.unrealized_pnl == Money(Decimal("502.50"), "USD")
    assert bal.realized_pnl == Money(Decimal("120.00"), "USD")
    assert bal.total_pnl == Money(Decimal("622.50"), "USD")
    assert bal.return_percent == Decimal("6.22")
    call = fake.calls[0]
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"
    assert call["params"]["TR_CRCY_CD"] == "USD"


def test_overseas_balance_currency_follows_market():
    fake = FakeTransport(response=_balance_resp(_summary()))
    bal = _client(fake).account.overseas.balance(market="JP")
    assert bal.purchase_amount.currency == "JPY"
    assert fake.calls[0]["params"]["OVRS_EXCG_CD"] == "TKSE"


def test_overseas_balance_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok",
                                              body={"output1": []}))
    with pytest.raises(KISError):
        _client(fake).account.overseas.balance(market="US")


def test_overseas_balance_all_markets_returns_list_per_market():
    # market 생략 -> 전 시장 순회(positions/open_orders 와 동일 arity). 통화가 시장마다 달라
    # 하나로 합칠 수 없으므로 시장별 요약을 list 로 준다(_MARKETS 순서).
    fake = FakeTransport(response=_balance_resp(_summary()))
    bals = _client(fake).account.overseas.balance()
    assert isinstance(bals, list)
    assert [b.exchange for b in bals] == ["NASD", "SEHK", "SHAA", "SZAA",
                                          "TKSE", "HASE", "VNSE"]
    assert [b.purchase_amount.currency for b in bals] == ["USD", "HKD", "CNY", "CNY",
                                                          "JPY", "VND", "VND"]
    assert len(fake.calls) == 7                          # 시장당 1콜


def test_overseas_balance_all_markets_requires_account():
    fake = FakeTransport(response=_balance_resp(_summary()))
    client = KISClient(app_key="k", app_secret="s", transport=fake)  # 계좌 없음
    with pytest.raises(KISUsageError):
        client.account.overseas.balance()


def _open_order(odno="0000123456", pdno="AAPL", side="02", qty="10", ccld="3", nccs="7",
                unpr="150.25", excg="NASD", crcy="USD"):
    return {"odno": odno, "pdno": pdno, "prdt_name": "APPLE", "sll_buy_dvsn_cd": side,
            "ft_ord_qty": qty, "ft_ccld_qty": ccld, "nccs_qty": nccs, "ft_ord_unpr3": unpr,
            "ovrs_excg_cd": excg, "tr_crcy_cd": crcy}


def _open_resp(rows, *, nk="", tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output": rows, "ctx_area_nk200": nk}, tr_cont=tr_cont)


def test_overseas_open_orders_maps_fields():
    fake = FakeTransport(response=_open_resp([_open_order()]))
    orders = _client(fake).account.overseas.open_orders(market="US")
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, OverseasOpenOrder)
    assert o.order_id == "0000123456"
    assert o.symbol == "AAPL"
    assert o.side == "buy"                             # 02 -> buy
    assert o.quantity == 10
    assert o.filled_quantity == 3
    assert o.unfilled_quantity == 7
    assert o.order_price == Money(Decimal("150.25"), "USD")
    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-stock/v1/trading/inquire-nccs"
    assert call["tr_id"] == "TTTS3018R"
    assert call["params"]["OVRS_EXCG_CD"] == "NASD"


def test_overseas_open_orders_demo_unsupported():
    fake = FakeTransport(response=_open_resp([]))
    with pytest.raises(KISUsageError, match="모의투자 미지원"):
        _client(fake, environment="paper").account.overseas.open_orders(market="US")


def test_overseas_open_orders_paginates():
    fake = FakeTransport(pages=[_open_resp([_open_order(odno="1")], nk="NEXT", tr_cont="M"),
                                _open_resp([_open_order(odno="2")], nk="")])
    orders = _client(fake).account.overseas.open_orders(market="US")
    assert [o.order_id for o in orders] == ["1", "2"]

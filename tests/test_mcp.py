"""kis_trader MCP 서버/핸들러 테스트.

핸들러(:mod:`kis_trader.mcp.handlers`)는 ``mcp`` 없이 테스트한다 -- 가짜 kis 로 공개 API 를 흉내내고,
직렬화가 ``_raw``/자격증명을 새지 않는지, 주문 미리보기가 전송하지 않고 계좌를 마스킹하는지 확인한다.
서버(:mod:`kis_trader.mcp.server`)는 ``mcp`` 가 있을 때만(importorskip) 도구 등록을 확인한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from kis_trader.account import StockAccount
from kis_trader.errors import KISUsageError
from kis_trader.mcp import handlers


@dataclass(frozen=True)
class _FakeQuote:
    symbol: str
    current_price: Decimal
    change_percent: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakeIndexQuote:
    code: str
    name: str
    value: Decimal
    change: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakeBalance:
    label: str
    total: Decimal
    _raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FakePosition:
    symbol: str
    quantity: int
    _raw: dict[str, Any] = field(default_factory=dict)


class _FakeStockAccount(StockAccount):
    """isinstance(.., StockAccount) 를 만족하는 가짜 주식 계좌 뷰 -- product_code 분기와 시장별 뷰
    (domestic/overseas/pension)를 제어한다(super().__init__ 건너뜀)."""

    def __init__(self, *, product_code="01", domestic=None, overseas=None, pension=None):
        self._product_code = product_code
        self._domestic_view = domestic
        self._overseas_view = overseas
        self._pension_view = pension

    @property
    def product_code(self):
        return self._product_code

    @property
    def orderable(self):
        # 실제 StockAccount.orderable 과 같은 상품-조회전용 판정(세션 킬스위치 없이): IRP(29)만 False.
        from kis_trader.account import _READ_ONLY_PRODUCT_CODES
        return self._product_code not in _READ_ONLY_PRODUCT_CODES

    @property
    def domestic(self):
        return self._domestic_view

    @property
    def overseas(self):
        return self._overseas_view

    @property
    def pension(self):
        if self._pension_view is None:
            raise KISUsageError("pension 뷰 없음(테스트)")
        return self._pension_view


def _fake_stock_kis(*, product_code="01", environment="paper", account="12345678-01",
                    domestic_balance=None, domestic_positions=None, domestic_open_orders=None,
                    overseas_balance=None, overseas_positions=None, pension_balance=None,
                    after_hours=None, quote=None):
    """활성 주식 계좌(StockAccount)를 든 가짜 kis -- 읽기 디스패치(국내/해외/연금·시간외)를 테스트한다."""
    domestic_view = SimpleNamespace(
        balance=lambda: domestic_balance,
        positions=lambda: domestic_positions or [],
        open_orders=lambda: domestic_open_orders or [],
    )
    overseas_view = SimpleNamespace(
        balance=lambda *, market=None: overseas_balance,
        positions=lambda *, market=None: overseas_positions or [],
    )
    pension_view = SimpleNamespace(balance=lambda: pension_balance)
    account_view = _FakeStockAccount(
        product_code=product_code, domestic=domestic_view, overseas=overseas_view,
        pension=pension_view,
    )
    domestic_ns = SimpleNamespace(
        stock=lambda sym: SimpleNamespace(quote=lambda: quote, after_hours_quote=lambda: after_hours),
    )
    return SimpleNamespace(
        domestic=domestic_ns, account=account_view, environment=environment, _account=account,
    )


@dataclass(frozen=True)
class _FakeHit:
    symbol: str
    name: str
    _raw: dict[str, Any] = field(default_factory=dict)


def _fake_kis(*, quote=None, overseas_quote=None, index_quote=None, hits=None, ranking=None, balance=None,
              positions=None, open_orders=None, reconcile_report="__unset__",
              bars=None, stock_flows=None, market_flows=None, net_buy=None,
              order_book=None, trades=None, status=None, rank_volume=None, rank_mktcap=None,
              environment="paper", account="12345678-01"):
    rec: list = []                                   # 호출 기록 -- forwarding(인자 전달) 검증용

    def _bars(tag):                                  # 어느 리시버에서 어떤 인자로 bars 가 불렸는지 기록
        def f(interval="1d", *, start=None, end=None, adjusted="__unset__", max_bars=None):
            rec.append((tag, interval, start, end, adjusted, max_bars))
            return bars or []
        return f

    def _by_volume(**k):
        # metric 생략(extra={})과 metric=None 을 구분해 기록 -- CLI/MCP 가 기본값을 재기술하지 않고
        # 생략으로 넘기는지(라이브러리 기본 적용) 검증할 수 있게 한다.
        rec.append(("by_volume", k.get("metric", "__omitted__")))
        return rank_volume or []

    ranking_ns = SimpleNamespace(
        by_change=lambda direction="gainers": ranking or [],
        by_volume=_by_volume,
        by_market_cap=lambda: rank_mktcap or [],
    )
    market_ns = SimpleNamespace(
        investor_flows=lambda **k: market_flows or [],
        investor_net_buy_stocks=lambda **k: net_buy or [],
    )
    _stock = lambda sym: SimpleNamespace(
        quote=lambda: quote,
        bars=_bars("dom_stock"),
        investor_flows=lambda: stock_flows or [],
        order_book=lambda: order_book,
        trades=lambda: trades or [],
        status=lambda: status,
    )
    def _index(tag):
        return SimpleNamespace(
            bars=_bars(tag),
            quote=lambda: (rec.append(("index_quote", tag)) or index_quote),
        )

    domestic = SimpleNamespace(
        stock=_stock,
        index=lambda code: _index("dom_index"),
        search_stocks=lambda query: hits or [],
        ranking=ranking_ns,
        market=market_ns,
    )
    def _ovs_ob():                                   # 태그로 해외 분기 라우팅을 증명(domestic 과 구별)
        rec.append(("order_book", "ovs")); return order_book

    def _ovs_tr():
        rec.append(("trades", "ovs")); return trades or []

    overseas = SimpleNamespace(
        stock=lambda sym: SimpleNamespace(
            quote=lambda: overseas_quote, bars=_bars("ovs_stock"),
            order_book=_ovs_ob, trades=_ovs_tr),
        index=lambda code, *, kind="index": _index(("ovs_index", kind)),
    )
    account_ns = SimpleNamespace(
        balance=lambda: balance,
        domestic=SimpleNamespace(
            positions=lambda: positions or [],
            open_orders=lambda: open_orders or [],
        ),
    )
    orders_ns = SimpleNamespace(
        reconcile=lambda cid, *, refresh=False: (None if reconcile_report == "__unset__" else reconcile_report),
    )
    return SimpleNamespace(
        domestic=domestic, overseas=overseas, account=account_ns, orders=orders_ns,
        environment=environment, _account=account, rec=rec,
    )


# --- 직렬화 방화벽 -----------------------------------------------------------


def test_serialize_drops_raw_and_stringifies_decimal():
    q = _FakeQuote("005930", Decimal(71500), Decimal("0.14"),
                   _raw={"app_secret": "SHOULD_NOT_LEAK", "stac_yn": "Y"})
    out = handlers._serialize(q)
    assert out == {"symbol": "005930", "current_price": "71500", "change_percent": "0.14"}
    assert "_raw" not in out
    assert "SHOULD_NOT_LEAK" not in str(out)


def test_serialize_recurses_lists_and_nested():
    out = handlers._serialize([_FakeHit("005930", "삼성전자", _raw={"x": 1})])
    assert out == [{"symbol": "005930", "name": "삼성전자"}]


def test_serialize_strips_credential_keys_in_nested_mappings():
    # 방어적 denylist -- 미래에 raw 매핑을 돌려주는 핸들러가 생겨도 자격증명 키는 새지 않는다.
    out = handlers._serialize({
        "symbol": "005930",
        "app_secret": "LEAK1", "access_token": "LEAK2", "approval_key": "LEAK3",
        "Authorization": "LEAK4", "app-key": "LEAK5",
        "nested": {"appsecret": "LEAK6", "ok": "keep"},
    })
    assert out == {"symbol": "005930", "nested": {"ok": "keep"}}
    for leaked in ("LEAK1", "LEAK2", "LEAK3", "LEAK4", "LEAK5", "LEAK6"):
        assert leaked not in str(out)


# --- 핸들러 -----------------------------------------------------------------


def test_quote_handler_no_raw():
    kis = _fake_kis(quote=_FakeQuote("005930", Decimal(71500), Decimal("0.14"),
                                     _raw={"secret": "x"}))
    out = handlers.quote(kis, "005930")
    assert out["symbol"] == "005930"
    assert out["current_price"] == "71500"
    assert "_raw" not in out and "secret" not in str(out)


def test_serialize_strips_underscore_keys_from_nested_mappings():
    """_serialize 는 dataclass 뿐 아니라 중첩 dict 에서도 밑줄 키(_raw/내부키)를 떼어 낸다(누출 방지)."""
    out = handlers._serialize(
        {"ok": 1, "_raw": {"app_secret": "LEAK"}, "nested": {"_internal": 2, "keep": 3}}
    )
    assert out == {"ok": 1, "nested": {"keep": 3}}
    assert "LEAK" not in str(out)


def test_serialize_recurses_mapping_proxy_from_frozen_body():
    """_serialize 는 ``MappingProxyType`` (freeze_vendor_payload 결과) 도 매핑으로 재귀 처리한다 --
    ``dict`` 만 보던 분기는 proxy 를 그대로 반환해 ``json.dumps`` 가 Decimal/proxy 에서 깨졌다."""
    from types import MappingProxyType

    frozen = MappingProxyType({"price": Decimal("10.5"), "rows": (MappingProxyType({"_raw": 1, "v": 2}),)})
    out = handlers._serialize(frozen)
    assert out == {"price": "10.5", "rows": [{"v": 2}]}  # proxy 재귀 + Decimal 문자열화 + 밑줄 키 제거
    import json
    json.dumps(out)  # 직렬화가 깨지지 않아야 한다


def test_search_handler_caps_and_serializes():
    hits = [_FakeHit(f"{i:06d}", f"n{i}") for i in range(30)]
    out = handlers.search(kis := _fake_kis(hits=hits), "삼성", limit=5)
    assert len(out) == 5
    assert out[0] == {"symbol": "000000", "name": "n0"}
    assert kis is not None


def test_ranking_handler():
    rows = [_FakeHit("005930", "삼성전자"), _FakeHit("000660", "SK하이닉스")]
    out = handlers.ranking_change(_fake_kis(ranking=rows), direction="gainers", limit=10)
    assert [r["symbol"] for r in out] == ["005930", "000660"]


def test_stock_bars_handler_caps_serializes_and_validates():
    bars = [_FakeHit(f"{i:06d}", f"n{i}", _raw={"secret": "x"}) for i in range(30)]
    out = handlers.stock_bars(_fake_kis(bars=bars), "005930", interval="1d", start="20250101", limit=5)
    assert len(out) == 5 and out[0] == {"symbol": "000000", "name": "n0"}
    assert "_raw" not in str(out) and "secret" not in str(out)
    for kwargs in ({"interval": "3d"}, {"region": "mars"}):          # 리터럴 밖 interval/region
        with pytest.raises(KISUsageError):
            handlers.stock_bars(_fake_kis(bars=bars), "005930", start="20250101", **kwargs)


def test_stock_bars_handler_region_overseas_forwards_args():
    kis = _fake_kis(bars=[_FakeHit("AAPL", "apple")])
    out = handlers.stock_bars(kis, "AAPL", region="overseas", interval="1wk",
                              start="20250101", end="20250201", limit=3)
    assert out == [{"symbol": "AAPL", "name": "apple"}]
    # 해외 리시버로 라우팅되고 사용자가 준 interval/start/end/max_bars 만 전달됐는지 검증. adjusted 는
    # 주지 않았으므로 **전달되지 않아야**("__unset__") 한다 -- 라이브러리 기본(수정주가)을 MCP 가
    # 재기술하지 않는다(소비자 경계).
    assert kis.rec == [("ovs_stock", "1wk", "20250101", "20250201", "__unset__", 3)]


def test_bars_handlers_require_start_for_period_intervals():
    # 기간봉(1d/1wk/1mo)은 start 없이 호출하면 라이브러리가 내부에서 터지므로 -- LLM 이 bars(symbol)
    # 로 자연스럽게 부르는 실패를 MCP 경계에서 명확한 메시지로 fail-closed 한다. 분봉(1m)만 생략 허용.
    kis = _fake_kis(bars=[_FakeHit("000000", "n0")])
    for interval in ("1d", "1wk", "1mo"):
        with pytest.raises(KISUsageError, match="start"):
            handlers.stock_bars(kis, "005930", interval=interval)
        with pytest.raises(KISUsageError, match="start"):
            handlers.index_bars(kis, "FX@KRW", kind="fx", interval=interval)
    assert handlers.stock_bars(kis, "005930", interval="1m") == [{"symbol": "000000", "name": "n0"}]
    assert handlers.index_bars(kis, ".DJI", interval="1m") == [{"symbol": "000000", "name": "n0"}]


def test_index_bars_handler_fx_and_validates_kind():
    bars = [_FakeHit("FX@KRW", "usdkrw")]
    kis = _fake_kis(bars=bars)
    out = handlers.index_bars(kis, "FX@KRW", kind="fx", interval="1wk", start="20250101", end="20250201")
    assert out == [{"symbol": "FX@KRW", "name": "usdkrw"}]
    # 해외 index 리시버로 kind=fx 가 accessor 에, interval/start/end 가 bars 에 전달됐는지 검증.
    assert kis.rec == [(("ovs_index", "fx"), "1wk", "20250101", "20250201", "__unset__", 500)]
    kis2 = _fake_kis(bars=bars)                                      # 국내 지수는 dom_index 로 라우팅
    assert handlers.index_bars(kis2, "0001", region="domestic", start="20250101") == out
    assert kis2.rec[0][0] == "dom_index"
    with pytest.raises(KISUsageError):                               # 해외 kind 리터럴 밖
        handlers.index_bars(_fake_kis(bars=bars), "FX@KRW", kind="crypto", start="20250101")
    with pytest.raises(KISUsageError, match="해외"):                  # 국내에 kind 주면 거부(조용히 무시 안 함)
        handlers.index_bars(_fake_kis(bars=bars), "0001", region="domestic", kind="fx", start="20250101")


def test_index_quote_handler_routes_validates_and_serializes_without_raw():
    snapshot = _FakeIndexQuote(
        "SPX", "S&P500", Decimal("7718.94"), Decimal("-86.51"),
        _raw={"access_token": "LEAK"},
    )
    kis = _fake_kis(index_quote=snapshot)

    out = handlers.index_quote(kis, "SPX")

    assert out == {
        "code": "SPX", "name": "S&P500", "value": "7718.94", "change": "-86.51",
    }
    assert kis.rec == [("index_quote", ("ovs_index", "index"))]
    assert "LEAK" not in str(out)

    domestic = _fake_kis(index_quote=snapshot)
    assert handlers.index_quote(domestic, "0001", region="domestic") == out
    assert domestic.rec == [("index_quote", "dom_index")]
    with pytest.raises(KISUsageError):
        handlers.index_quote(_fake_kis(index_quote=snapshot), "SPX", region="mars")
    with pytest.raises(KISUsageError, match="해외"):
        handlers.index_quote(
            _fake_kis(index_quote=snapshot), "0001", region="domestic", kind="fx"
        )
    with pytest.raises(KISUsageError):
        handlers.index_quote(_fake_kis(index_quote=snapshot), "SPX", kind="crypto")


def test_overseas_index_kind_literal_matches_code_map():
    # 단일 출처 OverseasIndexKind(Literal)와 _OVERSEAS_INDEX_KIND(코드 매핑 dict)가 드리프트하지 않음을 고정
    # -- MCP·CLI 가 둘 다 이 하나에서 파생한다(재선언 없음).
    from typing import get_args

    from kis_trader.overseas.namespace import _OVERSEAS_INDEX_KIND, OverseasIndexKind
    assert set(get_args(OverseasIndexKind)) == set(_OVERSEAS_INDEX_KIND)


def test_order_book_trades_status_handlers_serialize():
    ob = _FakeHit("005930", "book", _raw={"secret": "s"})            # _serialize 는 임의 dataclass OK
    assert handlers.order_book(_fake_kis(order_book=ob), "005930") == {"symbol": "005930", "name": "book"}
    tr = [_FakeHit(f"{i}", "t", _raw={"token": "LEAK"}) for i in range(60)]
    out = handlers.trades(_fake_kis(trades=tr), "005930", limit=10)
    assert len(out) == 10 and "LEAK" not in str(out)
    st = _FakeHit("005930", "normal")
    assert handlers.status(_fake_kis(status=st), "005930") == {"symbol": "005930", "name": "normal"}
    # region=overseas 는 해외 핸들로 분기해 돈다 -- 태그로 라우팅을 증명(domestic 과 다른 리시버).
    kob = _fake_kis(order_book=ob)
    assert handlers.order_book(kob, "AAPL", region="overseas") == {"symbol": "005930", "name": "book"}
    assert kob.rec == [("order_book", "ovs")]
    ktr = _fake_kis(trades=tr)
    assert handlers.trades(ktr, "AAPL", region="overseas", limit=5) == [
        {"symbol": str(i), "name": "t"} for i in range(5)]
    assert ktr.rec == [("trades", "ovs")]
    for fn in (handlers.order_book, handlers.trades):               # region 리터럴 밖 거부
        with pytest.raises(KISUsageError):
            fn(_fake_kis(), "AAPL", region="mars")


def test_ranking_volume_and_market_cap_handlers():
    rows = [_FakeHit("005930", "삼성전자")]
    assert handlers.ranking_volume(_fake_kis(rank_volume=rows), limit=5) == [{"symbol": "005930", "name": "삼성전자"}]
    assert handlers.ranking_market_cap(_fake_kis(rank_mktcap=rows), limit=5) == [{"symbol": "005930", "name": "삼성전자"}]
    # 유효 metric 이 라이브러리로 그대로 전달되는지(기본으로 조용히 떨어지지 않는지) 검증.
    kis = _fake_kis(rank_volume=rows)
    handlers.ranking_volume(kis, metric="turnover")
    assert kis.rec == [("by_volume", "turnover")]          # 유효 metric 은 그대로 전달
    omit = _fake_kis(rank_volume=rows)
    handlers.ranking_volume(omit)                                    # metric 생략 -> 재기술 않고 생략 전달
    assert omit.rec == [("by_volume", "__omitted__")]
    with pytest.raises(KISUsageError):                               # metric 리터럴 밖
        handlers.ranking_volume(_fake_kis(rank_volume=rows), metric="nope")


def test_stock_investor_flows_handler_caps_and_serializes():
    flows = [_FakeHit(f"{i:06d}", f"n{i}", _raw={"app_secret": "LEAK"}) for i in range(30)]
    out = handlers.stock_investor_flows(_fake_kis(stock_flows=flows), "005930", limit=7)
    assert len(out) == 7
    assert out[0] == {"symbol": "000000", "name": "n0"}   # full dict -- _raw/시크릿 비노출
    assert "LEAK" not in str(out)


def test_market_investor_flows_handler_validates_market():
    rows = [_FakeHit("KOSPI", "x")]
    out = handlers.market_investor_flows(_fake_kis(market_flows=rows), market="KOSPI", limit=10)
    assert out == [{"symbol": "KOSPI", "name": "x"}]
    with pytest.raises(KISUsageError):
        handlers.market_investor_flows(_fake_kis(market_flows=rows), market="NASDAQ")


def test_investor_net_buy_stocks_handler_validates_enums():
    rows = [_FakeHit("005930", "삼성전자", _raw={"token": "LEAK"}), _FakeHit("000660", "SK하이닉스")]
    out = handlers.investor_net_buy_stocks(
        _fake_kis(net_buy=rows), market="KOSPI", basis="amount", side="buy", investor="foreign", limit=10)
    assert out[0] == {"symbol": "005930", "name": "삼성전자"}   # full dict -- _raw/시크릿 비노출
    assert [r["symbol"] for r in out] == ["005930", "000660"] and "LEAK" not in str(out)
    kis = _fake_kis(net_buy=rows)
    for kwargs in ({"market": "X"}, {"basis": "X"}, {"side": "X"}, {"investor": "X"}):
        with pytest.raises(KISUsageError):
            handlers.investor_net_buy_stocks(kis, **kwargs)


@pytest.mark.parametrize("bad", [-1, 0, True, "2"])
@pytest.mark.parametrize("fn", ["search", "ranking_change", "ranking_volume", "ranking_market_cap",
                                "positions", "open_orders", "stock_bars", "index_bars", "trades",
                                "stock_investor_flows", "market_investor_flows",
                                "investor_net_buy_stocks"])
def test_list_handlers_reject_bad_limit(fn, bad):
    # limit 검증은 API 호출 전에 돈다 -- bool(int 서브클래스)·비int·1 미만은 조용히 오답(음수는
    # [:limit] 가 마지막 행만 빼고 반환)하지 않고 KISUsageError 로 거부한다.
    kis = _fake_kis(hits=[], ranking=[])
    call = {
        "search": lambda: handlers.search(kis, "x", limit=bad),
        "ranking_change": lambda: handlers.ranking_change(kis, direction="gainers", limit=bad),
        "positions": lambda: handlers.positions(kis, limit=bad),
        "open_orders": lambda: handlers.open_orders(kis, limit=bad),
        "ranking_volume": lambda: handlers.ranking_volume(kis, limit=bad),
        "ranking_market_cap": lambda: handlers.ranking_market_cap(kis, limit=bad),
        "stock_bars": lambda: handlers.stock_bars(kis, "005930", start="20250101", limit=bad),
        "index_bars": lambda: handlers.index_bars(kis, "FX@KRW", kind="fx", start="20250101", limit=bad),
        "trades": lambda: handlers.trades(kis, "005930", limit=bad),
        "stock_investor_flows": lambda: handlers.stock_investor_flows(kis, "005930", limit=bad),
        "market_investor_flows": lambda: handlers.market_investor_flows(kis, limit=bad),
        "investor_net_buy_stocks": lambda: handlers.investor_net_buy_stocks(kis, limit=bad),
    }[fn]
    with pytest.raises(KISUsageError):
        call()


def test_order_preview_not_sent_and_masks_account():
    kis = _fake_kis(environment="real", account="12345678-01")
    out = handlers.order_preview(kis, frozenset(), symbol="005930", side="buy", quantity=10,
                                 limit_price="70000", has_domestic_risk_cap=True)
    assert out["sent"] is False
    assert out["environment"] == "real"
    assert out["account"] == "****7801"          # 마지막 4자리만
    assert "1234567801" not in str(out)          # 전체 계좌번호 비노출
    assert out["order_type"] == "limit"
    assert out["symbol"] == "005930" and out["side"] == "buy" and out["quantity"] == 10


def test_order_preview_market_when_no_price():
    out = handlers.order_preview(_fake_kis(), frozenset(), symbol="005930", side="sell", quantity=1)
    assert out["order_type"] == "market" and out["limit_price"] is None


def test_order_preview_domestic_real_refused_without_domestic_cap():
    # preview 는 place 의 refusal 을 대칭으로 비춘다 -- 국내 실전인데 국내 캡이 없으면 미리보기도 거부
    # (place 가 거부할 주문을 preview 가 통과시키지 않는다; has_domestic_risk_cap 기본 False).
    with pytest.raises(KISUsageError, match="국내"):
        handlers.order_preview(_fake_kis(environment="real"), frozenset(), symbol="005930",
                               side="buy", quantity=1, limit_price="70000")


@pytest.mark.parametrize("bad", [
    {"side": "hold", "quantity": 1},
    {"side": "buy", "quantity": 0},
    {"side": "buy", "quantity": -5},
])
def test_order_preview_rejects_bad_input(bad):
    from kis_trader.errors import KISUsageError
    with pytest.raises(KISUsageError):
        handlers.order_preview(_fake_kis(), frozenset(), symbol="005930", **bad)


def test_reconcile_unresolved_returns_in_flight():
    out = handlers.reconcile(_fake_kis(), client_order_id="cid-1")
    assert out == {"client_order_id": "cid-1", "resolved": False, "report": None}


def test_reconcile_resolved_serializes_report():
    report = _FakeHit("005930", "done", _raw={"secret": "x"})
    out = handlers.reconcile(_fake_kis(reconcile_report=report), client_order_id="cid-2")
    assert out["resolved"] is True
    assert out["report"] == {"symbol": "005930", "name": "done"}
    assert "secret" not in str(out)


def test_reconcile_forces_broker_refresh():
    # 사후확정 도구는 저장 스냅샷(접수 직후 체결 0)이 아니라 브로커를 실제 재스캔해야 한다 --
    # handlers.reconcile 이 refresh=True 를 넘기는지 고정(회귀로 캐시 단락 복귀 시 이중매수 재발).
    seen: dict = {}
    orders = SimpleNamespace(
        reconcile=lambda cid, *, refresh=False: seen.update(cid=cid, refresh=refresh))
    handlers.reconcile(SimpleNamespace(orders=orders), client_order_id="cid-x")
    assert seen == {"cid": "cid-x", "refresh": True}


def test_account_tools_reject_non_stock_account():
    """잔고/보유/미체결 도구는 주식 계좌 전용 -- 다른 계좌 뷰면 명확히 거부."""
    from kis_trader.errors import KISUsageError
    kis = _fake_kis()  # account 가 StockAccount 가 아님(파생/기타 계좌 뷰 흉내)
    for fn in (handlers.balance, handlers.positions, handlers.open_orders):
        with pytest.raises(KISUsageError):
            fn(kis)


def test_ranking_rejects_bad_direction():
    from kis_trader.errors import KISUsageError
    with pytest.raises(KISUsageError):
        handlers.ranking_change(_fake_kis(), direction="sideways")


# --- 계좌종류-인식 읽기 디스패치 ---------------------------------------------


def test_balance_domestic_product_01_uses_domestic_view():
    kis = _fake_stock_kis(product_code="01", domestic_balance=_FakeBalance("domestic", Decimal(100)))
    out = handlers.balance(kis, region="domestic", active_profile="general")
    assert out == {"label": "domestic", "total": "100"}       # C8: domestic.balance() shape


def test_balance_domestic_product_22_stays_domestic_not_pension():
    # 연금저축(22)은 IRP 가 아니다 -- 국내주식 잔고 경로를 타야 한다(스펙의 pension 전제는 틀렸다).
    kis = _fake_stock_kis(product_code="22", domestic_balance=_FakeBalance("dom22", Decimal(5)),
                          pension_balance=SimpleNamespace(positions=()))
    out = handlers.balance(kis, region="domestic", active_profile="pension")
    assert out == {"label": "dom22", "total": "5"}


def test_balance_domestic_product_29_uses_pension_lens():
    kis = _fake_stock_kis(product_code="29", pension_balance=_FakeBalance("pension", Decimal(7)),
                          domestic_balance=_FakeBalance("SHOULD_NOT", Decimal(0)))
    out = handlers.balance(kis, region="domestic", active_profile="irp")
    assert out == {"label": "pension", "total": "7"}


def test_positions_product_29_from_pension_balance():
    pos = _FakePosition("005930", 3, _raw={"secret": "x"})
    kis = _fake_stock_kis(product_code="29",
                          pension_balance=SimpleNamespace(positions=(pos,)))
    out = handlers.positions(kis, region="domestic", active_profile="irp")
    assert out == [{"symbol": "005930", "quantity": 3}]
    assert "secret" not in str(out)


def test_positions_product_01_from_domestic_view():
    kis = _fake_stock_kis(product_code="01", domestic_positions=[_FakePosition("000660", 2)])
    out = handlers.positions(kis, region="domestic", active_profile="general")
    assert out == [{"symbol": "000660", "quantity": 2}]


def test_positions_product_22_from_domestic_view_not_pension():
    # T6: 연금저축(22)은 IRP 가 아니라 국내주식 보유 경로를 탄다(pension 렌즈 아님).
    kis = _fake_stock_kis(product_code="22", domestic_positions=[_FakePosition("005930", 4)],
                          pension_balance=SimpleNamespace(positions=(_FakePosition("SHOULD_NOT", 9),)))
    out = handlers.positions(kis, region="domestic", active_profile="pension")
    assert out == [{"symbol": "005930", "quantity": 4}]
    assert "SHOULD_NOT" not in str(out)


def test_overseas_balance_general_real_routes_to_overseas():
    kis = _fake_stock_kis(product_code="01", environment="real",
                          overseas_balance=[_FakeBalance("US", Decimal(42), _raw={"app_secret": "L"})])
    out = handlers.balance(kis, region="overseas", market=None, active_profile="general")
    assert out == {"by_market": [{"label": "US", "total": "42"}]}   # 항상 dict (market 생략=by_market 래핑)
    assert "app_secret" not in str(out) and "L" not in str(out)   # 방화벽 유지


def test_overseas_positions_general_real_routes_to_overseas():
    kis = _fake_stock_kis(product_code="01", environment="real",
                          overseas_positions=[_FakePosition("AAPL", 1)])
    out = handlers.positions(kis, region="overseas", market="US", active_profile="general")
    assert out == [{"symbol": "AAPL", "quantity": 1}]


def test_overseas_read_rejected_on_paper_mcp_policy():
    kis = _fake_stock_kis(product_code="01", environment="paper")
    with pytest.raises(KISUsageError, match="MCP 정책"):
        handlers.balance(kis, region="overseas", active_profile="general")


@pytest.mark.parametrize("profile", ["isa", "pension", "irp"])
def test_overseas_read_rejected_for_non_general_profile(profile):
    kis = _fake_stock_kis(product_code="01", environment="real")
    with pytest.raises(KISUsageError, match="general"):
        handlers.balance(kis, region="overseas", active_profile=profile)


def test_open_orders_admits_orderable_01_and_22_but_refuses_irp_29():
    # F1: 미체결 조회는 주문 능력(orderable)으로 가른다 -- 위탁(01)·연금저축(22)은 ADMIT(국내 미체결
    # 행 반환), IRP(29)만 REFUSE(조회전용이라 미체결 주문이 없다).
    for product in ("01", "22"):
        ok = _fake_stock_kis(product_code=product,
                             domestic_open_orders=[_FakePosition("005930", 1)])
        assert handlers.open_orders(ok) == [{"symbol": "005930", "quantity": 1}]
    with pytest.raises(KISUsageError, match="주문 가능"):
        handlers.open_orders(_fake_stock_kis(product_code="29"))


def test_quote_after_hours_routes_to_after_hours_quote():
    ah = _FakeQuote("005930", Decimal(71000), Decimal("0.1"), _raw={"secret": "x"})
    kis = _fake_stock_kis(product_code="01", after_hours=ah)
    out = handlers.quote(kis, "005930", region="domestic", session="after_hours")
    assert out["symbol"] == "005930" and out["current_price"] == "71000"
    assert "secret" not in str(out)


def test_quote_after_hours_overseas_refused_region_wins():
    kis = _fake_stock_kis(product_code="01")
    with pytest.raises(KISUsageError, match="국내"):
        handlers.quote(kis, "AAPL", region="overseas", session="after_hours")


def test_quote_rejects_bad_session():
    with pytest.raises(KISUsageError, match="session"):
        handlers.quote(_fake_stock_kis(), "005930", session="premarket")


def test_quote_overseas_regular_routes_to_overseas_and_no_leak():
    # T6: region='overseas' + session='regular' 은 해외 종목 뷰로 라우팅하고 _raw/자격증명을 새지 않는다.
    oq = _FakeQuote("AAPL", Decimal("150.25"), Decimal("1.2"),
                    _raw={"app_secret": "LEAK", "last": "150.25"})
    kis = _fake_kis(overseas_quote=oq)
    out = handlers.quote(kis, "AAPL", region="overseas", session="regular")
    assert out == {"symbol": "AAPL", "current_price": "150.25", "change_percent": "1.2"}
    assert "_raw" not in out and "app_secret" not in str(out) and "LEAK" not in str(out)


# --- 서버(도구 등록) -- mcp 있을 때만 -----------------------------------------


def _build_test_server(kis=None, *, environment="paper", profile="general", allowlist=None,
                       capped_currencies=frozenset()):
    from kis_trader.mcp._guardrails import CircuitBreaker
    from kis_trader.mcp._session import MCPSession, _StartupAuthorization
    from kis_trader.mcp.server import build_server

    kis = kis if kis is not None else _fake_kis(environment=environment)
    session = MCPSession(profile=profile, environment=environment, startup_client=kis,
                         auth=_StartupAuthorization(allow_real=False, confirmed=False),
                         build=None if profile is None else lambda p, e: kis)
    return build_server(session, allowlist=allowlist, breaker=CircuitBreaker(),
                        capped_currencies=capped_currencies)


def test_server_registers_read_switch_and_guarded_order_tools():
    pytest.importorskip("mcp")
    import asyncio

    server = _build_test_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    # 정확집합(==)으로 못박는다: subset(<=) 이면 의도치 않은 툴이 하나 더 등록돼도 통과해버린다.
    # 노출 표면이 늘면 이 테스트가 깨져 의식적 결정을 강제한다(실주문 안전 표면의 핵심 불변식).
    assert names == {"quote", "search", "ranking_change", "ranking_volume", "ranking_market_cap",
                     "stock_bars", "index_bars", "index_quote", "order_book", "trades", "status",
                     "stock_investor_flows", "market_investor_flows", "investor_net_buy_stocks",
                     "balance", "positions", "open_orders", "reconcile",
                     "use_environment", "use_profile", "list_profiles", "current_account",
                     "order_preview", "place_order", "cancel_order", "modify_order"}
    # 원시 매매 메서드(buy/sell/credit)는 직접 노출하지 않는다 -- 가드레일 통과하는 place_order 만.
    assert not (names & {"buy", "sell", "credit_buy", "credit_sell"})


def test_list_profiles_tool_returns_stored_switchable_profiles_for_active_environment(
    tmp_path, monkeypatch,
):
    pytest.importorskip("mcp")

    from kis_trader import KISConfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for profile in ("pension", "custom_account", "general"):
        KISConfig(profile=profile, app_key=f"{profile}_KEY", app_secret=f"{profile}_SECRET").save()

    server = _build_test_server(profile="pension", environment="paper")
    tool = server._tool_manager.get_tool("list_profiles")
    assert tool is not None
    result = tool.fn()

    assert result == {
        "profiles": ["general", "pension"],
        "active_profile": "pension",
        "environment": "paper",
        "switching_enabled": True,
    }


def test_list_profiles_tool_direct_credentials_reports_no_switchable_profiles(
    tmp_path, monkeypatch,
):
    pytest.importorskip("mcp")

    from kis_trader import KISConfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    KISConfig(profile="general", app_key="GENERAL_KEY", app_secret="GENERAL_SECRET").save()

    server = _build_test_server(profile=None, environment="paper")
    tool = server._tool_manager.get_tool("list_profiles")
    assert tool is not None
    result = tool.fn()

    assert result == {
        "profiles": [],
        "active_profile": None,
        "environment": "paper",
        "switching_enabled": False,
    }


def test_environment_switch_safety_phrasing_is_conditional_not_absolute():
    # FIX F 회귀: 서버 instructions 와 use_environment 도구 설명이 "환경을 real 로 바꿔도 실주문은
    # 열리지 않는다" 같은 절대적(그리고 거짓인) 주장을 담지 않고, 기동 시 ALLOW_REAL+REAL_CONFIRM 이
    # 켜져 있었다면 집행 가능해진다는 조건부 진실을 담는지 고정한다 -- 그 상태에서 use_environment
    # ('real') 로 실주문이 실제로 열릴 수 있기 때문이다.
    pytest.importorskip("mcp")
    import asyncio

    server = _build_test_server()
    instructions = server.instructions or ""
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    use_env_desc = tools["use_environment"].description or ""
    for text in (instructions, use_env_desc):
        assert "열리지 않는다" not in text              # 절대적 거짓 주장 재발 방지
        assert "집행 가능" in text                      # 조건부 진실이 들어 있다
    # 프로필 축도 같은 거짓 주장("읽는 계좌만"/"조회 맥락만") 재발 방지: use_profile 로 전환하면
    # 실주문이 언락된 세션에서 주문이 전환된 계좌로 나가므로 "읽기 전용 전환"은 거짓이다.
    use_profile_desc = tools["use_profile"].description or ""
    assert "읽는 계좌 종류만" not in use_profile_desc
    assert "조회 맥락만" not in use_profile_desc
    assert "주문 대상" in use_profile_desc              # 주문도 전환 계좌로 간다는 진실
    assert "읽는 계좌/환경" not in instructions          # instructions 도 읽기-전용 표현 금지


def test_order_tool_input_schemas_pin_switch_region_shape():
    # 전환+해외 확장 스키마를 고정: place_order/order_preview 는 region(domestic/overseas)·exchange 를
    # 노출하고 division/side 를 유지하며 side enum 은 buy/sell 로 좁혀져 있다. quote 는 session(regular/
    # after_hours), balance/positions 는 region/market 을 노출한다.
    pytest.importorskip("mcp")
    import asyncio

    server = _build_test_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    # 주문 4도구는 정확집합(==)으로 못박는다: subset(<=) 이면 taint 운반 파라미터(예: account, dict 인자)가
    # 주문 도구에 하나 더 붙어도 통과해버린다. 읽기 도구는 <= 로 둬(무해한 읽기 파라미터 추가에 안 깨짐).
    _ORDER_PARAMS = {"symbol", "side", "quantity", "limit_price", "division", "region", "exchange"}
    place = tools["place_order"].input_schema["properties"]
    assert set(place) == _ORDER_PARAMS
    assert place["side"]["enum"] == ["buy", "sell"]            # Side 리터럴이 넓어지지 않게
    assert place["region"]["enum"] == ["domestic", "overseas"]
    preview = tools["order_preview"].input_schema["properties"]
    assert set(preview) == _ORDER_PARAMS                       # dry-run 쌍둥이와 형상 일치
    assert preview["region"]["enum"] == ["domestic", "overseas"]
    assert set(tools["cancel_order"].input_schema["properties"]) == {"client_order_id"}
    assert set(tools["modify_order"].input_schema["properties"]) == {
        "client_order_id", "limit_price", "quantity"}
    # use_profile 파라미터는 SwitchableProfile Literal 에서 파생한 enum 을 싣는다(스키마<->런타임 가드 단일 출처).
    assert tools["use_profile"].input_schema["properties"]["profile"]["enum"] == [
        "general", "isa", "pension", "irp"]
    q = tools["quote"].input_schema["properties"]
    assert {"region", "session"} <= set(q)
    assert q["session"]["enum"] == ["regular", "after_hours"]
    for read_tool in ("balance", "positions"):
        props = tools[read_tool].input_schema["properties"]
        assert {"region", "market"} <= set(props)
    # 시세 bars/book/trades 의 region enum 을 고정 -- Region 이 str 로 넓어지면(검증 우회) 깨진다.
    # (index_bars 의 kind 집합 드리프트는 test_overseas_index_kind_literal_matches_code_map 가 별도로 고정.)
    for read_tool in ("stock_bars", "index_bars", "index_quote", "order_book", "trades"):
        assert tools[read_tool].input_schema["properties"]["region"]["enum"] == ["domestic", "overseas"]


def test_build_client_missing_credentials_gives_clear_error(monkeypatch):
    """자격증명이 없으면 raw KeyError 가 아니라 안내 메시지를 담은 KISUsageError 를 올린다."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import build_client
    for k in ("KIS_MCP_PROFILE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KISUsageError) as exc:
        build_client()
    msg = str(exc.value)
    assert "자격증명" in msg and "KIS_APP_KEY" in msg


def _isolate_config(monkeypatch, tmp_path):
    """실 사용자의 ~/.config 를 읽지 않도록 XDG 를 임시 경로로 돌리고 KIS_* 를 지운다."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


def test_build_session_refuses_legacy_store_points_to_migrate_but_opens_v2(monkeypatch, tmp_path):
    """레거시(스키마 마커 없는) 저장소로는 기동을 거부하고 'kis config migrate' 를 짚어준다 -- 런타임은
    두 축 schema-2 만 읽기 때문이다(조용한 오해석/엉뚱한 환경 접속 대신 명확한 거부). schema-2 저장소로는
    정상 기동한다."""
    pytest.importorskip("mcp")

    from kis_trader import KISConfig
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _build_session

    _isolate_config(monkeypatch, tmp_path)
    config_dir = tmp_path / "config" / "kis-trader"
    config_dir.mkdir(parents=True)

    # 레거시: 스키마 마커 없이 프로필 섹션만 -> 기동 거부 + migrate 안내.
    (config_dir / "credentials.json").write_text(
        json.dumps({"main": {"app_key": "k", "app_secret": "s", "account": "12345678-01"}}),
        encoding="utf-8",
    )
    environ = {"KIS_MCP_PROFILE": "main"}
    with pytest.raises(KISUsageError, match="kis config migrate"):
        _build_session(environ, "paper", None)

    # v2(schema 2): 같은 프로필을 두 축 저장소로 다시 쓰면 정상 기동한다.
    (config_dir / "credentials.json").unlink()
    KISConfig(profile="main", app_key="k", app_secret="s",
              account="12345678-01", config_dir=config_dir).save()
    _build_session(environ, "paper", None)


def test_resolve_transport_defaults_to_stdio():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    assert _resolve_transport({}) == ("stdio", {})


def test_resolve_transport_sse_local_passes_host_port():
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    transport, kw = _resolve_transport({"KIS_MCP_TRANSPORT": "sse", "KIS_MCP_PORT": "9001"})
    assert transport == "sse"
    assert kw == {"host": "127.0.0.1", "port": 9001}


def test_resolve_transport_rejects_nonlocal_bind():
    """돈이 오가는 서버 -- 비-로컬 bind 는 fail-closed(조용히 열어 주지 않음)."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "streamable-http", "KIS_MCP_HOST": "0.0.0.0"})


def test_resolve_transport_rejects_unknown_transport():
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "carrier-pigeon"})


def test_resolve_transport_allows_nonlocal_only_with_token():
    """비-로컬 bind 는 액세스 토큰이 있을 때만 허용(인증 없는 공개 노출 방지)."""
    pytest.importorskip("mcp")
    from kis_trader.mcp.server import _resolve_transport
    transport, kw = _resolve_transport({
        "KIS_MCP_TRANSPORT": "streamable-http", "KIS_MCP_HOST": "0.0.0.0",
        "KIS_MCP_ACCESS_TOKEN": "s3cret",
    })
    assert transport == "streamable-http"
    assert kw == {"host": "0.0.0.0", "port": 8000}


# --- bearer 토큰 미들웨어(HTTP 전송 방어심층) ---------------------------------


def _drive_bearer(headers, token="s3cret"):
    """BearerTokenMiddleware 를 http scope 로 한 번 구동하고 (status, inner_호출여부)."""
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    inner_called = {"v": False}

    async def inner(scope, receive, send):
        inner_called["v"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = BearerTokenMiddleware(inner, token)
    asyncio.run(mw({"type": "http", "headers": headers}, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, inner_called["v"]


def test_bearer_middleware_rejects_missing_or_wrong_token():
    pytest.importorskip("mcp")
    assert _drive_bearer([]) == (401, False)
    assert _drive_bearer([(b"authorization", b"Bearer nope")]) == (401, False)
    assert _drive_bearer([(b"authorization", b"Basic s3cret")]) == (401, False)


def test_bearer_middleware_passes_correct_token():
    pytest.importorskip("mcp")
    assert _drive_bearer([(b"authorization", b"Bearer s3cret")]) == (200, True)


def test_bearer_middleware_passes_through_non_http_scope():
    """lifespan 등 비-HTTP scope 는 토큰 검사 없이 통과."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    seen = {"v": False}

    async def inner(scope, receive, send):
        seen["v"] = True

    async def send(msg):
        ...

    async def receive():
        return {}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")({"type": "lifespan"}, receive, send))
    assert seen["v"] is True


def test_bearer_middleware_denies_websocket_scope():
    """deny-by-default -- websocket scope 는 인증 없이 통과시키지 않고 닫는다(미인증 경로 차단)."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    inner_called = {"v": False}

    async def inner(scope, receive, send):
        inner_called["v"] = True

    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")(
        {"type": "websocket", "headers": []}, receive, send))
    assert inner_called["v"] is False
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_bearer_middleware_rejects_duplicate_auth_headers():
    """같은 Authorization 헤더가 둘이면 거부(헤더 스머글링 방지)."""
    pytest.importorskip("mcp")
    dup = [(b"authorization", b"Bearer s3cret"), (b"authorization", b"Bearer s3cret")]
    assert _drive_bearer(dup) == (401, False)


def test_bearer_middleware_non_ascii_token_no_500():
    """비-ASCII 토큰/헤더도 예외(500) 없이 bytes 상수시간 비교 -- 일치 통과, 불일치 401."""
    pytest.importorskip("mcp")
    assert _drive_bearer([(b"authorization", "Bearer 비밀".encode())], token="비밀") == (200, True)
    assert _drive_bearer([(b"authorization", b"Bearer \x80\x81")], token="s3cret") == (401, False)


def test_bearer_middleware_reject_payload_shape():
    """401 응답은 www-authenticate 헤더와 JSON 본문을 갖는다."""
    pytest.importorskip("mcp")
    import asyncio

    from kis_trader.mcp._http_auth import BearerTokenMiddleware
    sent: list = []

    async def inner(scope, receive, send):
        ...

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(BearerTokenMiddleware(inner, "s3cret")({"type": "http", "headers": []}, receive, send))
    assert sent[0]["status"] == 401
    assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"error":"unauthorized"}'


def test_main_missing_credentials_exits_cleanly_without_traceback(monkeypatch, capsys):
    """진입점 main() 은 자격증명이 없을 때 raw 트레이스백이 아니라 한 줄 안내 + SystemExit(1)."""
    pytest.importorskip("mcp")
    from kis_trader.mcp import server
    for k in ("KIS_MCP_PROFILE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_MCP_ENVIRONMENT", "paper")
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("kis-mcp:") and "자격증명" in err
    assert "Traceback" not in err and "KeyError" not in err


def test_resolve_transport_rejects_non_numeric_port():
    """숫자가 아닌 KIS_MCP_PORT 는 raw ValueError 가 아니라 KISUsageError(깔끔한 종료)."""
    pytest.importorskip("mcp")
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _resolve_transport
    with pytest.raises(KISUsageError):
        _resolve_transport({"KIS_MCP_TRANSPORT": "sse", "KIS_MCP_PORT": "abc"})


def test_breaker_from_env_honors_explicit_zero_and_value():
    # 명시적 KIS_MCP_MAX_REAL_ORDERS=0 은 조용히 기본 10 이 되면 안 된다 -- 값으로 분기(falsy)하면
    # 사용자가 둔 가장 엄격한 cap 이 사라져 실주문 10건이 허용된다. 설정 여부로 분기해 명시값을 그대로
    # CircuitBreaker 에 넘긴다(0/음수는 거기서 "양의 정수여야 한다"로 fail-closed).
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp.server import _breaker_from_env

    with pytest.raises(KISUsageError):
        _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": "0"})
    # 빈 문자열(명시적으로 설정은 됐으나 값이 빔)도 조용히 기본 10 이 되면 안 된다 -- fail-closed.
    with pytest.raises(KISUsageError):
        _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": ""})
    assert _breaker_from_env({"KIS_MCP_MAX_REAL_ORDERS": "3"})._max == 3
    assert _breaker_from_env({})._max == 10

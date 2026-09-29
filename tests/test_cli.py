"""CLI(``kis``) -- 인자 배선, 출력 렌더링, 주문 안전 게이트, 오류 번역."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import kis_trader.cli.app as cli_main
from kis_trader.account import StockAccount
from kis_trader.cli.app import build_parser
from kis_trader.cli.commands import account, order
from kis_trader.cli.context import account_suffix
from kis_trader.cli.errors import CliAborted, CliConfigError, translate
from kis_trader.cli.output import _display_width, _pad, render, to_jsonable
from kis_trader.domestic.derivative_account import DomesticDerivativesAccount
from kis_trader.errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    KISRateLimitError,
    KISUsageError,
    OrderError,
    OrderRejectedError,
    OrderTimeoutError,
    PreTradeRiskError,
)
from kis_trader.overseas.derivative_account import OverseasDerivativesAccount


@pytest.fixture(autouse=True)
def _isolate_credentials(tmp_path, monkeypatch):
    """CLI 테스트를 자격증명 해석에서 격리한다. 실 사용자의 ``~/.config/kis-trader`` 를 읽지 않도록
    XDG 경로를 빈 임시 디렉터리로 돌리고, 상속된 ``KIS_*`` 환경변수를 지운다 -- 각 테스트가 필요한
    자격증명만 명시적으로 설정하게 한다(hermetic + 실 자격증명 미접촉)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))   # 영속 주문 저장소도 격리
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


# --- 스텁 클라이언트: 네트워크 없이 어떤 공개 메서드가 불렸는지만 기록 --------------

class _Handle:
    def __init__(self, log, code):
        self._log = log
        self._code = code

    def quote(self):
        self._log.append(("quote", self._code)); return "QUOTE"

    def buy(self, *, quantity, limit_price, division=None, stop_price=None,
            algo=None, algo_window=None):
        self._log.append(("buy", self._code, quantity, limit_price, division, stop_price,
                          algo, algo_window)); return "REPORT"

    def sell(self, *, quantity, limit_price, division=None, stop_price=None,
             algo=None, algo_window=None):
        self._log.append(("sell", self._code, quantity, limit_price, division, stop_price,
                          algo, algo_window)); return "REPORT"

    def reserve_buy(self, *, quantity, limit_price=None, client_order_id=None, **kwargs):
        self._log.append(("reserve_buy", self._code, quantity, limit_price, kwargs)); return "REPORT"

    def reserve_sell(self, *, quantity, limit_price=None, client_order_id=None, **kwargs):
        self._log.append(("reserve_sell", self._code, quantity, limit_price, kwargs)); return "REPORT"


class _BondHandle:
    def __init__(self, log, code):
        self._log = log
        self._code = code

    def buy(self, *, quantity, limit_price, client_order_id=None):
        self._log.append(("bond_buy", self._code, quantity, limit_price)); return "REPORT"

    def sell(self, *, quantity, limit_price, buy_date, buy_seq, client_order_id=None):
        self._log.append(("bond_sell", self._code, quantity, limit_price, buy_date, buy_seq))
        return "REPORT"


class _DomesticDerivHandle:
    def __init__(self, log, kind, code, right=None):
        self._log = log; self._kind = kind; self._code = code; self._right = right

    def buy(self, *, quantity, limit_price=None, division=None, night=False,
            client_order_id=None):
        self._log.append(("dom_deriv_buy", self._kind, self._code, self._right,
                          quantity, limit_price, division, night)); return "REPORT"

    def sell(self, *, quantity, limit_price=None, division=None, night=False,
             client_order_id=None):
        self._log.append(("dom_deriv_sell", self._kind, self._code, self._right,
                          quantity, limit_price, division, night)); return "REPORT"


class _OverseasDerivHandle:
    def __init__(self, log, kind, code):
        self._log = log; self._kind = kind; self._code = code

    def buy(self, *, quantity, limit_price=None, stop_price=None, client_order_id=None):
        self._log.append(("ovs_deriv_buy", self._kind, self._code, quantity,
                          limit_price, stop_price)); return "REPORT"

    def sell(self, *, quantity, limit_price=None, stop_price=None, client_order_id=None):
        self._log.append(("ovs_deriv_sell", self._kind, self._code, quantity,
                          limit_price, stop_price)); return "REPORT"


class _Ranking:
    def __init__(self, log):
        self._log = log

    def by_volume(self, *, metric="cumulative_trading_amount"):  # 라이브러리 기본값(CLI 는 재기술 안 함)
        self._log.append(("by_volume", metric)); return ["RV"]


class _Domestic:
    def __init__(self, log):
        self._log = log
        self.ranking = _Ranking(log)

    def stock(self, code):
        return _Handle(self._log, code)

    def bond(self, code):
        return _BondHandle(self._log, code)

    def futures(self, code):
        return _DomesticDerivHandle(self._log, "futures", code)

    def option(self, code, *, right=None):
        return _DomesticDerivHandle(self._log, "option", code, right=right)

    def search(self, query, *, market="all"):   # 라이브러리처럼 기본값을 쥔다(CLI 는 재기술 안 함)
        self._log.append(("search", query, market)); return ["HIT"]


class _Overseas:
    def __init__(self, log):
        self._log = log

    def stock(self, symbol, *, exchange=None):
        self._log.append(("ovs_stock", symbol, exchange)); return _Handle(self._log, symbol)

    def futures(self, code):
        return _OverseasDerivHandle(self._log, "futures", code)

    def option(self, code):
        return _OverseasDerivHandle(self._log, "option", code)


class _Orders:
    def __init__(self, log):
        self._log = log

    def reconcile(self, client_order_id):
        self._log.append(("reconcile", client_order_id))

    def modify(self, client_order_id, *, limit_price, quantity=None):
        self._log.append(("modify", client_order_id, limit_price, quantity)); return "REPORT"

    def cancel(self, client_order_id, *, quantity=None):
        self._log.append(("cancel", client_order_id, quantity)); return "REPORT"


class StubKis:
    def __init__(self, account=None, environment="paper", account_view=None):
        self.log: list = []
        self._account = account    # 세션이 해석한 계좌(주문 게이트가 kis._account 로 읽음)
        self.account = account_view  # 상품계좌 뷰(_stock_account 가 isinstance 로 검사)
        self.environment = environment
        self.domestic = _Domestic(self.log)
        self.overseas = _Overseas(self.log)
        self.orders = _Orders(self.log)


def _args(argv):
    return build_parser().parse_args(argv)


# --- 파서 배선 --------------------------------------------------------------

def test_parser_routes_stock_quote_to_handler():
    args = _args(["stock", "quote", "005930"])
    kis = StubKis()
    assert args.func(kis, args) == "QUOTE"
    assert kis.log == [("quote", "005930")]


def test_parser_leaves_profile_unset():
    args = _args(["search", "삼성전자"])
    assert args.profile is None    # 미지정 -> 라이브러리가 기본 프로필 해석(값은 config 테스트가 검증)
    assert args.fmt == "table"


def test_search_passes_market_and_returns_all_candidates():
    args = _args(["search", "삼성", "--market", "KOSDAQ"])
    kis = StubKis()
    assert args.func(kis, args) == ["HIT"]
    assert kis.log == [("search", "삼성", "KOSDAQ")]


def test_ranking_volume_omitting_metric_uses_library_default():
    # --metric 생략 시 CLI 가 기본값을 재기술하지 않고 라이브러리 기본(스텁 by_volume 의 기본)이 적용된다.
    args = _args(["ranking", "volume"])
    assert args.metric is None                 # 파서 기본은 None
    kis = StubKis()
    args.func(kis, args)
    assert kis.log == [("by_volume", "cumulative_trading_amount")]


def test_search_omitting_market_uses_library_default():
    # --market 를 생략하면 CLI 가 기본값을 재기술하지 않고 라이브러리 기본(스텁의 "all")이 적용된다.
    args = _args(["search", "삼성"])
    assert args.market is None                 # 파서 기본은 None(라이브러리 기본 재기술 안 함)
    kis = StubKis()
    args.func(kis, args)
    assert kis.log == [("search", "삼성", "all")]   # 스텁 기본값이 적용됨(CLI 가 안 덮음)


# --- 계좌 마스킹 ------------------------------------------------------------

def test_account_suffix_takes_last_four_digits():
    assert account_suffix("12345678-01") == "7801"
    assert account_suffix(None) == ""


# --- 출력 렌더링 ------------------------------------------------------------

@dataclass(frozen=True)
class _Row:
    symbol: str
    price: Decimal
    _raw: dict


def test_render_json_preserves_decimal_as_string_and_hides_raw():
    row = _Row(symbol="005930", price=Decimal(71500), _raw={"x": 1})
    out = render(row, fmt="json")
    assert '"price": "71500"' in out
    assert "_raw" not in out
    assert '"ok": true' in out


def test_render_json_includes_raw_only_when_asked():
    row = _Row(symbol="005930", price=Decimal(71500), _raw={"x": 1})
    assert "_raw" in render(row, fmt="json", include_raw=True)


def test_to_jsonable_serializes_nested_list_of_dataclasses():
    rows = [_Row("005930", Decimal(1), {}), _Row("000660", Decimal(2), {})]
    jsonable_rows = to_jsonable(rows)
    assert jsonable_rows == [{"symbol": "005930", "price": "1"}, {"symbol": "000660", "price": "2"}]


def test_render_json_include_raw_with_frozen_vendor_payload():
    # 실제 엔티티의 _raw 는 freeze_vendor_payload 가 만든 (중첩) MappingProxyType 이다.
    # 이를 json.dumps 에 그대로 넘기면 TypeError 라, to_jsonable 이 Mapping 을 재귀해 풀어야 한다.
    from kis_trader._internal._freeze import freeze_vendor_payload

    frozen = freeze_vendor_payload({"a": "1", "nested": {"b": "2"}})
    row = _Row(symbol="005930", price=Decimal(71500), _raw=frozen)
    out = render(row, fmt="json", include_raw=True)
    assert '"_raw"' in out
    assert '"b": "2"' in out          # 중첩 proxy 까지 값으로 풀렸다
    assert "mappingproxy" not in out  # repr 문자열로 새지 않았다


# --- 주문 안전 게이트 -------------------------------------------------------

def test_order_dry_run_shows_ticket_and_sends_nothing():
    args = _args(["order", "buy", "005930", "10", "--limit-price", "70000"])
    kis = StubKis()
    result = order.cmd_buy(kis, args, is_tty=False)
    assert result["side"] == "buy"
    assert result["limit_price"] == "70000"
    assert "note" in result
    assert kis.log == []  # 전송 없음


def test_order_execute_environment_mismatch_is_rejected():
    args = _args(["--profile", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "real"])
    with pytest.raises(CliConfigError):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_gate_uses_session_environment_not_profile_name():
    # 게이트는 세션(kis)이 해석한 실제 환경을 본다 -- 프로필 이름이 아니다.
    # 세션이 real 이면 프로필 이름이 paper 여도 --execute paper 는 막힌다.
    real_session = StubKis(account="12345678-01", environment="real")
    reject = _args(["--profile", "paper", "order", "buy", "005930", "10",
                    "--limit-price", "70000", "--execute", "paper", "--yes"])
    with pytest.raises(CliConfigError):
        order.cmd_buy(real_session, reject, is_tty=False)
    # 세션이 paper 이면 프로필 이름이 main 이어도 --execute paper 가 통과(전송)된다.
    paper_session = StubKis(environment="paper")
    allow = _args(["--profile", "main", "order", "buy", "005930", "10",
                   "--limit-price", "70000", "--execute", "paper", "--yes"])
    assert order.cmd_buy(paper_session, allow, is_tty=False) == "REPORT"


def test_order_noninteractive_requires_yes():
    args = _args(["--profile", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "paper"])
    with pytest.raises(CliConfigError):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_paper_noninteractive_with_yes_sends_once():
    args = _args(["--profile", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "paper", "--yes"])
    kis = StubKis()
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("buy", "005930", 10, "70000", None, None, None, None)]


_KST_TEST = timezone(timedelta(hours=9))


def _freeze_clock(monkeypatch, wall):
    """schedule 플래너와 order._twap_start 의 ``datetime.now`` 를 고정해 --start 해석·과거판정을 결정적으로."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall if tz is None else wall.astimezone(tz)
    monkeypatch.setattr("kis_trader.execution.schedule.datetime", _Frozen)
    monkeypatch.setattr("kis_trader.cli.commands.order.datetime", _Frozen)


def test_order_twap_dry_run_shows_schedule(monkeypatch):
    _freeze_clock(monkeypatch, datetime(2026, 8, 31, 9, 0, tzinfo=_KST_TEST))  # now 09:00 -> start 10:00 future
    dry = order.cmd_twap(StubKis(), _args(
        ["order", "twap", "005930", "--side", "buy", "--quantity", "100",
         "--over", "20m", "--slices", "4", "--start", "100000"]), is_tty=False)
    assert dry["symbol"] == "005930" and dry["side"] == "buy"
    assert [s["quantity"] for s in dry["slices"]] == [25, 25, 25, 25]
    assert len(dry["slices"]) == 4 and "note" in dry


def test_order_twap_rejects_session_spill(monkeypatch):
    # --start 152000 = 15:20, +30m/3 slices spills past 15:30 close -> planner raises.
    _freeze_clock(monkeypatch, datetime(2026, 8, 31, 9, 0, tzinfo=_KST_TEST))
    with pytest.raises(KISUsageError, match="정규장"):
        order.cmd_twap(StubKis(), _args(
            ["order", "twap", "005930", "--side", "buy", "--quantity", "9",
             "--over", "30m", "--slices", "3", "--start", "152000"]), is_tty=False)


def test_order_twap_rejects_past_start(monkeypatch):
    # now 12:00, --start 100000 (10:00) is in the past -> planner refuses the burst.
    _freeze_clock(monkeypatch, datetime(2026, 8, 31, 12, 0, tzinfo=_KST_TEST))
    with pytest.raises(KISUsageError, match="과거"):
        order.cmd_twap(StubKis(), _args(
            ["order", "twap", "005930", "--side", "buy", "--quantity", "9",
             "--over", "30m", "--slices", "3", "--start", "100000"]), is_tty=False)


@pytest.mark.parametrize("bad", ["240000", "96000", "10AA00", "236000"])
def test_order_twap_rejects_bad_start_hhmmss(monkeypatch, bad):
    _freeze_clock(monkeypatch, datetime(2026, 8, 31, 9, 0, tzinfo=_KST_TEST))
    with pytest.raises(CliConfigError):
        order.cmd_twap(StubKis(), _args(
            ["order", "twap", "005930", "--side", "buy", "--quantity", "9",
             "--over", "30m", "--slices", "3", "--start", bad]), is_tty=False)


def test_order_division_dry_run_shows_it_and_execute_forwards_it():
    # 최유리지정가(immediate_limit): 시장이 가격을 정하므로 limit_price 없이. dry-run 은 티켓에 노출.
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "005930", "10", "--division", "immediate_limit"]), is_tty=False)
    assert dry["division"] == "immediate_limit"
    # 전송 시 국내 핸들 buy 에 division 이 그대로 전달된다.
    kis = StubKis()
    order.cmd_buy(kis, _args(["--profile", "paper", "order", "buy", "005930", "10",
                              "--division", "immediate_limit", "--execute", "paper", "--yes"]),
                  is_tty=False)
    assert kis.log == [("buy", "005930", 10, None, "immediate_limit", None, None, None)]


def test_order_division_rejected_for_overseas():
    args = _args(["order", "buy", "AAPL", "10", "--venue", "overseas",
                  "--division", "immediate_limit"])
    with pytest.raises(CliConfigError):  # KRX 주문구분은 국내 전용
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_exchange_rejected_for_domestic_stock():
    # --exchange 는 해외 거래소코드 전용이라 국내 주식 주문엔 무의미 -- 조용히 무시하지 않고 거부한다.
    args = _args(["order", "buy", "005930", "10", "--limit-price", "70000", "--exchange", "NAS"])
    with pytest.raises(CliConfigError, match="exchange"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_domestic_stop_price_dry_run_and_execute_forwards_it():
    # 국내 주식 스톱지정가(ORD_DVSN 22): dry-run 은 티켓에 노출, 전송 시 stop_price 를 그대로 전달.
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "005930", "10", "--limit-price", "70000", "--stop-price", "69000"]),
        is_tty=False)
    assert dry["stop_price"] == "69000"
    kis = StubKis()
    order.cmd_buy(kis, _args(["--profile", "paper", "order", "buy", "005930", "10",
                              "--limit-price", "70000", "--stop-price", "69000",
                              "--execute", "paper", "--yes"]), is_tty=False)
    assert kis.log == [("buy", "005930", 10, "70000", None, "69000", None, None)]


def test_order_domestic_stop_price_requires_limit():
    with pytest.raises(CliConfigError, match="limit-price"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--stop-price", "69000"]), is_tty=False)


def test_order_stop_price_conflicts_with_division():
    with pytest.raises(CliConfigError, match="division"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--limit-price", "70000", "--stop-price", "69000",
             "--division", "immediate_limit"]), is_tty=False)


def test_order_overseas_stock_stop_price_rejected():
    # --stop-price 는 해외 파생 또는 국내 주식 전용 -- 해외 주식은 어느 쪽도 아니라 거부.
    with pytest.raises(CliConfigError, match="stop-price"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--stop-price", "99"]),
            is_tty=False)


def test_order_reserve_dry_run_shows_reserve_and_end_date():
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "005930", "10", "--limit-price", "70000",
         "--reserve", "--end-date", "20240131"]), is_tty=False)
    assert dry["reserve"] is True
    assert dry["end_date"] == "20240131"


def test_order_reserve_execute_routes_to_reserve_buy():
    kis = StubKis(account="12345678-01", environment="real")
    order.cmd_buy(kis, _args(["--profile", "real", "order", "buy", "005930", "10",
                              "--limit-price", "70000", "--reserve", "--end-date", "20240131",
                              "--execute", "real", "--yes", "--confirm-account", "7801"]),
                  is_tty=False)
    assert kis.log[-1] == ("reserve_buy", "005930", 10, "70000", {"end_date": "20240131"})


def test_order_reserve_sell_execute_routes_to_reserve_sell():
    # --end-date 미지정: 라이브러리 기본을 재기술하지 않고 end_date 를 전혀 넘기지 않는다.
    kis = StubKis(account="12345678-01", environment="real")
    order.cmd_sell(kis, _args(["--profile", "real", "order", "sell", "005930", "10",
                               "--limit-price", "70000", "--reserve",
                               "--execute", "real", "--yes", "--confirm-account", "7801"]),
                   is_tty=False)
    assert kis.log[-1] == ("reserve_sell", "005930", 10, "70000", {})


# --- 해외 예약주문 CLI: place(발주, 국내와 달리 모의 허용) --------------------

def test_order_overseas_reserve_dry_run_shows_reserve_currency_exchange():
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "00700", "100", "--venue", "overseas", "--reserve",
         "--limit-price", "350", "--exchange", "HKS", "--currency", "HKD"]), is_tty=False)
    assert dry["reserve"] is True
    assert dry["currency"] == "HKD" and dry["exchange"] == "HKS"


def test_order_overseas_reserve_execute_allows_paper_and_forwards_currency():
    # 국내 예약과 달리 해외 예약 발주는 모의(paper) 허용.
    kis = StubKis(account="12345678-01", environment="paper")
    order.cmd_buy(kis, _args(
        ["--profile", "paper", "order", "buy", "00700", "100", "--venue", "overseas",
         "--reserve", "--limit-price", "350", "--exchange", "HKS", "--currency", "HKD",
         "--execute", "paper", "--yes"]), is_tty=False)
    # 거래소가 핸들 조회로 전달되고, 통화가 발주로 전달되는 전 과정을 검증한다.
    assert kis.log == [("ovs_stock", "00700", "HKS"),
                       ("reserve_buy", "00700", 100, "350", {"currency": "HKD"})]


def test_order_overseas_reserve_defers_currency_to_library():
    # --currency 미지정: 라이브러리 기본(홍콩=HKD)을 재기술하지 않고 전혀 넘기지 않는다.
    kis = StubKis(account="12345678-01", environment="paper")
    order.cmd_sell(kis, _args(
        ["--profile", "paper", "order", "sell", "AAPL", "10", "--venue", "overseas",
         "--reserve", "--limit-price", "190", "--exchange", "NAS",
         "--execute", "paper", "--yes"]), is_tty=False)
    assert kis.log == [("ovs_stock", "AAPL", "NAS"),
                       ("reserve_sell", "AAPL", 10, "190", {})]


def test_order_overseas_reserve_requires_limit_price():
    with pytest.raises(CliConfigError, match="지정가"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--reserve",
             "--exchange", "NAS"]), is_tty=False)


def test_order_overseas_reserve_rejects_end_date():
    with pytest.raises(CliConfigError, match="end-date"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--reserve",
             "--limit-price", "190", "--end-date", "20240131"]), is_tty=False)


def test_order_domestic_reserve_rejects_currency():
    with pytest.raises(CliConfigError, match="--currency"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--limit-price", "70000", "--reserve",
             "--currency", "HKD"]), is_tty=False)


def test_order_currency_requires_reserve():
    with pytest.raises(CliConfigError, match="--currency"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "00700", "100", "--venue", "overseas",
             "--limit-price", "350", "--currency", "HKD"]), is_tty=False)


def test_reserved_currency_taxonomy_exported():
    from typing import get_args

    from kis_trader import ReservedCurrency
    assert get_args(ReservedCurrency) == ("HKD", "CNY", "USD")


# --- 미국주식 algo(TWAP/VWAP) 분할주문 CLI ----------------------------------

def test_algo_strategy_taxonomy_exported():
    from typing import get_args

    from kis_trader import AlgoStrategy
    assert get_args(AlgoStrategy) == ("twap", "vwap")


def test_order_overseas_algo_dry_run_shows_algo_fields():
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "AAPL", "10", "--venue", "overseas", "--limit-price", "150",
         "--algo", "twap", "--algo-start", "093000", "--algo-end", "160000"]), is_tty=False)
    assert dry["algo"] == "twap"
    assert dry["algo_start"] == "093000" and dry["algo_end"] == "160000"


def test_order_overseas_algo_execute_forwards_algo_and_window():
    kis = StubKis(account="12345678-01", environment="real")
    order.cmd_buy(kis, _args(
        ["--profile", "real", "order", "buy", "AAPL", "10", "--venue", "overseas",
         "--exchange", "NAS", "--limit-price", "150", "--algo", "twap", "--algo-start", "093000",
         "--algo-end", "160000", "--execute", "real", "--yes", "--confirm-account", "7801"]),
        is_tty=False)
    assert kis.log == [("ovs_stock", "AAPL", "NAS"),
                       ("buy", "AAPL", 10, "150", None, None, "twap", ("093000", "160000"))]


def test_order_overseas_algo_no_window_forwards_algo_only():
    # 시간창 미지정: algo 만 넘기고 algo_window 는 라이브러리 기본(정규장 종료)에 맡긴다.
    kis = StubKis(account="12345678-01", environment="real")
    order.cmd_sell(kis, _args(
        ["--profile", "real", "order", "sell", "AAPL", "10", "--venue", "overseas",
         "--limit-price", "150", "--algo", "vwap",
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("sell", "AAPL", 10, "150", None, None, "vwap", None)


def test_order_overseas_reserve_algo_forwards_algo():
    kis = StubKis(account="12345678-01", environment="real")
    order.cmd_buy(kis, _args(
        ["--profile", "real", "order", "buy", "AAPL", "10", "--venue", "overseas", "--reserve",
         "--limit-price", "150", "--exchange", "NAS", "--algo", "twap",
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("reserve_buy", "AAPL", 10, "150", {"algo": "twap"})


def test_order_algo_rejected_for_domestic():
    with pytest.raises(CliConfigError, match="해외 주식"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--limit-price", "70000", "--algo", "twap"]),
            is_tty=False)


def test_order_algo_rejects_paper_execute():
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_buy(StubKis(account="12345678-01", environment="paper"), _args(
            ["--profile", "paper", "order", "buy", "AAPL", "10", "--venue", "overseas",
             "--limit-price", "150", "--algo", "twap", "--execute", "paper", "--yes"]),
            is_tty=False)


def test_order_algo_window_requires_algo():
    with pytest.raises(CliConfigError, match="--algo"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--limit-price", "150",
             "--algo-start", "093000", "--algo-end", "160000"]), is_tty=False)


def test_order_algo_half_window_rejected():
    with pytest.raises(CliConfigError, match="함께"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--limit-price", "150",
             "--algo", "twap", "--algo-start", "093000"]), is_tty=False)


def test_order_reserve_algo_rejects_window():
    with pytest.raises(CliConfigError, match="정규장 종료"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "AAPL", "10", "--venue", "overseas", "--reserve",
             "--limit-price", "150", "--algo", "twap", "--algo-start", "093000",
             "--algo-end", "160000"]), is_tty=False)


@pytest.mark.parametrize("extra,match", [
    (["--division", "immediate_limit"], "division"),
    (["--stop-price", "69000"], "stop-price"),
    (["--night"], "night"),
    (["--asset", "bond"], "주식 예약주문"),
])
def test_order_reserve_rejects_incompatible_flags(extra, match):
    with pytest.raises(CliConfigError, match=match):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--limit-price", "70000", "--reserve", *extra]),
            is_tty=False)


def test_order_end_date_requires_reserve():
    with pytest.raises(CliConfigError, match="--reserve"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "10", "--limit-price", "70000", "--end-date", "20240131"]),
            is_tty=False)


def test_order_real_noninteractive_needs_matching_confirm_account():
    base = ["--profile", "main", "order", "buy",
            "005930", "10", "--limit-price", "70000", "--execute", "real", "--yes"]
    kis = StubKis(account="12345678-01", environment="real")
    with pytest.raises(CliConfigError):
        order.cmd_buy(kis, _args(base + ["--confirm-account", "0000"]), is_tty=False)
    assert order.cmd_buy(kis, _args(base + ["--confirm-account", "7801"]), is_tty=False) == "REPORT"


def test_order_interactive_real_confirms_by_account_suffix():
    args = _args(["--profile", "main", "order", "buy",
                  "005930", "10", "--limit-price", "70000", "--execute", "real"])
    kis = StubKis(account="12345678-01", environment="real")
    with pytest.raises(CliAborted):
        order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "0000")
    assert order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "7801") == "REPORT"


def test_order_real_fails_closed_when_account_unresolved():
    # 계좌 미해석(suffix "") 이면 빈 확인이 통과해선 안 된다 -- 실주문 우회 회귀 방지.
    noninteractive = _args(["--profile", "main", "order", "buy", "005930", "10",
                            "--limit-price", "70000", "--execute", "real", "--yes"])
    kis = StubKis()
    with pytest.raises(CliConfigError):
        order.cmd_buy(kis, noninteractive, is_tty=False)  # --confirm-account 생략
    with pytest.raises(CliConfigError):
        order.cmd_buy(kis, noninteractive, is_tty=True, prompt=lambda _p: "")  # 빈 Enter
    assert kis.log == []  # 어느 경로로도 전송 없음


def test_order_paper_interactive_rejects_non_affirmative():
    args = _args(["--profile", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "paper"])
    kis = StubKis()
    with pytest.raises(CliAborted):
        order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "")
    assert order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "y") == "REPORT"
    assert kis.log == [("buy", "005930", 10, "70000", None, None, None, None)]


def test_order_modify_dry_run_then_executes_once():
    modify_base_argv = ["--profile", "paper", "order", "modify", "abc-123", "--limit-price", "70500"]
    kis = StubKis()
    plan = order.cmd_modify(kis, _args(modify_base_argv), is_tty=False)
    assert "note" in plan and kis.log == []
    args = _args(modify_base_argv + ["--execute", "paper", "--yes"])
    assert order.cmd_modify(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("modify", "abc-123", "70500", None)]


def test_order_cancel_dry_run_then_executes_once():
    cancel_base_argv = ["--profile", "paper", "order", "cancel", "abc-123"]
    kis = StubKis()
    plan = order.cmd_cancel(kis, _args(cancel_base_argv), is_tty=False)
    assert "note" in plan and kis.log == []
    args = _args(cancel_base_argv + ["--execute", "paper", "--yes"])
    assert order.cmd_cancel(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("cancel", "abc-123", None)]


def test_order_cancel_reserved_dry_run_shows_sequence_without_calling(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    dry = order.cmd_cancel_reserved(kis, _args(
        ["order", "cancel-reserved", "SEQ7", "--order-date", "20240131"]), is_tty=False)
    assert dry["sequence"] == "SEQ7"
    assert dry["order_date"] == "20240131"
    assert kis.log == []


def test_order_cancel_reserved_execute_routes_to_cancel(monkeypatch):
    kis = StubKis(account="12345678-01", environment="real")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    order.cmd_cancel_reserved(kis, _args(
        ["--profile", "real", "order", "cancel-reserved", "SEQ7", "--order-date", "20240131",
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("cancel_reserved_order", "SEQ7", {"order_date": "20240131"})


def test_order_cancel_reserved_overseas_dry_run_shows_receipt_date(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    dry = order.cmd_cancel_reserved(kis, _args(
        ["order", "cancel-reserved", "US123", "--venue", "overseas",
         "--receipt-date", "20240131"]), is_tty=False)
    assert dry["reserved_order_id"] == "US123" and dry["venue"] == "overseas"
    assert dry["receipt_date"] == "20240131" and "note" in dry and kis.log == []


def test_order_cancel_reserved_overseas_execute_allows_paper_and_routes(monkeypatch):
    # 국내 예약 취소와 달리 해외(미국) 예약 취소는 모의(paper) 허용.
    kis = StubKis(account="12345678-01", environment="paper")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    result = order.cmd_cancel_reserved(kis, _args(
        ["--profile", "paper", "order", "cancel-reserved", "US123", "--venue", "overseas",
         "--receipt-date", "20240131", "--execute", "paper", "--yes"]), is_tty=False)
    assert kis.log[-1] == ("ovs_cancel_reserved_order", "US123", "20240131")
    assert result["cancelled"] is True and result["venue"] == "overseas"
    assert result["reserved_order_id"] == "US123" and result["receipt_date"] == "20240131"


def test_order_cancel_reserved_overseas_requires_receipt_date(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    with pytest.raises(CliConfigError, match="--receipt-date"):
        order.cmd_cancel_reserved(kis, _args(
            ["order", "cancel-reserved", "US123", "--venue", "overseas"]), is_tty=False)
    assert kis.log == []


def test_order_cancel_reserved_overseas_rejects_order_date(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    with pytest.raises(CliConfigError, match="--order-date"):
        order.cmd_cancel_reserved(kis, _args(
            ["order", "cancel-reserved", "US123", "--venue", "overseas",
             "--receipt-date", "20240131", "--order-date", "20240131"]), is_tty=False)
    assert kis.log == []


def test_order_cancel_reserved_domestic_rejects_receipt_date(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    with pytest.raises(CliConfigError, match="--receipt-date"):
        order.cmd_cancel_reserved(kis, _args(
            ["order", "cancel-reserved", "SEQ7", "--receipt-date", "20240131"]), is_tty=False)
    assert kis.log == []


def test_order_reserve_rejects_paper():
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_buy(StubKis(), _args(
            ["--profile", "paper", "order", "buy", "005930", "10", "--limit-price", "70000",
             "--reserve", "--execute", "paper", "--yes"]), is_tty=False)


def test_order_cancel_reserved_rejects_paper(monkeypatch):
    kis = StubKis(environment="paper")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_cancel_reserved(kis, _args(
            ["--profile", "paper", "order", "cancel-reserved", "SEQ7",
             "--execute", "paper", "--yes"]), is_tty=False)
    assert kis.log == []


def test_order_modify_reserved_dry_run_shows_fields_without_calling(monkeypatch):
    kis = StubKis()
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    dry = order.cmd_modify_reserved(kis, _args(
        ["order", "modify-reserved", "SEQ7", "--symbol", "005930", "--side", "buy",
         "--quantity", "10", "--limit-price", "70000"]), is_tty=False)
    assert dry["sequence"] == "SEQ7" and dry["symbol"] == "005930"
    assert dry["side"] == "buy" and dry["quantity"] == 10 and dry["limit_price"] == "70000"
    assert "note" in dry
    # 단가를 줬으므로 시장가 경고는 빠지고, 순번 재배정 안내는 항상 실린다.
    assert "시장가" not in dry["caution"]
    assert "예약 순번이 재배정될 수 있습니다" in dry["caution"]
    assert kis.log == []


def test_order_modify_reserved_execute_routes_and_forwards(monkeypatch):
    kis = StubKis(account="12345678-01", environment="real")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    result = order.cmd_modify_reserved(kis, _args(
        ["--profile", "real", "order", "modify-reserved", "SEQ7", "--symbol", "005930",
         "--side", "buy", "--quantity", "10", "--limit-price", "70000", "--end-date", "20240131",
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("modify_reserved_order", "SEQ7", "005930", "buy", 10,
                           {"limit_price": "70000", "end_date": "20240131"})
    assert result["modified"] is True
    # 실송신 영수증도 순번 재배정 안내를 싣는다(단가 줬으니 시장가 경고는 없음).
    assert "예약 순번이 재배정될 수 있습니다" in result["caution"]
    assert "시장가" not in result["caution"]


def test_order_modify_reserved_defers_optional_price_to_library(monkeypatch):
    kis = StubKis(account="12345678-01", environment="real")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    result = order.cmd_modify_reserved(kis, _args(
        ["--profile", "real", "order", "modify-reserved", "SEQ7", "--symbol", "005930",
         "--side", "sell", "--quantity", "5",
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("modify_reserved_order", "SEQ7", "005930", "sell", 5, {})
    # 단가를 생략한 실송신이므로 영수증이 시장가 전환 경고와 순번 재배정 안내를 모두 싣는다.
    assert "시장가로 바뀝니다" in result["caution"]
    assert "예약 순번이 재배정될 수 있습니다" in result["caution"]


def test_order_modify_reserved_rejects_paper(monkeypatch):
    kis = StubKis(environment="paper")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_modify_reserved(kis, _args(
            ["--profile", "paper", "order", "modify-reserved", "SEQ7", "--symbol", "005930",
             "--side", "buy", "--quantity", "10", "--execute", "paper", "--yes"]), is_tty=False)
    assert kis.log == []


def test_order_modify_reserved_requires_respecify_fields():
    for missing in (
        ["order", "modify-reserved", "SEQ7", "--side", "buy", "--quantity", "10"],
        ["order", "modify-reserved", "SEQ7", "--symbol", "005930", "--quantity", "10"],
        ["order", "modify-reserved", "SEQ7", "--symbol", "005930", "--side", "buy"],
    ):
        with pytest.raises(SystemExit):
            _args(missing)


def test_order_modify_reserved_rejects_invalid_side():
    with pytest.raises(SystemExit):
        _args(["order", "modify-reserved", "SEQ7", "--symbol", "005930",
               "--side", "hold", "--quantity", "10"])


@pytest.mark.parametrize(
    ("optional_argv", "expected_kwargs"),
    [
        (["--limit-price", "70000"], {"limit_price": "70000"}),
        (["--end-date", "20240131"], {"end_date": "20240131"}),
        (["--order-date", "20240130"], {"order_date": "20240130"}),
    ],
)
def test_order_modify_reserved_forwards_each_optional_independently(
    monkeypatch, optional_argv, expected_kwargs
):
    kis = StubKis(account="12345678-01", environment="real")
    monkeypatch.setattr(order, "_stock_account", lambda k: _StubStockView(k.log))
    order.cmd_modify_reserved(kis, _args(
        ["--profile", "real", "order", "modify-reserved", "SEQ7", "--symbol", "005930",
         "--side", "buy", "--quantity", "10", *optional_argv,
         "--execute", "real", "--yes", "--confirm-account", "7801"]), is_tty=False)
    assert kis.log[-1] == ("modify_reserved_order", "SEQ7", "005930", "buy", 10, expected_kwargs)


def test_order_modify_reserved_rejects_non_stock_account():
    # 실제 _stock_account 가 돌아 kis.account 가 StockAccount 가 아니면 거부한다(monkeypatch 없음).
    kis = StubKis(account="12345678-03", environment="real", account_view=object())
    with pytest.raises(CliConfigError):
        order.cmd_modify_reserved(kis, _args(
            ["--profile", "real", "order", "modify-reserved", "SEQ7", "--symbol", "005930",
             "--side", "buy", "--quantity", "10",
             "--execute", "real", "--yes", "--confirm-account", "7803"]), is_tty=False)
    assert kis.log == []


def test_order_cancel_reserved_rejects_non_stock_account():
    # 실제 _stock_account 가 돌아 kis.account 가 StockAccount 가 아니면 거부한다(monkeypatch 없음).
    kis = StubKis(account="12345678-03", environment="real", account_view=object())
    with pytest.raises(CliConfigError):
        order.cmd_cancel_reserved(kis, _args(
            ["--profile", "real", "order", "cancel-reserved", "SEQ7",
             "--execute", "real", "--yes", "--confirm-account", "7803"]), is_tty=False)
    assert kis.log == []


def test_order_reconcile_never_resends():
    args = _args(["order", "reconcile", "abc-123"])
    kis = StubKis()
    assert args.func(kis, args) is None
    assert kis.log == [("reconcile", "abc-123")]


# --- 오류 번역 --------------------------------------------------------------

@pytest.mark.parametrize("exc, exit_code, outcome, reconcile_required, retryable", [
    (OrderTimeoutError("timeout", client_order_id="abc-123"), 7, "unknown", True, False),
    (OrderError("accepted but no ODNO"), 7, "unknown", True, False),  # 결과불명 -> reconcile, 재전송 금지
    (OrderRejectedError("rejected"), 6, "rejected", False, False),
    (PreTradeRiskError("risk"), 4, "not_sent", False, False),
    (AccountNotOrderableError("irp"), 4, "not_sent", False, False),
    (KISUsageError("bad use"), 4, "not_sent", False, False),
    (KISAuthError("bad key"), 3, "config", False, False),
    (KISRateLimitError("slow down"), 5, "failed", False, True),
    (KISError("boom"), 5, "failed", False, False),
    (CliConfigError("no creds"), 3, "config", False, False),
    (CliAborted("user said no"), 3, "not_sent", False, False),
])
def test_translate_maps_known_exceptions_to_translated_fields(
    exc, exit_code, outcome, reconcile_required, retryable
):
    translated = translate(exc)
    assert (translated.exit_code, translated.outcome, translated.reconcile_required,
            translated.retryable) == (exit_code, outcome, reconcile_required, retryable)


def test_translate_timeout_message_carries_client_order_id():
    # 타임아웃 번역 메시지에 client_order_id 를 실어 사용자가 바로 재조회할 수 있어야 한다
    # (예전엔 id 를 버려 어느 주문을 reconcile 해야 하는지 알 수 없었다).
    translated = translate(OrderTimeoutError("timeout", client_order_id="abc-123"))
    assert "abc-123" in translated.message


def test_translate_reraises_unknown_exception():
    with pytest.raises(ValueError):
        translate(ValueError("not a KIS error"))


# --- main() 종단 --------------------------------------------------------------

def test_main_order_dry_run_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "build_client", lambda args: StubKis())
    code = cli_main.main(["order", "buy", "005930", "10", "--limit-price", "70000"])
    assert code == 0
    assert "비권위적" in capsys.readouterr().out


def test_main_config_error_exits_three(monkeypatch, capsys):
    def _boom(args):
        raise CliConfigError("자격증명 없음")
    monkeypatch.setattr(cli_main, "build_client", _boom)
    code = cli_main.main(["stock", "quote", "005930"])
    assert code == 3
    assert "오류" in capsys.readouterr().err


def test_main_order_timeout_exits_seven_with_reconcile(monkeypatch, capsys):
    class _TimeoutHandle:
        def buy(self, *, quantity, limit_price, division=None, stop_price=None):
            raise OrderTimeoutError("전송 시간초과", client_order_id="cid-1")

    class _Domestic:
        def stock(self, code):
            return _TimeoutHandle()

    class _Kis:
        _account = None
        environment = "paper"
        domestic = _Domestic()

    monkeypatch.setattr(cli_main, "build_client", lambda args: _Kis())
    code = cli_main.main(["--profile", "paper", "order", "buy", "005930", "10",
                          "--limit-price", "70000", "--execute", "paper", "--yes", "--format", "json"])
    assert code == 7  # 결과 불명 -> reconcile
    err = capsys.readouterr().err
    assert "reconcile" in err and "unknown" in err


def test_render_table_covers_list_single_dict_and_empty():
    rows = [_Row("005930", Decimal(71500), {}), _Row("000660", Decimal(120000), {})]
    table = render(rows, fmt="table")
    assert "symbol" in table and "005930" in table and "000660" in table
    single = render(_Row("005930", Decimal(71500), {}), fmt="table")
    assert "symbol" in single and "005930" in single
    plan = render({"symbol": "005930", "note": "dry-run"}, fmt="table")
    assert "symbol" in plan and "note" in plan
    assert render([], fmt="table") == "(빈 결과)"
    no_header = render(rows, fmt="table", no_header=True)
    assert "symbol" not in no_header.splitlines()[0]  # 머리글 없음


@dataclass(frozen=True)
class _NamedRow:
    name: str
    tag: str
    _raw: dict


@dataclass(frozen=True)
class _Level:
    price: int
    quantity: int
    _raw: dict


@dataclass(frozen=True)
class _Book:
    symbol: str
    bids: list
    _raw: dict


def test_render_table_expands_nested_record_list_as_subtable():
    book = _Book("005930", [_Level(274000, 100, {}), _Level(273500, 50, {})], {})
    out = render(book, fmt="table")
    assert "bids" in out                              # 필드명이 머리로
    assert "price" in out and "quantity" in out       # 하위 표 헤더
    assert "274,000" in out and "273,500" in out      # 각 호가 행(천단위 콤마)
    assert "[{" not in out                            # JSON 블롭으로 접지 않음


def test_render_table_commas_and_right_align_in_key_value_but_json_plain():
    @dataclass(frozen=True)
    class _Balance:
        currency: str
        deposit: Decimal
        net_asset: Decimal
        _raw: dict

    obj = _Balance("KRW", Decimal(399684), Decimal(127776156), {})
    table = render(obj, fmt="table")
    assert "127,776,156" in table and "399,684" in table       # 표엔 천단위 콤마
    # 숫자는 공통 폭으로 우측정렬 -> 두 금액 줄의 오른끝(표시폭)이 같다
    deposit_line = next(line for line in table.splitlines() if line.startswith("deposit"))
    net_line = next(line for line in table.splitlines() if line.startswith("net_asset"))
    assert _display_width(deposit_line) == _display_width(net_line)
    # JSON 은 콤마 없이(기계 파싱 가능)
    j = render(obj, fmt="json")
    assert "127776156" in j and "127,776,156" not in j


def test_render_table_right_aligns_numeric_columns_only():
    @dataclass(frozen=True)
    class _R:
        name: str
        qty: int
        _raw: dict

    out = render([_R("삼성", 100, {}), _R("SK", 110873, {})], fmt="table")
    assert "   qty" in out       # 숫자 헤더도 열 정렬 따라 우측
    assert "   100" in out       # 작은 숫자는 앞을 채워 우측정렬(폭 6)
    assert any(line.startswith("삼성") for line in out.splitlines())  # 텍스트는 좌측정렬


def test_render_table_datetime_is_kst_seconds_but_json_keeps_iso():
    from datetime import datetime, timedelta, timezone

    @dataclass(frozen=True)
    class _Stamped:
        as_of: datetime
        _raw: dict

    dt = datetime(2026, 8, 15, 19, 57, 45, 694957, tzinfo=timezone(timedelta(hours=9)))
    table = render(_Stamped(dt, {}), fmt="table")
    assert "2026-08-15 19:57:45 KST" in table       # 공백·초단위·KST 라벨
    assert "T19" not in table and "+09:00" not in table
    assert "2026-08-15T19:57:45.694957+09:00" in render(_Stamped(dt, {}), fmt="json")  # JSON은 ISO


def test_order_book_table_is_ladder_and_json_is_raw():
    from datetime import datetime, timedelta, timezone

    from kis_trader.cli.commands import stock as stock_cmd
    from kis_trader.order_book import OrderBook, PriceLevel

    book = OrderBook(
        symbol="005930", market="KRX",
        bids=[PriceLevel(Decimal(274000), 100), PriceLevel(Decimal(273500), 50)],
        asks=[PriceLevel(Decimal(274500), 80), PriceLevel(Decimal(275000), 60)],
        total_bid_quantity=150, total_ask_quantity=140,
        as_of=datetime(2026, 8, 15, 20, 0, 0, tzinfo=timezone(timedelta(hours=9))),
    )

    class _BookHandle:
        def order_book(self):
            return book

    class _BookNamespace:
        def stock(self, code):
            return _BookHandle()

    class _Kis:
        def __init__(self):
            self.domestic = _BookNamespace()

    # table: 센터 사다리 -- 호가가 위->아래 내림차순(매도 높은가격부터 -> 매수), 스프레드 가운데
    result = stock_cmd.cmd_book(_Kis(), _args(["stock", "book", "005930"]))
    assert isinstance(result, dict)
    rows = result["order_book"]
    assert [str(row["price"]) for row in rows] == ["275000", "274500", "274000", "273500"]
    assert rows[0]["ask_size"] == 60 and rows[0]["bid_size"] is None      # 매도 행
    assert rows[-1]["bid_size"] == 50 and rows[-1]["ask_size"] is None     # 매수 행
    # json: 구조화된 원본 OrderBook 을 그대로(각 변 최우선 먼저)
    assert stock_cmd.cmd_book(_Kis(), _args(["--format", "json", "stock", "book", "005930"])) is book


def test_render_table_builds_grid_from_list_of_dicts():
    out = render([{"a": 1, "b": "x"}, {"a": 22, "b": "y"}], fmt="table")
    assert "a" in out and "b" in out          # dict 키가 헤더
    assert "22" in out and "y" in out          # 값이 격자로


def test_render_table_hides_all_empty_columns():
    # 전 행이 비어있는 열(gap)은 숨기고, 값 있는 열만 남긴다.
    out = render([{"name": "삼성", "gap": None, "qty": 1},
                  {"name": "SK", "gap": None, "qty": 2}], fmt="table")
    assert "gap" not in out                    # 전부 빈 열은 숨김
    assert "name" in out and "qty" in out       # 값 있는 열은 유지


def test_display_width_counts_hangul_as_two_cells():
    assert _display_width("삼성전자") == 8  # 한글 4자 x 2칸
    assert _display_width("AAPL") == 4
    assert _pad("삼성전자", 10) == "삼성전자  "  # 표시폭 8 -> 2칸 채움


def test_table_aligns_columns_across_cjk_and_ascii_rows():
    rows = [_NamedRow("삼성전자", "A", {}), _NamedRow("SK하이닉스", "B", {}), _NamedRow("AAPL", "C", {})]
    lines = render(rows, fmt="table", no_header=True).splitlines()
    # 한글/ASCII 폭이 섞여도 모든 행의 표시폭이 같으면 tag 열이 세로로 맞은 것
    assert len({_display_width(line) for line in lines}) == 1


# --- build_client: 프로필 -> 세션(계좌·환경 1회 해석) --------------------------

def test_build_client_resolves_account_and_environment_from_profile(monkeypatch):
    from kis_trader.cli.context import build_client
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "s")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT", "12345678-01")
    monkeypatch.setenv("KIS_PAPER_ENVIRONMENT", "paper")   # 환경은 프로필에 저장된 값이 정한다
    kis = build_client(_args(["--profile", "paper", "stock", "quote", "005930"]))
    assert kis._account == "12345678-01"
    assert kis.environment == "paper"


def test_build_client_uses_default_profile_env_when_profile_unset(monkeypatch):
    from kis_trader.cli.context import build_client
    monkeypatch.setenv("KIS_DEFAULT_PROFILE", "paper")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "s")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT", "12345678-01")
    monkeypatch.setenv("KIS_PAPER_ENVIRONMENT", "paper")
    kis = build_client(_args(["stock", "quote", "005930"]))   # --profile 없음 -> 기본 프로필 env
    assert kis.environment == "paper"


def test_build_client_missing_credentials_raises_config_error():
    from kis_trader.cli.context import build_client
    with pytest.raises(CliConfigError):  # KIS_* 없음(격리 픽스처) -> exit 3
        build_client(_args(["--profile", "paper", "stock", "quote", "005930"]))


# --- 채권 lot 목록: kis account balance --asset bond -----------------------

class _StubBonds:
    def __init__(self, log):
        self._log = log

    def balance(self):
        self._log.append(("bonds_balance",)); return ["LOT"]

    def open_orders(self, order_date):
        self._log.append(("bonds_open_orders", order_date)); return ["OPEN"]

    def fills(self, *, start, end, **kwargs):
        self._log.append(("bonds_fills", start, end, kwargs))
        return ["FILLS"]


class _StubDomesticAccount:
    def __init__(self, log):
        self._log = log
        self.bonds = _StubBonds(log)

    def balance(self):
        self._log.append(("dom_balance",)); return "STOCK_BAL"

    def positions(self):
        self._log.append(("dom_positions",)); return ["DOM_POS"]

    def open_orders(self):
        self._log.append(("dom_open_orders",)); return "STOCK_OPEN"

    def fills(self, *, start, end, **kwargs):
        self._log.append(("stock_fills", start, end, kwargs)); return ["STOCK_FILLS"]

    def trade_profits(self, *, start, end, **kwargs):
        self._log.append(("trade_profits", start, end, kwargs)); return ["TRADE_PROFITS"]

    def daily_profits(self, *, start, end, **kwargs):
        self._log.append(("daily_profits", start, end, kwargs)); return ["DAILY_PROFITS"]

    def reserved_orders(self, *, start, end, **kwargs):
        self._log.append(("reserved_orders", start, end, kwargs)); return ["RESERVED"]

    def cancel_reserved_order(self, sequence, **kwargs):
        self._log.append(("cancel_reserved_order", sequence, kwargs))

    def modify_reserved_order(self, sequence, *, symbol, side, quantity, **kwargs):
        self._log.append(("modify_reserved_order", sequence, symbol, side, quantity, kwargs))


class _StubOverseasAccount:
    def __init__(self, log):
        self._log = log

    def balance(self, *, market):
        self._log.append(("ovs_balance", market)); return "OVS_BAL"

    def positions(self, *, market=None):
        self._log.append(("ovs_positions", market)); return ["OVS_POS"]

    def open_orders(self, *, market=None):
        self._log.append(("ovs_open_orders", market)); return ["OVS_OPEN"]

    def reserved_orders(self, *, start, end):
        self._log.append(("ovs_reserved_orders", start, end)); return ["OVS_RESERVED"]

    def cancel_reserved_order(self, reserved_order_id, *, receipt_date):
        self._log.append(("ovs_cancel_reserved_order", reserved_order_id, receipt_date))

    def period_profit(self, *, start, end, **kwargs):
        self._log.append(("ovs_period_profit", start, end, kwargs)); return "OVS_PROFIT"

    def transactions(self, *, start, end, **kwargs):
        self._log.append(("ovs_transactions", start, end, kwargs)); return ["OVS_TXN"]

    def present_balance(self):
        self._log.append(("ovs_present_balance",)); return "OVS_PRESENT"

    def settlement_balance(self, *, basis_date):
        self._log.append(("ovs_settlement_balance", basis_date)); return "OVS_SETTLE"

    def foreign_margin(self):
        self._log.append(("ovs_foreign_margin",)); return ["OVS_FMARGIN"]


class _StubStockView(StockAccount):
    def __init__(self, log):  # StockAccount.__init__ 우회 -- isinstance 만 통과시키고 뷰는 스텁
        self._domestic = _StubDomesticAccount(log)  # domestic/overseas 는 property -> 백킹필드 세팅
        self._overseas = _StubOverseasAccount(log)


class _StubDeriv03(DomesticDerivativesAccount):
    """국내선물옵션(03) 계좌 뷰 스텁 -- isinstance 통과 + 호출 로깅."""

    def __init__(self, log):
        self._log = log

    def balance(self):
        self._log.append(("d03_balance",)); return "D03_BAL"

    def open_orders(self, *args, **kwargs):  # 실제 전달 인자를 그대로 캡처 -- CLI 의 defer 증명용
        self._log.append(("d03_open_orders", args, kwargs)); return ["D03_OPEN"]

    def base_date_fills(self, *, order_date, start_time="000000", end_time="240000"):
        self._log.append(("d03_base_date_fills", order_date)); return "D03_FILLS"

    def valuation_pl(self):
        self._log.append(("d03_valuation_pl",)); return "D03_VAL"

    def settlement_pl(self, *, base_date):
        self._log.append(("d03_settlement_pl", base_date)); return "D03_SETTLE"

    def commissions(self, *, start, end):
        self._log.append(("d03_commissions", start, end)); return "D03_COMM"

    def deposit(self):
        self._log.append(("d03_deposit",)); return "D03_DEPOSIT"

    def night_margin(self, *args, **kwargs):  # defer 증명용 캡처
        self._log.append(("d03_night_margin", args, kwargs)); return "D03_MARGIN"


class _StubDeriv08(OverseasDerivativesAccount):
    """해외선물옵션(08) 계좌 뷰 스텁 -- isinstance 통과 + 호출 로깅."""

    def __init__(self, log):
        self._log = log

    def deposit(self, **kwargs):  # **kwargs 로 잡아 CLI 의 defer(준 것만 전달)를 검증
        self._log.append(("o08_deposit", kwargs)); return "O08_DEPOSIT"

    def margin_detail(self, **kwargs):
        self._log.append(("o08_margin_detail", kwargs)); return "O08_MARGIN"

    def positions(self, *args, **kwargs):  # defer 증명용 캡처
        self._log.append(("o08_positions", args, kwargs)); return ["O08_POS"]

    def today_orders(self):
        self._log.append(("o08_today_orders",)); return ["O08_TODAY"]

    def daily_orders(self, *, start, end):
        self._log.append(("o08_daily_orders", start, end)); return ["O08_DAILY"]

    def daily_fills(self, *, start, end):
        self._log.append(("o08_daily_fills", start, end)); return "O08_FILLS"

    def period_pnl(self, *, start, end):
        self._log.append(("o08_period_pnl", start, end)); return "O08_PNL"

    def transactions(self, *, start, end):
        self._log.append(("o08_transactions", start, end)); return ["O08_TXN"]


def test_account_balance_bond_lists_lots(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_balance(object(), _args(["account", "balance", "--asset", "bond"]))
    assert result == ["LOT"]
    assert log == [("bonds_balance",)]


def test_account_balance_bond_rejects_overseas(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError):
        account.cmd_balance(object(), _args(
            ["account", "balance", "--asset", "bond", "--venue", "overseas"]))


def test_account_balance_stock_default_unchanged(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    assert account.cmd_balance(object(), _args(["account", "balance"])) == "STOCK_BAL"
    assert log == [("dom_balance",)]


# --- 채권 미체결: kis account orders --asset bond (주문 타임아웃 복구 경로) ---

def test_account_orders_bond_lists_open_orders(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_orders(object(), _args(
        ["account", "orders", "--asset", "bond", "--date", "20260814"]))
    assert result == ["OPEN"]
    assert log == [("bonds_open_orders", "20260814")]


def test_account_orders_bond_requires_date(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="주문일자"):
        account.cmd_orders(object(), _args(["account", "orders", "--asset", "bond"]))


def test_account_orders_bond_rejects_overseas(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="overseas"):
        account.cmd_orders(object(), _args(
            ["account", "orders", "--asset", "bond", "--date", "20260814", "--venue", "overseas"]))


def test_account_orders_stock_default_unchanged(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    assert account.cmd_orders(object(), _args(["account", "orders"])) == "STOCK_OPEN"
    assert log == [("dom_open_orders",)]


# --- 채권 체결내역: kis account fills --asset bond (기간별 주문·체결) ---

def test_account_fills_bond_forwards_all_arguments(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_fills(object(), _args(
        ["account", "fills", "--asset", "bond", "--start", "20240101", "--end", "20240131",
         "--side", "buy", "--symbol", "KR6449111CB8", "--unfilled-only"]))
    assert result == ["FILLS"]
    assert log == [("bonds_fills", "20240101", "20240131",
                    {"side": "buy", "symbol": "KR6449111CB8", "unfilled_only": True})]


def test_account_fills_bond_forwards_no_filters_by_default(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_fills(object(), _args(
        ["account", "fills", "--asset", "bond", "--start", "20240101", "--end", "20240131"]))
    assert log == [("bonds_fills", "20240101", "20240131", {})]


@pytest.mark.parametrize("cli_args", [
    ["account", "fills", "--asset", "bond", "--start", "20240101"],
    ["account", "fills", "--asset", "bond", "--end", "20240131"],
])
def test_account_fills_requires_start_and_end(monkeypatch, cli_args):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="기간"):
        account.cmd_fills(object(), _args(cli_args))


def test_account_fills_stock_default_forwards_all_arguments(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_fills(object(), _args(
        ["account", "fills", "--start", "20240101", "--end", "20240131",
         "--side", "sell", "--symbol", "005930", "--unfilled-only"]))
    assert result == ["STOCK_FILLS"]
    assert log == [("stock_fills", "20240101", "20240131",
                    {"side": "sell", "symbol": "005930", "unfilled_only": True})]


def test_account_fills_stock_forwards_no_filters_by_default(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_fills(object(), _args(
        ["account", "fills", "--start", "20240101", "--end", "20240131"]))
    assert log == [("stock_fills", "20240101", "20240131", {})]


def test_account_fills_rejects_overseas(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="overseas"):
        account.cmd_fills(object(), _args(
            ["account", "fills", "--asset", "bond", "--start", "20240101", "--end", "20240131",
             "--venue", "overseas"]))


# --- 국내 예약주문 조회: kis account reserved (기간별, 처리상태 필터) ---

def test_account_reserved_forwards_process_when_given(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_reserved(object(), _args(
        ["account", "reserved", "--start", "20240101", "--end", "20240131",
         "--process", "unprocessed"]))
    assert result == ["RESERVED"]
    assert log == [("reserved_orders", "20240101", "20240131", {"process": "unprocessed"})]


def test_account_reserved_defers_process_default(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_reserved(object(), _args(
        ["account", "reserved", "--start", "20240101", "--end", "20240131"]))
    assert log == [("reserved_orders", "20240101", "20240131", {})]


@pytest.mark.parametrize("partial", [
    ["--start", "20240101"],
    ["--end", "20240131"],
])
def test_account_reserved_requires_start_and_end(monkeypatch, partial):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="기간"):
        account.cmd_reserved(object(), _args(["account", "reserved", *partial]))


def test_account_reserved_overseas_routes_to_overseas(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_reserved(object(), _args(
        ["account", "reserved", "--venue", "overseas", "--start", "20240101", "--end", "20240131"]))
    assert result == ["OVS_RESERVED"]
    assert log == [("ovs_reserved_orders", "20240101", "20240131")]


def test_account_reserved_overseas_rejects_process(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="--process"):
        account.cmd_reserved(object(), _args(
            ["account", "reserved", "--venue", "overseas", "--start", "20240101",
             "--end", "20240131", "--process", "unprocessed"]))


def test_account_reserved_overseas_requires_range(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView([]))
    with pytest.raises(CliConfigError, match="기간"):
        account.cmd_reserved(object(), _args(
            ["account", "reserved", "--venue", "overseas", "--start", "20240101"]))


# --- 손익: kis account profits (국내 종목별/일별, 해외 기간손익) ---------

def test_account_profits_domestic_default_by_symbol(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_profits(object(), _args(
        ["account", "profits", "--start", "20240101", "--end", "20240131"]))
    assert result == ["TRADE_PROFITS"]
    assert log == [("trade_profits", "20240101", "20240131", {})]


def test_account_profits_domestic_by_day_forwards_filters(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_profits(object(), _args(
        ["account", "profits", "--start", "20240101", "--end", "20240131",
         "--by", "day", "--symbol", "005930", "--sort", "oldest"]))
    assert log == [("daily_profits", "20240101", "20240131",
                    {"symbol": "005930", "sort": "oldest"})]


def test_account_profits_domestic_default_forwards_filters(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_profits(object(), _args(
        ["account", "profits", "--start", "20240101", "--end", "20240131",
         "--symbol", "005930", "--sort", "oldest"]))
    assert log == [("trade_profits", "20240101", "20240131",
                    {"symbol": "005930", "sort": "oldest"})]


@pytest.mark.parametrize("bad", [["--currency", "USD"], ["--won-basis"]])
def test_account_profits_domestic_rejects_overseas_flags(monkeypatch, bad):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError):
        account.cmd_profits(object(), _args(
            ["account", "profits", "--start", "20240101", "--end", "20240131", *bad]))
    assert log == []


def test_account_profits_overseas_routes_and_defers(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_profits(object(), _args(
        ["account", "profits", "--venue", "overseas", "--start", "20240101", "--end", "20240131"]))
    assert result == "OVS_PROFIT"
    assert log == [("ovs_period_profit", "20240101", "20240131", {})]


def test_account_profits_overseas_forwards_currency_and_won_basis(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_profits(object(), _args(
        ["account", "profits", "--venue", "overseas", "--start", "20240101", "--end", "20240131",
         "--symbol", "AAPL", "--currency", "USD", "--won-basis"]))
    assert log == [("ovs_period_profit", "20240101", "20240131",
                    {"symbol": "AAPL", "currency": "USD", "won_basis": True})]


@pytest.mark.parametrize("bad", [["--by", "day"], ["--sort", "recent"]])
def test_account_profits_overseas_rejects_domestic_flags(monkeypatch, bad):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError):
        account.cmd_profits(object(), _args(
            ["account", "profits", "--venue", "overseas", "--start", "20240101",
             "--end", "20240131", *bad]))
    assert log == []


@pytest.mark.parametrize("partial", [["--start", "20240101"], ["--end", "20240131"]])
def test_account_profits_requires_range(monkeypatch, partial):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="기간"):
        account.cmd_profits(object(), _args(["account", "profits", *partial]))
    assert log == []


# --- 거래·입출금내역: kis account transactions (해외 전용) ----------------

def test_account_transactions_overseas_routes_and_defers(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    result = account.cmd_transactions(object(), _args(
        ["account", "transactions", "--venue", "overseas", "--start", "20240101", "--end", "20240131"]))
    assert result == ["OVS_TXN"]
    assert log == [("ovs_transactions", "20240101", "20240131", {})]


def test_account_transactions_overseas_forwards_filters(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_transactions(object(), _args(
        ["account", "transactions", "--venue", "overseas", "--start", "20240101", "--end", "20240131",
         "--symbol", "AAPL", "--side", "buy"]))
    assert log == [("ovs_transactions", "20240101", "20240131", {"symbol": "AAPL", "side": "buy"})]


def test_account_transactions_rejects_domestic(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="해외"):
        account.cmd_transactions(object(), _args(
            ["account", "transactions", "--start", "20240101", "--end", "20240131"]))
    assert log == []


@pytest.mark.parametrize("partial", [["--start", "20240101"], ["--end", "20240131"]])
def test_account_transactions_requires_range(monkeypatch, partial):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="기간"):
        account.cmd_transactions(object(), _args(
            ["account", "transactions", "--venue", "overseas", *partial]))
    assert log == []


# --- 파생 계좌(03/08) 다형 디스패치: kis.account 뷰 타입으로 라우팅 -------

def _deriv(monkeypatch, stub_cls):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: stub_cls(log))
    return log


def test_account_balance_domestic_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    assert account.cmd_balance(object(), _args(["account", "balance"])) == "D03_BAL"
    assert log == [("d03_balance",)]


def test_account_balance_overseas_deriv_maps_to_deposit(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    assert account.cmd_balance(object(), _args(["account", "balance"])) == "O08_DEPOSIT"
    assert log == [("o08_deposit", {})]


def test_account_positions_overseas_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    assert account.cmd_positions(object(), _args(["account", "positions"])) == ["O08_POS"]
    assert log == [("o08_positions", (), {})]


def test_account_positions_domestic_deriv_rejects(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    with pytest.raises(CliConfigError, match="balance"):
        account.cmd_positions(object(), _args(["account", "positions"]))
    assert log == []


def test_account_orders_domestic_deriv_open_orders(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    account.cmd_orders(object(), _args(["account", "orders", "--date", "20240102"]))
    assert log == [("d03_open_orders", (), {"order_date": "20240102"})]


def test_account_orders_overseas_deriv_today_vs_daily(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_orders(object(), _args(["account", "orders"]))
    account.cmd_orders(object(), _args(
        ["account", "orders", "--start", "20240101", "--end", "20240131"]))
    assert log == [("o08_today_orders",), ("o08_daily_orders", "20240101", "20240131")]


def test_account_orders_overseas_deriv_partial_range_rejected(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    with pytest.raises(CliConfigError, match="start/--end"):
        account.cmd_orders(object(), _args(["account", "orders", "--start", "20240101"]))
    assert log == []


def test_account_fills_domestic_deriv_base_date(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    account.cmd_fills(object(), _args(["account", "fills", "--date", "20240102"]))
    assert log == [("d03_base_date_fills", "20240102")]


def test_account_fills_domestic_deriv_requires_date(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    with pytest.raises(CliConfigError, match="주문일자"):
        account.cmd_fills(object(), _args(["account", "fills"]))
    assert log == []


def test_account_fills_overseas_deriv_daily(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_fills(object(), _args(
        ["account", "fills", "--start", "20240101", "--end", "20240131"]))
    assert log == [("o08_daily_fills", "20240101", "20240131")]


def test_account_profits_overseas_deriv_period_pnl(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_profits(object(), _args(
        ["account", "profits", "--start", "20240101", "--end", "20240131"]))
    assert log == [("o08_period_pnl", "20240101", "20240131")]


def test_account_profits_domestic_deriv_rejects(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    with pytest.raises(CliConfigError, match="valuation"):
        account.cmd_profits(object(), _args(
            ["account", "profits", "--start", "20240101", "--end", "20240131"]))
    assert log == []


def test_account_transactions_overseas_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_transactions(object(), _args(
        ["account", "transactions", "--start", "20240101", "--end", "20240131"]))
    assert log == [("o08_transactions", "20240101", "20240131")]


# --- 파생 전용 신규 subcommand: deposit/margin/valuation/settlement/commissions/present ---

def test_account_deposit_domestic_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    assert account.cmd_deposit(object(), _args(["account", "deposit"])) == "D03_DEPOSIT"
    assert log == [("d03_deposit",)]


def test_account_deposit_overseas_deriv_defers_currency_date(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_deposit(object(), _args(["account", "deposit"]))
    account.cmd_deposit(object(), _args(
        ["account", "deposit", "--currency", "USD", "--date", "20240102"]))
    assert log == [("o08_deposit", {}),
                   ("o08_deposit", {"currency": "USD", "date": "20240102"})]


def test_account_deposit_stock_rejects(monkeypatch):
    log = _deriv(monkeypatch, _StubStockView)
    with pytest.raises(CliConfigError, match="선물옵션"):
        account.cmd_deposit(object(), _args(["account", "deposit"]))
    assert log == []


def test_account_margin_domestic_and_overseas(monkeypatch):
    log03 = _deriv(monkeypatch, _StubDeriv03)
    assert account.cmd_margin(object(), _args(["account", "margin"])) == "D03_MARGIN"
    log08 = _deriv(monkeypatch, _StubDeriv08)
    account.cmd_margin(object(), _args(["account", "margin", "--currency", "USD"]))
    assert log03 == [("d03_night_margin", (), {})]
    assert log08 == [("o08_margin_detail", {"currency": "USD"})]


def test_account_valuation_domestic_deriv_only(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    assert account.cmd_valuation(object(), _args(["account", "valuation"])) == "D03_VAL"
    assert log == [("d03_valuation_pl",)]


def test_account_valuation_stock_rejects(monkeypatch):
    _deriv(monkeypatch, _StubStockView)
    with pytest.raises(CliConfigError, match="국내선물옵션"):
        account.cmd_valuation(object(), _args(["account", "valuation"]))


def test_account_settlement_domestic_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    account.cmd_settlement(object(), _args(["account", "settlement", "--date", "20240102"]))
    assert log == [("d03_settlement_pl", "20240102")]


def test_account_settlement_domestic_deriv_requires_date(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    with pytest.raises(CliConfigError, match="기준일자"):
        account.cmd_settlement(object(), _args(["account", "settlement"]))
    assert log == []


def test_account_settlement_overseas_stock(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_settlement(object(), _args(
        ["account", "settlement", "--venue", "overseas", "--date", "20240102"]))
    assert log == [("ovs_settlement_balance", "20240102")]


def test_account_commissions_domestic_deriv(monkeypatch):
    log = _deriv(monkeypatch, _StubDeriv03)
    account.cmd_commissions(object(), _args(
        ["account", "commissions", "--start", "20240101", "--end", "20240131"]))
    assert log == [("d03_commissions", "20240101", "20240131")]


def test_account_commissions_stock_rejects(monkeypatch):
    _deriv(monkeypatch, _StubStockView)
    with pytest.raises(CliConfigError, match="국내선물옵션"):
        account.cmd_commissions(object(), _args(
            ["account", "commissions", "--start", "20240101", "--end", "20240131"]))


def test_account_present_overseas_stock(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_present(object(), _args(["account", "present"]))  # venue 축 없음 -- 해외주식 전용
    assert log == [("ovs_present_balance",)]


def test_account_present_deriv_rejects(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubDeriv03([]))
    with pytest.raises(CliConfigError, match="주식"):
        account.cmd_present(object(), _args(["account", "present"]))


def test_account_foreign_margin_overseas_stock(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_foreign_margin(object(), _args(["account", "foreign-margin"]))
    assert log == [("ovs_foreign_margin",)]


def test_account_foreign_margin_deriv_rejects(monkeypatch):
    monkeypatch.setattr(account, "_view", lambda kis: _StubDeriv08([]))
    with pytest.raises(CliConfigError, match="주식"):
        account.cmd_foreign_margin(object(), _args(["account", "foreign-margin"]))


# --- 파생 분기: 안 쓰는 계좌 플래그를 명시하면 거부(조용한 무시 방지) ----

@pytest.mark.parametrize("cmd,stub,argv", [
    (account.cmd_balance, _StubDeriv03, ["account", "balance", "--asset", "bond"]),
    (account.cmd_balance, _StubDeriv08, ["account", "balance", "--market", "US"]),
    (account.cmd_fills, _StubDeriv08,
     ["account", "fills", "--start", "20240101", "--end", "20240131", "--side", "buy"]),
    (account.cmd_profits, _StubDeriv08,
     ["account", "profits", "--start", "20240101", "--end", "20240131", "--symbol", "X"]),
    (account.cmd_orders, _StubDeriv08, ["account", "orders", "--date", "20240102"]),
    (account.cmd_orders, _StubDeriv03, ["account", "orders", "--start", "20240101", "--end", "20240131"]),
    (account.cmd_deposit, _StubDeriv03, ["account", "deposit", "--currency", "USD"]),
    (account.cmd_settlement, _StubDeriv03,
     ["account", "settlement", "--date", "20240102", "--venue", "overseas"]),
])
def test_account_deriv_rejects_foreign_flag(monkeypatch, cmd, stub, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: stub(log))
    with pytest.raises(CliConfigError, match="지원되지 않습니다"):
        cmd(object(), _args(argv))
    assert log == []


# --- 파생 미지원 (계좌타입 × 명령) 거부 -----------------------------------

@pytest.mark.parametrize("cmd,stub,argv", [
    (account.cmd_transactions, _StubDeriv03,
     ["account", "transactions", "--start", "20240101", "--end", "20240131"]),
    (account.cmd_valuation, _StubDeriv08, ["account", "valuation"]),
    (account.cmd_settlement, _StubDeriv08, ["account", "settlement", "--date", "20240102"]),
    (account.cmd_commissions, _StubDeriv08,
     ["account", "commissions", "--start", "20240101", "--end", "20240131"]),
])
def test_account_command_rejects_wrong_account_type(monkeypatch, cmd, stub, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: stub(log))
    with pytest.raises(CliConfigError):
        cmd(object(), _args(argv))
    assert log == []


# --- 파생 range/date 경계 요구 -------------------------------------------

@pytest.mark.parametrize("cmd,stub,argv", [
    (account.cmd_fills, _StubDeriv08, ["account", "fills", "--start", "20240101"]),
    (account.cmd_profits, _StubDeriv08, ["account", "profits", "--start", "20240101"]),
    (account.cmd_transactions, _StubDeriv08, ["account", "transactions", "--end", "20240131"]),
    (account.cmd_commissions, _StubDeriv03, ["account", "commissions", "--start", "20240101"]),
])
def test_account_deriv_requires_full_range(monkeypatch, cmd, stub, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: stub(log))
    with pytest.raises(CliConfigError):
        cmd(object(), _args(argv))
    assert log == []


def test_account_settlement_overseas_stock_requires_date(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="기준일자"):
        account.cmd_settlement(object(), _args(["account", "settlement", "--venue", "overseas"]))
    assert log == []


def test_account_margin_overseas_deriv_defers_and_forwards(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubDeriv08(log))
    account.cmd_margin(object(), _args(["account", "margin"]))
    account.cmd_margin(object(), _args(
        ["account", "margin", "--currency", "USD", "--date", "20240102"]))
    assert log == [("o08_margin_detail", {}),
                   ("o08_margin_detail", {"currency": "USD", "date": "20240102"})]


@pytest.mark.parametrize("cmd,argv", [
    (account.cmd_valuation, ["account", "valuation"]),
    (account.cmd_commissions, ["account", "commissions", "--start", "20240101", "--end", "20240131"]),
])
def test_account_deriv_only_command_rejects_stock_noop(monkeypatch, cmd, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError):
        cmd(object(), _args(argv))
    assert log == []


# --- 주식 분기도 안 쓰는 계좌 플래그를 거부(파생 분기와 대칭) ------------

@pytest.mark.parametrize("argv", [
    ["account", "fills", "--start", "20240101", "--end", "20240131", "--date", "20240101"],
    ["account", "orders", "--start", "20240101", "--end", "20240131"],
    ["account", "balance", "--market", "US"],       # venue=domestic 이면 --market 은 무의미 -> 거부
    ["account", "positions", "--market", "US"],
    ["account", "orders", "--market", "US"],
])
def test_account_stock_rejects_foreign_flag(monkeypatch, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    func = {"fills": account.cmd_fills, "orders": account.cmd_orders,
            "balance": account.cmd_balance, "positions": account.cmd_positions}[argv[1]]
    with pytest.raises(CliConfigError, match="지원되지 않습니다"):
        func(object(), _args(argv))
    assert log == []


# --- 해외주식 라우팅 + 필수 --market ------------------------------------

def test_account_balance_overseas_stock_routes_with_market(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_balance(object(), _args(["account", "balance", "--venue", "overseas", "--market", "US"]))
    assert log == [("ovs_balance", "US")]


def test_account_balance_overseas_requires_market(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="시장"):
        account.cmd_balance(object(), _args(["account", "balance", "--venue", "overseas"]))
    assert log == []


def test_account_positions_stock_routes(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_positions(object(), _args(["account", "positions"]))
    account.cmd_positions(object(), _args(["account", "positions", "--venue", "overseas", "--market", "US"]))
    assert log == [("dom_positions",), ("ovs_positions", "US")]


def test_account_orders_stock_overseas_routes(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    account.cmd_orders(object(), _args(["account", "orders", "--venue", "overseas", "--market", "US"]))
    assert log == [("ovs_open_orders", "US")]


def test_account_orders_domestic_deriv_defers_date(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubDeriv03(log))
    account.cmd_orders(object(), _args(["account", "orders"]))  # --date 없음 -> 무인자 호출(라이브러리 기본)
    assert log == [("d03_open_orders", (), {})]


# --- 파생/계좌타입 reject 갭(B2) -----------------------------------------

@pytest.mark.parametrize("cmd,stub,argv", [
    (account.cmd_positions, _StubDeriv08, ["account", "positions", "--market", "US"]),
    (account.cmd_fills, _StubDeriv03, ["account", "fills", "--date", "20240101", "--side", "buy"]),
    (account.cmd_margin, _StubDeriv03, ["account", "margin", "--currency", "USD"]),
    (account.cmd_transactions, _StubDeriv08,
     ["account", "transactions", "--start", "20240101", "--end", "20240131", "--side", "buy"]),
])
def test_account_deriv_rejects_foreign_flag_more(monkeypatch, cmd, stub, argv):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: stub(log))
    with pytest.raises(CliConfigError, match="지원되지 않습니다"):
        cmd(object(), _args(argv))
    assert log == []


@pytest.mark.parametrize("stub", [_StubDeriv03, _StubDeriv08])
def test_account_reserved_rejects_derivative(monkeypatch, stub):
    monkeypatch.setattr(account, "_view", lambda kis: stub([]))
    with pytest.raises(CliConfigError, match="주식"):
        account.cmd_reserved(object(), _args(
            ["account", "reserved", "--start", "20240101", "--end", "20240131"]))


def test_account_margin_stock_rejects(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="선물옵션"):
        account.cmd_margin(object(), _args(["account", "margin"]))
    assert log == []


def test_account_settlement_domestic_stock_rejects(monkeypatch):
    log: list = []
    monkeypatch.setattr(account, "_view", lambda kis: _StubStockView(log))
    with pytest.raises(CliConfigError, match="정산잔고"):
        account.cmd_settlement(object(), _args(["account", "settlement", "--date", "20240101"]))
    assert log == []


@pytest.mark.parametrize("stub", [_StubDeriv03, _StubDeriv08])
def test_account_present_deriv_rejects_both(monkeypatch, stub):
    monkeypatch.setattr(account, "_view", lambda kis: stub([]))
    with pytest.raises(CliConfigError, match="주식"):
        account.cmd_present(object(), _args(["account", "present"]))


@pytest.mark.parametrize("stub", [_StubDeriv03, _StubDeriv08])
def test_account_foreign_margin_deriv_rejects_both(monkeypatch, stub):
    monkeypatch.setattr(account, "_view", lambda kis: stub([]))
    with pytest.raises(CliConfigError, match="주식"):
        account.cmd_foreign_margin(object(), _args(["account", "foreign-margin"]))


# --- 완결성 가드: _ACCOUNT_FLAGS 가 파서 선언과 동기(누락/센티널 드리프트 방지) ---

def test_account_flags_table_matches_parser():
    import argparse as _argparse

    from kis_trader.cli.commands.account import _ACCOUNT_FLAGS

    def _subchoices(p):
        for a in p._actions:
            if isinstance(a, _argparse._SubParsersAction):
                return a.choices
        return {}

    table = {dest: sentinel for dest, sentinel, _cli in _ACCOUNT_FLAGS}
    account_cmds = _subchoices(_subchoices(build_parser())["account"])
    common = {"help", "func", "profile", "account", "fmt", "no_header", "include_raw"}
    for cmd, leaf in account_cmds.items():
        for act in leaf._actions:
            if act.dest in common or not act.option_strings:
                continue
            assert act.dest in table, f"{cmd}: --{act.dest} 가 _ACCOUNT_FLAGS 에 없음(조용한 무시 위험)"
            assert table[act.dest] == act.default, (
                f"{cmd}: {act.dest} 센티널 {table[act.dest]!r} != 파서 기본값 {act.default!r}")


# --- 채권 주문 CLI: dry-run 티켓 + fail-closed 검증 ------------------------

def test_order_bond_buy_dry_run_shows_ticket_and_sends_nothing():
    kis = StubKis()
    dry = order.cmd_buy(kis, _args(
        ["order", "buy", "KR6449111CB8", "100", "--asset", "bond",
         "--limit-price", "10125"]), is_tty=False)
    assert dry["asset"] == "bond"
    assert dry["limit_price"] == "10125"
    assert dry["division"] is None
    assert "note" in dry
    assert kis.log == []  # dry-run 은 채권 핸들에 닿지 않는다


def test_order_bond_sell_dry_run_shows_lot_and_sends_nothing():
    kis = StubKis()
    dry = order.cmd_sell(kis, _args(
        ["order", "sell", "KR6449111CB8", "100", "--asset", "bond",
         "--limit-price", "10130", "--buy-date", "20260814", "--buy-seq", "1"]),
        is_tty=False)
    assert dry["asset"] == "bond"
    assert dry["buy_date"] == "20260814"
    assert dry["buy_seq"] == "1"
    assert kis.log == []


def test_order_bond_buy_requires_limit_price():
    args = _args(["order", "buy", "KR6449111CB8", "100", "--asset", "bond"])
    with pytest.raises(CliConfigError, match="--limit-price"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_bond_buy_rejects_empty_limit_price():
    args = _args(["order", "buy", "KR6449111CB8", "100", "--asset", "bond",
                  "--limit-price", "  "])
    with pytest.raises(CliConfigError, match="--limit-price"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_bond_rejects_division():
    args = _args(["order", "buy", "KR6449111CB8", "100", "--asset", "bond",
                  "--limit-price", "10125", "--division", "immediate_limit"])
    with pytest.raises(CliConfigError, match="--division"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_bond_rejects_overseas():
    args = _args(["order", "buy", "KR6449111CB8", "100", "--asset", "bond",
                  "--limit-price", "10125", "--venue", "overseas"])
    with pytest.raises(CliConfigError, match="overseas"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_bond_rejects_execute_paper():
    kis = StubKis(environment="paper")
    args = _args(["--profile", "paper", "order", "buy", "KR6449111CB8", "100",
                  "--asset", "bond", "--limit-price", "10125",
                  "--execute", "paper", "--yes"])
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_buy(kis, args, is_tty=False)
    assert kis.log == []


def test_order_bond_sell_requires_lot():
    args = _args(["order", "sell", "KR6449111CB8", "100", "--asset", "bond",
                  "--limit-price", "10130"])
    with pytest.raises(CliConfigError, match="buy-date"):
        order.cmd_sell(StubKis(), args, is_tty=False)


def test_order_bond_buy_rejects_lot():
    args = _args(["order", "buy", "KR6449111CB8", "100", "--asset", "bond",
                  "--limit-price", "10125", "--buy-date", "20260814", "--buy-seq", "1"])
    with pytest.raises(CliConfigError, match="buy-date"):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_stock_rejects_bond_lot():
    args = _args(["order", "sell", "005930", "10",
                  "--buy-date", "20260814", "--buy-seq", "1"])
    with pytest.raises(CliConfigError, match="buy-date"):
        order.cmd_sell(StubKis(), args, is_tty=False)


def test_order_stock_dry_run_unchanged_has_no_bond_fields():
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "005930", "10", "--limit-price", "70000"]), is_tty=False)
    assert dry["asset"] == "stock"
    assert "buy_date" not in dry
    assert dry["limit_price"] == "70000"


# --- 채권 주문 CLI: 전송 경로 라우팅 --------------------------------------

def test_order_bond_buy_execute_routes_to_bond_handle():
    kis = StubKis(account="12345678-29", environment="real")
    args = _args(["--profile", "irp", "order", "buy", "KR6449111CB8", "100",
                  "--asset", "bond", "--limit-price", "10125",
                  "--execute", "real", "--yes", "--confirm-account", "1729"])
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("bond_buy", "KR6449111CB8", 100, "10125")]


def test_order_bond_sell_execute_forwards_lot():
    kis = StubKis(account="12345678-29", environment="real")
    args = _args(["--profile", "irp", "order", "sell", "KR6449111CB8", "100",
                  "--asset", "bond", "--limit-price", "10130",
                  "--buy-date", "20260814", "--buy-seq", "1",
                  "--execute", "real", "--yes", "--confirm-account", "1729"])
    assert order.cmd_sell(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("bond_sell", "KR6449111CB8", 100, "10130", "20260814", "1")]


def test_order_bond_real_rejects_mismatched_confirm_account():
    # 실주문 계좌확인 게이트가 채권 분기에서도 유지되는지 -- 회귀 방지(auth 는 라우팅보다 먼저).
    kis = StubKis(account="12345678-29", environment="real")
    args = _args(["--profile", "irp", "order", "buy", "KR6449111CB8", "100",
                  "--asset", "bond", "--limit-price", "10125",
                  "--execute", "real", "--yes", "--confirm-account", "0000"])
    with pytest.raises(CliConfigError, match="--confirm-account"):
        order.cmd_buy(kis, args, is_tty=False)
    assert kis.log == []  # 게이트 실패 -> 채권 핸들에 닿지 않음


# --- 파생 주문 CLI: dry-run 티켓 + fail-closed 검증 -----------------------

def test_order_futures_domestic_dry_run_ticket():
    kis = StubKis()
    dry = order.cmd_buy(kis, _args(
        ["order", "buy", "101W09", "1", "--asset", "futures", "--limit-price", "350.5"]),
        is_tty=False)
    assert dry["asset"] == "futures"
    assert dry["limit_price"] == "350.5"
    assert dry["night"] is False
    assert kis.log == []


def test_order_option_domestic_dry_run_shows_right():
    kis = StubKis()
    dry = order.cmd_buy(kis, _args(
        ["order", "buy", "201S07", "1", "--asset", "option", "--right", "call",
         "--limit-price", "5.2"]), is_tty=False)
    assert dry["asset"] == "option"
    assert dry["right"] == "call"
    assert kis.log == []


def test_order_futures_overseas_dry_run_shows_stop_price():
    kis = StubKis()
    dry = order.cmd_buy(kis, _args(
        ["order", "buy", "ESZ25", "1", "--asset", "futures", "--venue", "overseas",
         "--stop-price", "99"]), is_tty=False)
    assert dry["asset"] == "futures"
    assert dry["stop_price"] == "99"
    assert kis.log == []


def test_order_right_rejected_for_futures():
    with pytest.raises(CliConfigError, match="--right"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "101W09", "1", "--asset", "futures", "--right", "call"]),
            is_tty=False)


def test_order_right_rejected_for_overseas_option():
    with pytest.raises(CliConfigError, match="--right"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "X", "1", "--asset", "option", "--venue", "overseas",
             "--right", "call"]), is_tty=False)


def test_order_night_rejected_for_stock():
    with pytest.raises(CliConfigError, match="--night"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "005930", "1", "--night", "--limit-price", "70000"]),
            is_tty=False)


def test_order_night_rejected_for_overseas_futures():
    with pytest.raises(CliConfigError, match="--night"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "X", "1", "--asset", "futures", "--venue", "overseas", "--night"]),
            is_tty=False)


def test_order_stop_price_rejected_for_domestic_futures():
    with pytest.raises(CliConfigError, match="--stop-price"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "101W09", "1", "--asset", "futures", "--stop-price", "99"]),
            is_tty=False)


def test_order_division_priority_limit_rejected_for_futures():
    with pytest.raises(CliConfigError, match="priority_limit"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "101W09", "1", "--asset", "futures",
             "--division", "priority_limit"]), is_tty=False)


def test_order_division_immediate_limit_ok_for_futures():
    kis = StubKis()
    dry = order.cmd_buy(kis, _args(
        ["order", "buy", "101W09", "1", "--asset", "futures",
         "--division", "immediate_limit"]), is_tty=False)
    assert dry["division"] == "immediate_limit"


@pytest.mark.parametrize("division", ["midpoint", "pre_market_close",
                                      "post_market_close", "after_market_limit",
                                      "after_market_immediate_limit", "after_market_priority_limit"])
def test_order_tier2_division_dry_run_and_execute_forwards_it(division):
    # 지정가 기반(after_market_limit)은 --limit-price 필수, 나머지 가격없는 구분은 주지 않는다.
    price_args = ["--limit-price", "70000"] if division == "after_market_limit" else []
    dry = order.cmd_buy(StubKis(), _args(
        ["order", "buy", "005930", "10", "--division", division, *price_args]), is_tty=False)
    assert dry["division"] == division
    kis = StubKis()
    order.cmd_buy(kis, _args(["order", "buy", "005930", "10", "--division", division,
                              *price_args, "--execute", "paper", "--yes"]),
                  is_tty=False)
    # _Handle.buy 로그: (op, code, qty, limit, division, stop_price, algo, algo_window)
    assert kis.log[-1][4] == division


@pytest.mark.parametrize("division", ["midpoint", "pre_market_close",
                                      "post_market_close", "after_market_limit",
                                      "gtp_limit"])
def test_order_tier2_division_rejected_for_futures(division):
    kis = StubKis()
    with pytest.raises(CliConfigError, match=division):
        order.cmd_buy(kis, _args(
            ["order", "buy", "101W09", "1", "--asset", "futures",
             "--division", division]), is_tty=False)
    assert kis.log == []  # 거부는 파생 핸들에 닿기 전 -- 전송 없음


def test_order_overseas_derivative_rejects_execute_paper():
    kis = StubKis(environment="paper")
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_buy(kis, _args(
            ["--profile", "paper", "order", "buy", "X", "1", "--asset", "futures",
             "--venue", "overseas", "--limit-price", "100", "--execute", "paper", "--yes"]),
            is_tty=False)
    assert kis.log == []


def test_order_night_rejects_execute_paper():
    kis = StubKis(environment="paper")
    with pytest.raises(CliConfigError, match="실전전용"):
        order.cmd_buy(kis, _args(
            ["--profile", "paper", "order", "buy", "101W09", "1", "--asset", "futures",
             "--night", "--limit-price", "350", "--execute", "paper", "--yes"]),
            is_tty=False)
    assert kis.log == []


def test_order_option_domestic_requires_right():
    with pytest.raises(CliConfigError, match="--right"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "201S07", "1", "--asset", "option", "--limit-price", "5.2"]),
            is_tty=False)


def test_order_exchange_rejected_for_derivative():
    with pytest.raises(CliConfigError, match="--exchange"):
        order.cmd_buy(StubKis(), _args(
            ["order", "buy", "ESZ25", "1", "--asset", "futures", "--venue", "overseas",
             "--exchange", "CME", "--limit-price", "100"]), is_tty=False)


# --- 파생 주문 CLI: 전송 경로 라우팅 --------------------------------------

def test_order_futures_domestic_execute_routes_with_division_night():
    kis = StubKis(account="12345678-03", environment="real")
    args = _args(["--profile", "derivatives", "order", "buy", "101W09", "1",
                  "--asset", "futures", "--limit-price", "350.5",
                  "--division", "immediate_limit",
                  "--execute", "real", "--yes", "--confirm-account", "7803"])
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("dom_deriv_buy", "futures", "101W09", None, 1, "350.5",
                        "immediate_limit", False)]


def test_order_option_domestic_execute_forwards_right():
    kis = StubKis(account="12345678-03", environment="real")
    args = _args(["--profile", "derivatives", "order", "buy", "201S07", "1",
                  "--asset", "option", "--right", "put", "--limit-price", "5.2",
                  "--execute", "real", "--yes", "--confirm-account", "7803"])
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("dom_deriv_buy", "option", "201S07", "put", 1, "5.2", None, False)]


def test_order_futures_overseas_execute_forwards_stop_price():
    kis = StubKis(account="12345678-08", environment="real")
    args = _args(["--profile", "overseas_derivatives", "order", "sell", "ESZ25", "2",
                  "--asset", "futures", "--venue", "overseas", "--stop-price", "99",
                  "--execute", "real", "--yes", "--confirm-account", "7808"])
    assert order.cmd_sell(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("ovs_deriv_sell", "futures", "ESZ25", 2, None, "99")]


def test_order_futures_domestic_sell_execute_routes_to_sell_handle():
    kis = StubKis(account="12345678-03", environment="real")
    args = _args(["--profile", "derivatives", "order", "sell", "101W09", "1",
                  "--asset", "futures", "--limit-price", "350.5",
                  "--execute", "real", "--yes", "--confirm-account", "7803"])
    assert order.cmd_sell(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("dom_deriv_sell", "futures", "101W09", None, 1, "350.5", None, False)]


def test_order_option_overseas_execute_routes_to_option_handle():
    kis = StubKis(account="12345678-08", environment="real")
    args = _args(["--profile", "overseas_derivatives", "order", "buy", "OESX25", "1",
                  "--asset", "option", "--venue", "overseas", "--limit-price", "12",
                  "--execute", "real", "--yes", "--confirm-account", "7808"])
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("ovs_deriv_buy", "option", "OESX25", 1, "12", None)]


def test_order_futures_domestic_real_rejects_mismatched_confirm():
    kis = StubKis(account="12345678-03", environment="real")
    args = _args(["--profile", "derivatives", "order", "buy", "101W09", "1",
                  "--asset", "futures", "--limit-price", "350.5",
                  "--execute", "real", "--yes", "--confirm-account", "0000"])
    with pytest.raises(CliConfigError, match="--confirm-account"):
        order.cmd_buy(kis, args, is_tty=False)
    assert kis.log == []

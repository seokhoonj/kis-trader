"""CLI(``kis``) -- 인자 배선, 출력 렌더링, 주문 안전 게이트, 오류 번역."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

import kis_trader.cli.app as cli_main
from kis_trader.cli.app import build_parser
from kis_trader.cli.commands import order
from kis_trader.cli.context import account_suffix
from kis_trader.cli.errors import CliAborted, CliConfigError, translate
from kis_trader.cli.output import render, to_jsonable
from kis_trader.errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    OrderRejectedError,
    OrderTimeoutError,
    PreTradeRiskError,
)


# --- 스텁 클라이언트: 네트워크 없이 어떤 공개 메서드가 불렸는지만 기록 --------------

class _Handle:
    def __init__(self, log, code):
        self._log = log
        self._code = code

    def quote(self):
        self._log.append(("quote", self._code)); return "QUOTE"

    def buy(self, *, quantity, limit_price):
        self._log.append(("buy", self._code, quantity, limit_price)); return "REPORT"

    def sell(self, *, quantity, limit_price):
        self._log.append(("sell", self._code, quantity, limit_price)); return "REPORT"


class _Domestic:
    def __init__(self, log):
        self._log = log

    def stock(self, code):
        return _Handle(self._log, code)

    def search(self, query, *, market):
        self._log.append(("search", query, market)); return ["HIT"]


class _Orders:
    def __init__(self, log):
        self._log = log

    def reconcile(self, client_order_id):
        self._log.append(("reconcile", client_order_id))

    def cancel(self, client_order_id, *, quantity=None):
        self._log.append(("cancel", client_order_id, quantity)); return "REPORT"


class StubKis:
    def __init__(self):
        self.log: list = []
        self.domestic = _Domestic(self.log)
        self.orders = _Orders(self.log)


def _args(argv):
    return build_parser().parse_args(argv)


# --- 파서 배선 --------------------------------------------------------------

def test_parser_routes_stock_quote_to_handler():
    args = _args(["stock", "quote", "005930"])
    kis = StubKis()
    assert args.func(kis, args) == "QUOTE"
    assert kis.log == [("quote", "005930")]


def test_parser_defaults_to_paper_and_table():
    args = _args(["search", "삼성전자"])
    assert args.env == "paper"
    assert args.fmt == "table"


def test_search_passes_market_and_returns_all_candidates():
    args = _args(["search", "삼성", "--market", "KOSDAQ"])
    kis = StubKis()
    assert args.func(kis, args) == ["HIT"]
    assert kis.log == [("search", "삼성", "KOSDAQ")]


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
    row = _Row(symbol="005930", price=Decimal("71500"), _raw={"x": 1})
    out = render(row, fmt="json")
    assert '"price": "71500"' in out
    assert "_raw" not in out
    assert '"ok": true' in out


def test_render_json_includes_raw_only_when_asked():
    row = _Row(symbol="005930", price=Decimal("71500"), _raw={"x": 1})
    assert "_raw" in render(row, fmt="json", include_raw=True)


def test_to_jsonable_serializes_nested_list_of_dataclasses():
    rows = [_Row("005930", Decimal("1"), {}), _Row("000660", Decimal("2"), {})]
    data = to_jsonable(rows)
    assert data == [{"symbol": "005930", "price": "1"}, {"symbol": "000660", "price": "2"}]


# --- 주문 안전 게이트 -------------------------------------------------------

def test_order_dry_run_shows_ticket_and_sends_nothing():
    args = _args(["order", "buy", "005930", "10", "--limit-price", "70000"])
    kis = StubKis()
    result = order.cmd_buy(kis, args, is_tty=False)
    assert result["side"] == "buy"
    assert result["order_type"] == "limit"
    assert "note" in result
    assert kis.log == []  # 전송 없음


def test_order_execute_environment_mismatch_is_rejected():
    args = _args(["--env", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "real"])
    with pytest.raises(CliConfigError):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_noninteractive_requires_yes():
    args = _args(["--env", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "paper"])
    with pytest.raises(CliConfigError):
        order.cmd_buy(StubKis(), args, is_tty=False)


def test_order_paper_noninteractive_with_yes_sends_once():
    args = _args(["--env", "paper", "order", "buy", "005930", "10",
                  "--limit-price", "70000", "--execute", "paper", "--yes"])
    kis = StubKis()
    assert order.cmd_buy(kis, args, is_tty=False) == "REPORT"
    assert kis.log == [("buy", "005930", 10, "70000")]


def test_order_real_noninteractive_needs_matching_confirm_account():
    base = ["--env", "real", "--account", "12345678-01", "order", "buy",
            "005930", "10", "--limit-price", "70000", "--execute", "real", "--yes"]
    kis = StubKis()
    with pytest.raises(CliConfigError):
        order.cmd_buy(kis, _args(base + ["--confirm-account", "0000"]), is_tty=False)
    assert order.cmd_buy(kis, _args(base + ["--confirm-account", "7801"]), is_tty=False) == "REPORT"


def test_order_interactive_real_confirms_by_account_suffix():
    args = _args(["--env", "real", "--account", "12345678-01", "order", "buy",
                  "005930", "10", "--limit-price", "70000", "--execute", "real"])
    kis = StubKis()
    with pytest.raises(CliAborted):
        order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "0000")
    assert order.cmd_buy(kis, args, is_tty=True, prompt=lambda _p: "7801") == "REPORT"


def test_order_reconcile_never_resends():
    args = _args(["order", "reconcile", "abc-123"])
    kis = StubKis()
    assert args.func(kis, args) is None
    assert kis.log == [("reconcile", "abc-123")]


# --- 오류 번역 --------------------------------------------------------------

@pytest.mark.parametrize("exc, exit_code, outcome, reconcile", [
    (OrderTimeoutError("timeout", client_order_id="abc-123"), 7, "unknown", True),
    (OrderRejectedError("rejected"), 6, "rejected", False),
    (PreTradeRiskError("risk"), 4, "not_sent", False),
    (AccountNotOrderableError("irp"), 4, "not_sent", False),
    (KISAuthError("bad key"), 3, "config", False),
    (KISError("boom"), 5, "failed", False),
])
def test_translate_maps_exceptions_to_exit_codes(exc, exit_code, outcome, reconcile):
    t = translate(exc)
    assert (t.exit_code, t.outcome, t.reconcile_required) == (exit_code, outcome, reconcile)


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

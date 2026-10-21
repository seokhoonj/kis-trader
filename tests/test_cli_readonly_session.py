"""조회 CLI 는 주문 저널 없이 동작하고 쓰기·미지 명령은 영속 가드를 유지한다."""
from __future__ import annotations

import os
import socket
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.cli import app, context
from kis_trader.config import ResolvedCredentials
from kis_trader.errors import AccountNotOrderableError, KISUsageError
from kis_trader.transport import RawResponse

_READ_COMMANDS = [
    ["stock", "quote", "005930"], ["stock", "bars", "005930", "--interval", "1d"],
    ["stock", "book", "005930"], ["stock", "trades", "005930"],
    ["stock", "status", "005930"], ["stock", "investors", "005930"],
    ["index", "quote", "0001", "--region", "domestic"],
    ["index", "bars", "0001", "--region", "domestic", "--interval", "1d"],
    ["search", "synthetic"], ["ranking", "change", "--direction", "gainers"],
    ["ranking", "volume"], ["ranking", "market-cap"],
    ["market", "investors"], ["market", "net-buy"],
    *[["account", action] for action in (
        "balance", "positions", "orders", "fills", "reserved", "profits", "transactions",
        "deposit", "margin", "valuation", "settlement", "commissions", "present", "foreign-margin",
    )],
    ["account", "buyable", "005930"],
]
_ORDER_COMMANDS = [
    ["order", "buy", "005930", "1"], ["order", "sell", "005930", "1"],
    ["order", "buy", "AAPL", "10", "--region", "overseas", "--algo", "twap"],
    ["order", "buy", "005930", "1", "--reserve"],
    ["order", "reconcile", "synthetic-request"],
    ["order", "modify", "synthetic-request", "--limit-price", "70000"],
    ["order", "cancel", "synthetic-request"], ["order", "cancel-reserved", "synthetic-request"],
    ["order", "modify-reserved", "synthetic-request", "--symbol", "005930", "--side", "buy", "--quantity", "1"],
    ["order", "twap", "005930", "--side", "buy", "--quantity", "2", "--over", "1m", "--slices", "2"],
]


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("KIS_"):
            monkeypatch.delenv(name)
    for name, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                            ("APPDATA", "config"), ("LOCALAPPDATA", "cache")):
        monkeypatch.setenv(name, str(tmp_path / directory))
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("unexpected network"))
    credentials = ResolvedCredentials(app_key="SYNTHETIC_KEY", app_secret="SYNTHETIC_SECRET",
                                      account="12345678-01", environment="paper",
                                      hts_id="synthetic_hts", account_password="synthetic_password")
    monkeypatch.setattr(context, "resolve_credentials", lambda *args, **kwargs: credentials)
    monkeypatch.setattr("kis_trader.client._fill_credentials", lambda *args, **kwargs: pytest.fail("second credential lookup"))


@pytest.mark.parametrize("arguments", _READ_COMMANDS)
def test_explicit_read_handler_never_resolves_order_journal_and_cannot_place(arguments, monkeypatch):
    monkeypatch.setattr(context, "order_store_path", lambda **kwargs: pytest.fail("read tried order journal"))
    args = app.build_parser().parse_args(arguments)
    with context.build_client(args) as client:
        assert client.environment == "paper"
        assert client._account == "12345678-01"
        assert client._hts_id == "synthetic_hts"
        assert client._account_password == "synthetic_password"
        with pytest.raises(AccountNotOrderableError):
            client.domestic.stock("005930").buy(quantity=1, limit_price=70000)


@pytest.mark.parametrize("arguments", _ORDER_COMMANDS)
def test_order_and_recovery_handlers_still_require_durable_layout(arguments, monkeypatch):
    def refused(**kwargs):
        raise KISUsageError("synthetic legacy layout requires offline migration")
    monkeypatch.setattr(context, "order_store_path", refused)
    with pytest.raises(context.CliConfigError, match="offline migration"):
        context.build_client(app.build_parser().parse_args(arguments))


@pytest.mark.parametrize("kind", ["unknown", "missing", "spoofed_name", "spoofed_equality"])
def test_unknown_or_spoofed_handler_does_not_gain_memory_session(kind, monkeypatch):
    args = app.build_parser().parse_args(["stock", "quote", "005930"])
    def unknown(*args):
        pytest.fail("unknown handler ran")
    class PretendsEqual:
        def __eq__(self, other):
            return True
    if kind == "missing":
        del args.func
    elif kind == "spoofed_equality":
        args.func = PretendsEqual()
    else:
        if kind == "spoofed_name":
            unknown.__name__ = args.func.__name__
        args.func = unknown
    def refused(**kwargs):
        raise KISUsageError("synthetic durable guard")
    monkeypatch.setattr(context, "order_store_path", refused)
    with pytest.raises(context.CliConfigError, match="durable guard"):
        context.build_client(args)


class StrictReadTransport:
    def __init__(self, expected):
        self.expected = list(expected)
        self.calls = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        assert method == "GET" and idempotent and body is None
        expected_path, expected_tr, response = self.expected.pop(0)
        assert (path, tr_id) == (expected_path, expected_tr)
        self.calls.append((path, tr_id, params))
        return response


@pytest.mark.parametrize("operation", ["quote", "fills", "older_fills", "positions"])
def test_actual_parser_builder_read_route_parses_without_touching_conflicting_journals(operation, tmp_path, monkeypatch):
    from test_client_overseas_positions import _holding
    from test_client_overseas_positions import _resp as position_response
    from test_stock_orders import _resp as history_response
    from test_stock_orders import _row

    cache = tmp_path / "cache" / "kis-trader" / "orders"
    state = tmp_path / "state" / "kis-trader" / "orders"
    cache.mkdir(parents=True)
    state.mkdir(parents=True)
    (cache / "unrelated.json").write_bytes(b"synthetic legacy bytes")
    (state / "unrelated.json").write_bytes(b"synthetic independent state")
    directories = (cache, state)
    before = [(directory.stat().st_ino, {p.name: p.read_bytes() for p in directory.iterdir()}) for directory in directories]
    if operation == "quote":
        args = ["stock", "quote", "005930"]
        expected = [("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                     RawResponse(rt_cd="0", msg_cd="", msg1="", body={"output": {
                         "stck_prpr": "70000", "stck_oprc": "69000", "stck_hgpr": "71000",
                         "stck_lwpr": "68000", "stck_sdpr": "69500", "prdy_vrss": "500",
                         "prdy_ctrt": "0.7", "acml_vol": "100", "prdy_vrss_sign": "2"}}))]
    elif operation in {"fills", "older_fills"}:
        args = ["account", "fills", "--start", "20260501", "--end", "20260531"]
        row = _row()
        row["ord_dt"] = "20260509"
        trs = ("VTTC0081R", "VTSC9215R")
        if operation == "older_fills":
            args += ["--older-than-three-months"]
            trs = ("VTSC9215R",)
        expected = [("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", tr, history_response(rows=[row]))
                    for tr in trs]
    else:
        args = ["account", "positions", "--region", "overseas", "--market", "US"]
        expected = [("/uapi/overseas-stock/v1/trading/inquire-balance", "VTTS3012R", position_response([_holding()]))]
    transport = StrictReadTransport(expected)
    original = KISClient
    monkeypatch.setattr(context, "KISClient", lambda **kwargs: original(**kwargs, transport=transport))
    monkeypatch.setattr(context, "order_store_path", lambda **kwargs: pytest.fail("read resolved durable path"))
    parsed = app.build_parser().parse_args(args)
    with context.build_client(parsed) as client:
        result = parsed.func(client, parsed)
        if operation == "quote":
            assert result.current_price == Decimal(70000)
        elif operation == "fills":
            assert result.orders[0].symbol == "005930"
            assert result.recent.orders and result.historical.orders
            assert result.orders[0].order_date.isoformat() == "2026-05-09"
        elif operation == "older_fills":
            assert result.orders[0].order_date.isoformat() == "2026-05-09"
        else:
            assert result[0].symbol == "AAPL" and result[0].quantity == Decimal(10)
    assert not transport.expected
    after = [(directory.stat().st_ino, {p.name: p.read_bytes() for p in directory.iterdir()}) for directory in directories]
    assert before == after

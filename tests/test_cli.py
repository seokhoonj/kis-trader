"""CLI(``kis``) -- 인자 배선, 출력 렌더링, 주문 안전 게이트, 오류 번역."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import pytest

import kis_trader.cli.app as cli_main
from kis_trader.cli.app import build_parser
from kis_trader.cli.commands import order
from kis_trader.cli.context import account_suffix
from kis_trader.cli.errors import CliAborted, CliConfigError, translate
from kis_trader.cli.output import _display_width, _pad, render, to_jsonable
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


@pytest.fixture(autouse=True)
def _isolate_credentials(tmp_path, monkeypatch):
    """CLI 테스트를 자격증명 해석에서 격리한다. 실 사용자의 ``~/.config/kis-trader`` 를 읽지 않도록
    XDG 경로를 빈 임시 디렉터리로 돌리고, 상속된 ``KIS_*`` 환경변수를 지운다 -- 각 테스트가 필요한
    자격증명만 명시적으로 설정하게 한다(hermetic + 실 자격증명 미접촉)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("KIS_PROFILE", raising=False)
    for var in [name for name in os.environ if name.startswith("KIS_")]:
        monkeypatch.delenv(var, raising=False)


# --- 스텁 클라이언트: 네트워크 없이 어떤 공개 메서드가 불렸는지만 기록 --------------

class _Handle:
    def __init__(self, log, code):
        self._log = log
        self._code = code

    def quote(self):
        self._log.append(("quote", self._code)); return "QUOTE"

    def buy(self, *, quantity, limit_price, division=None):
        self._log.append(("buy", self._code, quantity, limit_price, division)); return "REPORT"

    def sell(self, *, quantity, limit_price, division=None):
        self._log.append(("sell", self._code, quantity, limit_price, division)); return "REPORT"


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

    def modify(self, client_order_id, *, limit_price, quantity=None):
        self._log.append(("modify", client_order_id, limit_price, quantity)); return "REPORT"

    def cancel(self, client_order_id, *, quantity=None):
        self._log.append(("cancel", client_order_id, quantity)); return "REPORT"


class StubKis:
    def __init__(self, account=None, environment="paper"):
        self.log: list = []
        self.account = account        # 세션이 해석한 계좌(주문 게이트가 kis.account 로 읽음)
        self.environment = environment
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


def test_parser_defaults_to_main_and_table():
    from kis_trader.cli.context import resolve_environment
    args = _args(["search", "삼성전자"])
    assert args.profile == "main"                 # 기본 프로필 = 실전 주계좌
    assert resolve_environment(args) == "real"    # main 은 실전 환경
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
    jsonable_rows = to_jsonable(rows)
    assert jsonable_rows == [{"symbol": "005930", "price": "1"}, {"symbol": "000660", "price": "2"}]


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
    assert kis.log == [("buy", "005930", 10, "70000", None)]


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
    assert kis.log == [("buy", "005930", 10, None, "immediate_limit")]


def test_order_division_rejected_for_overseas():
    args = _args(["order", "buy", "AAPL", "10", "--venue", "overseas",
                  "--division", "immediate_limit"])
    with pytest.raises(CliConfigError):  # KRX 주문구분은 국내 전용
        order.cmd_buy(StubKis(), args, is_tty=False)


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
    assert kis.log == [("buy", "005930", 10, "70000", None)]


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
        def buy(self, *, quantity, limit_price, division=None):
            raise OrderTimeoutError("전송 시간초과", client_order_id="cid-1")

    class _Domestic:
        def stock(self, code):
            return _TimeoutHandle()

    class _Kis:
        account = None
        environment = "paper"
        domestic = _Domestic()

    monkeypatch.setattr(cli_main, "build_client", lambda args: _Kis())
    code = cli_main.main(["--profile", "paper", "order", "buy", "005930", "10",
                          "--limit-price", "70000", "--execute", "paper", "--yes", "--format", "json"])
    assert code == 7  # 결과 불명 -> reconcile
    err = capsys.readouterr().err
    assert "reconcile" in err and "unknown" in err


def test_render_table_covers_list_single_dict_and_empty():
    rows = [_Row("005930", Decimal("71500"), {}), _Row("000660", Decimal("120000"), {})]
    table = render(rows, fmt="table")
    assert "symbol" in table and "005930" in table and "000660" in table
    single = render(_Row("005930", Decimal("71500"), {}), fmt="table")
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

    obj = _Balance("KRW", Decimal("399684"), Decimal("127776156"), {})
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
        bids=[PriceLevel(Decimal("274000"), 100), PriceLevel(Decimal("273500"), 50)],
        asks=[PriceLevel(Decimal("274500"), 80), PriceLevel(Decimal("275000"), 60)],
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
    monkeypatch.setenv("KIS_PAPER_CANO", "12345678")
    monkeypatch.setenv("KIS_PAPER_ACNT_PRDT_CD", "01")
    kis = build_client(_args(["--profile", "paper", "stock", "quote", "005930"]))
    assert kis.account == "12345678-01"
    assert kis.environment == "paper"


def test_build_client_missing_credentials_raises_config_error():
    from kis_trader.cli.context import build_client
    with pytest.raises(CliConfigError):  # KIS_* 없음(격리 픽스처) -> exit 3
        build_client(_args(["--profile", "paper", "stock", "quote", "005930"]))

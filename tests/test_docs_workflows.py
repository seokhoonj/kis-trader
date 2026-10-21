"""Trusted source-bound adapters. Documentation is parsed, never exec'd."""

import ast
import hashlib
import json
import os
import shlex
import socket
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_published_skill_has_current_state_recipe_with_real_signature():
    import inspect

    from tools.check_docs import extract_documents

    from kis_trader._order_engine import OrderEngine

    spans, errors = extract_documents(
        ROOT, ["plugins/kis-trader/skills/kis-trader/SKILL.md"]
    )
    assert not errors
    calls = [
        node
        for span in spans
        if span.kind == "fence" and span.language == "python"
        for node in ast.walk(ast.parse(span.text))
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "kis.orders.reconcile"
    ]
    refresh_calls = [
        call for call in calls
        if any(keyword.arg == "refresh" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is True for keyword in call.keywords)
    ]
    assert refresh_calls, "The current-state recipe must distinguish refresh from cached reports"
    for call in refresh_calls:
        assert len(call.args) == 1
        assert all(keyword.arg == "refresh" for keyword in call.keywords)
        inspect.signature(OrderEngine.reconcile).bind(None, "synthetic-request-id", refresh=True)


def blocks(file):
    from tools.check_docs import extract_documents

    registry = json.loads((ROOT / "tests/docs_contracts.json").read_text())
    assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == registry["files"][file]
    return [s for s in extract_documents(ROOT, [file])[0] if s.kind == "fence"]


def literal_args(call):
    return [ast.literal_eval(a) for a in call.args], {
        k.arg: ast.literal_eval(k.value) for k in call.keywords
    }


def test_documented_combined_history_uses_both_sources_and_keeps_summaries():
    from kis_trader import KISClient, OrderStore, StockOrderHistoryCollection
    from kis_trader.cli.output import to_jsonable
    from kis_trader.transport import RawResponse

    calls = [
        node
        for block in blocks("docs/account.md")
        if block.language == "python"
        for node in ast.walk(ast.parse(block.text))
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "account.combined_order_history"
    ]
    calls = [call for call in calls if literal_args(call)[1]["start"] == "20240101"]
    assert len(calls) == 1, "The account guide must include the short combined query recipe"
    args, kwargs = literal_args(calls[0])
    assert not args and set(kwargs) == {"start", "end"}

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, *, method, path, tr_id, params=None, body=None,
                    idempotent, tr_cont=""):
            assert method == "GET" and path.endswith("/inquire-daily-ccld")
            assert params["INQR_STRT_DT"] == kwargs["start"]
            assert params["INQR_END_DT"] == kwargs["end"]
            assert body is None and idempotent and tr_cont == ""
            self.calls.append(tr_id)
            assert self.calls in (["TTTC0081R"], ["TTTC0081R", "CTSC9215R"])
            index = len(self.calls)
            summary = {
                "tot_ord_qty": "1", "tot_ccld_qty": "1", "tot_ccld_amt": str(index * 100),
                "pchs_avg_pric": str(index * 100), "prsm_tlex_smtl": str(index),
            }
            row = {
                "ord_dt": "20240102" if index == 1 else "20240202", "odno": "SYNTHETIC",
                "ord_gno_brno": "SYNTHETIC_BRANCH", "ord_orgno": "SYNTHETIC_ORG",
                "sll_buy_dvsn_cd": "02", "pdno": "005930", "ord_qty": "1",
            }
            return RawResponse("0", "synthetic", "", {"output1": [row], "output2": summary})

    transport = Transport()
    with OrderStore() as store, KISClient(
        app_key="synthetic", app_secret="synthetic", account="synthetic-01",
        environment="real", transport=transport, store=store,
    ) as client:
        result = client.account.domestic.combined_order_history(**kwargs)
    assert isinstance(result, StockOrderHistoryCollection)
    assert transport.calls == ["TTTC0081R", "CTSC9215R"]
    assert len(result.orders) == 2
    assert result.recent.average_purchase_price == Decimal(100)
    assert result.historical.average_purchase_price == Decimal(200)
    assert result.recent.estimated_expenses == Decimal(1)
    assert result.historical.estimated_expenses == Decimal(2)
    assert set(to_jsonable(result)) == {"orders", "recent", "historical"}


def test_documented_long_history_preserves_every_period_and_provider_summary():
    from datetime import date, timedelta

    from kis_trader import KISClient, OrderStore
    from kis_trader.cli.output import to_jsonable
    from kis_trader.transport import RawResponse

    calls = [
        node
        for block in blocks("docs/account.md")
        if block.language == "python"
        for node in ast.walk(ast.parse(block.text))
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "account.combined_order_history"
        and literal_args(node)[1]["start"] == "20200509"
    ]
    assert len(calls) == 1, "The account guide must demonstrate the original long query"
    _, kwargs = literal_args(calls[0])
    assert kwargs == {"start": "20200509", "end": "20261022"}

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, *, method, path, tr_id, params=None, body=None,
                    idempotent, tr_cont=""):
            assert method == "GET" and path.endswith("/inquire-daily-ccld")
            assert body is None and idempotent and tr_cont == ""
            start = date.fromisoformat(
                f'{params["INQR_STRT_DT"][:4]}-{params["INQR_STRT_DT"][4:6]}-{params["INQR_STRT_DT"][6:]}'
            )
            end = date.fromisoformat(
                f'{params["INQR_END_DT"][:4]}-{params["INQR_END_DT"][4:6]}-{params["INQR_END_DT"][6:]}'
            )
            assert 0 <= (end - start).days < 365
            self.calls.append((tr_id, start, end))
            index = len(self.calls)
            summary = {
                "tot_ord_qty": "1", "tot_ccld_qty": "1", "tot_ccld_amt": str(index * 100),
                "pchs_avg_pric": str(index * 11), "prsm_tlex_smtl": str(index * 7),
                "period_marker": {"index": index},
            }
            row = {
                "ord_dt": params["INQR_STRT_DT"], "odno": f"SYNTHETIC{index}",
                "ord_gno_brno": "SYNTHETIC_BRANCH", "ord_orgno": "SYNTHETIC_ORG",
                "sll_buy_dvsn_cd": "02", "pdno": "005930", "ord_qty": "1",
            }
            return RawResponse("0", "synthetic", "", {"output1": [row], "output2": summary})

    transport = Transport()
    with OrderStore() as store, KISClient(
        app_key="synthetic", app_secret="synthetic", account="synthetic-01",
        environment="real", transport=transport, store=store,
    ) as client:
        result = client.account.domestic.combined_order_history(**kwargs)
    assert len(result.orders) == 14
    assert len(result.periods) == 7
    assert len(transport.calls) == 14
    assert result.periods[0].start == date(2020, 5, 9)
    assert result.periods[-1].end == date(2026, 10, 22)
    for index, period in enumerate(result.periods):
        if index:
            assert period.start == result.periods[index - 1].end + timedelta(days=1)
        assert transport.calls[2 * index:2 * index + 2] == [
            ("TTTC0081R", period.start, period.end), ("CTSC9215R", period.start, period.end),
        ]
        for offset, source in enumerate((period.history.recent, period.history.historical), 1):
            marker = 2 * index + offset
            assert source.average_purchase_price == Decimal(marker * 11)
            assert source.estimated_expenses == Decimal(marker * 7)
            assert source._raw["period_marker"]["index"] == marker
    encoded = to_jsonable(result, include_raw=True)
    assert set(encoded) == {"orders", "periods"}
    assert encoded["periods"][0]["start"] == "2020-05-09"
    assert encoded["periods"][-1]["end"] == "2026-10-22"
    assert encoded["periods"][-1]["history"]["historical"]["_raw"]["period_marker"] == {"index": 14}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("KIS_"):
            monkeypatch.delenv(name)
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        monkeypatch.setenv(name, str(tmp_path / name))

    def deny(*a, **kw):
        raise AssertionError("unexpected network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr("kis_trader.client._fill_credentials", deny)
    monkeypatch.setattr("kis_trader.client.load_overseas_index", deny)
    monkeypatch.setattr("kis_trader.client.load_domestic_index", deny)
    monkeypatch.setattr("kis_trader._internal._auth.TokenManager.access_token", deny)


def test_documented_blank_slot_and_actual_exports_resolve_from_empty(
    tmp_path, monkeypatch
):
    from kis_trader.config import resolve_credentials

    entries = blocks("docs/configuration.md")
    blank = next(
        json.loads(s.text)
        for s in entries
        if s.language == "json" and '"general": {}' in s.text
    )
    exports = next(
        s.text for s in entries if "export KIS_GENERAL_REAL_APP_KEY=" in s.text
    )
    env = {}
    for line in exports.splitlines():
        tokens = shlex.split(line, comments=True)
        assert tokens[0] == "export"
        key, value = tokens[1].split("=", 1)
        monkeypatch.setenv(key, value)
        env[key] = value
    (tmp_path / "credentials.json").write_text(json.dumps(blank))
    resolved = resolve_credentials(config_dir=tmp_path)
    assert (
        resolved.environment == env["KIS_DEFAULT_ENVIRONMENT"]
        and resolved.app_key == env["KIS_GENERAL_REAL_APP_KEY"]
    )
    # Removing setup is a meaningful recipe regression, not a hidden preseed.
    (tmp_path / "credentials.json").unlink()
    from kis_trader.errors import KISUsageError

    with pytest.raises(KISUsageError):
        resolve_credentials(config_dir=tmp_path)


def test_quickstart_literals_real_client_strict_response_parse():
    from kis_trader import KISClient, OrderStore
    from kis_trader.instrument import DomesticListingIndex, MasterIndex
    from kis_trader.transport import RawResponse

    entries = blocks("docs/quickstart.md")
    setup = next(s.text for s in entries if "kis = KISClient(" in s.text)
    tree = ast.parse(setup)
    call = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "KISClient"
    )
    _, kwargs = literal_args(call)
    query = next(s.text for s in entries if "stock = kis.domestic.stock(" in s.text)
    tree = ast.parse(query)
    args, kw = literal_args(tree.body[0].value)
    symbol = args[0]

    class Transport:
        environment = kwargs["environment"]

        def __init__(self):
            self.calls = 0

        def request(
            self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
        ):
            assert (method, path, tr_id, params, body, idempotent, tr_cont) == (
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
                None,
                True,
                "",
            )
            self.calls += 1
            return RawResponse(
                "0",
                "OK",
                "Synthetic",
                {
                    "output": {
                        "stck_prpr": "71500",
                        "stck_oprc": "71000",
                        "stck_hgpr": "72000",
                        "stck_lwpr": "70000",
                        "stck_sdpr": "71000",
                        "prdy_vrss": "500",
                        "prdy_vrss_sign": "2",
                        "prdy_ctrt": "0.70",
                        "acml_vol": "1234",
                    }
                },
            )

    transport = Transport()
    store = OrderStore()
    cano, product = kwargs["account"].split("-")
    store.bind_scope(cano=cano, product_code=product, environment=kwargs["environment"])
    with KISClient(
        **kwargs,
        transport=transport,
        store=store,
        master_index=MasterIndex([]),
        domestic_index=DomesticListingIndex([]),
    ) as client:
        stock = client.domestic.stock(*args, **kw)
        a, k = literal_args(tree.body[1].value)
        quote = stock.quote(*a, **k)
        members = [
            n.value.attr
            for n in tree.body[2:]
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Attribute)
        ]
        assert [getattr(quote, m) for m in members] == [
            Decimal(71500),
            Decimal(500),
            Decimal(".70"),
            1234,
        ]
        assert transport.calls == 1


def test_documented_realtime_signature_and_terminal_route():
    import inspect

    from kis_trader.realtime._connection import RealtimeMessage
    from kis_trader.realtime.client import _DISPATCH_SENTINEL, RealtimeClient
    from kis_trader.realtime.parsers.domestic_stock import (
        _TRADE_TICK_FIELDS,
        StockTick,
        parse_trade_tick,
    )

    entries = blocks("docs/realtime.md")
    rt = RealtimeClient("synthetic", "wss://invalid", connect=None)
    raw = next(s for s in entries if "realtime.subscribe(" in s.text)
    call = next(
        n
        for n in ast.walk(ast.parse(raw.text))
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "realtime.subscribe"
    )
    inspect.signature(rt.subscribe).bind(
        **{
            k.arg: (print if k.arg == "on" else ast.literal_eval(k.value))
            for k in call.keywords
        }
    )
    typed = next(s for s in entries if "sub = realtime.domestic.stock(" in s.text)
    call = next(
        n.value
        for n in ast.parse(typed.text).body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "sub"
    )
    a, k = literal_args(call.func.value)
    sub = rt.domestic.stock(*a, **k).trades()
    values = {"MKSC_SHRN_ISCD": sub.tr_key, "STCK_PRPR": "71500", "CNTG_VOL": "3"}
    tick = parse_trade_tick([values.get(field, "0") for field in _TRADE_TICK_FIELDS])
    assert isinstance(tick, StockTick)
    rt._dispatch(RealtimeMessage(sub.tr_id, sub.tr_key, tick))
    rt._dispatch_queue.put(_DISPATCH_SENTINEL)
    rt._dispatch_worker()
    sub.close()
    assert list(sub) == [tick] and list(sub) == []


def test_actual_aapl_auto_master_then_positions_us_cli_route(monkeypatch):
    from kis_trader import KISClient, OrderStore
    from kis_trader.cli.app import build_parser
    from kis_trader.instrument import (
        DomesticListingIndex,
        InstrumentRecord,
        MasterIndex,
    )
    from kis_trader.transport import RawResponse

    def deny(*a, **k):
        raise AssertionError("auth/credential/master fallback forbidden")

    monkeypatch.setattr("kis_trader.client._fill_credentials", deny)
    monkeypatch.setattr("kis_trader.client.load_overseas_index", deny)
    monkeypatch.setattr("kis_trader.client.load_domestic_index", deny)
    monkeypatch.setattr("kis_trader._internal._auth.TokenManager.access_token", deny)
    setup = next(
        s.text for s in blocks("docs/quickstart.md") if "kis = KISClient(" in s.text
    )
    call = next(
        n
        for n in ast.walk(ast.parse(setup))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "KISClient"
    )
    _, kwargs = literal_args(call)
    source = next(
        s.text
        for s in blocks("docs/overseas.md")
        if "stock = kis.overseas.stock(" in s.text
    )
    tree = ast.parse(source)
    args, kw = literal_args(tree.body[0].value)
    assert not kw, "source must exercise AUTO exchange"
    symbol = args[0]
    command = next(
        line.strip()
        for line in (ROOT / "docs/cli.md").read_text().splitlines()
        if line.startswith("kis account positions ")
    )
    parsed = build_parser().parse_args(shlex.split(command, comments=True)[1:])
    assert parsed.region == "overseas" and parsed.market == "US"
    cano, product = kwargs["account"].split("-")

    class ScriptedTransport:
        environment = kwargs["environment"]

        def __init__(self):
            self.calls = []

        def request(
            self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
        ):
            assert method == "GET" and body is None and idempotent and tr_cont == ""
            self.calls.append(path)
            if len(self.calls) == 1:
                assert (
                    path == "/uapi/overseas-price/v1/quotations/price-detail"
                    and tr_id == "HHDFS76200200"
                    and params == {"AUTH": "", "EXCD": "NAS", "SYMB": symbol}
                )
                return RawResponse(
                    "0",
                    "OK",
                    "Synthetic",
                    {
                        "output": {
                            "last": "150",
                            "base": "148",
                            "curr": "USD",
                            "open": "149",
                            "high": "151",
                            "low": "147",
                            "tvol": "123",
                            "h52p": "200",
                            "l52p": "100",
                        }
                    },
                )
            assert (
                len(self.calls) == 2
                and path == "/uapi/overseas-stock/v1/trading/inquire-balance"
                and tr_id
                == ("VTTS3012R" if self.environment == "paper" else "TTTS3012R")
            )
            assert params == {
                "CANO": cano,
                "ACNT_PRDT_CD": product,
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            }
            return RawResponse(
                "0",
                "OK",
                "Synthetic",
                {
                    "output1": [
                        {
                            "ovrs_pdno": symbol,
                            "ovrs_item_name": "Synthetic",
                            "ovrs_cblc_qty": "1.5",
                            "ord_psbl_qty": "1",
                            "pchs_avg_pric": "148",
                            "now_pric2": "150",
                            "frcr_pchs_amt1": "222",
                            "ovrs_stck_evlu_amt": "225",
                            "frcr_evlu_pfls_amt": "3",
                            "evlu_pfls_rt": "1.35",
                        }
                    ]
                },
            )

    t = ScriptedTransport()
    master = MasterIndex(
        [
            InstrumentRecord(
                symbol, "NAS", "USD", "stock", "Synthetic", "Synthetic", "NAS" + symbol
            )
        ]
    )
    store = OrderStore()
    store.bind_scope(cano=cano, product_code=product, environment=kwargs["environment"])
    with KISClient(
        **kwargs,
        transport=t,
        store=store,
        master_index=master,
        domestic_index=DomesticListingIndex([]),
    ) as client:
        stock = client.overseas.stock(*args, **kw)
        a, k = literal_args(tree.body[1].value)
        quote = stock.quote(*a, **k)
        assert (
            quote.symbol == symbol
            and quote.exchange == "NAS"
            and quote.currency == "USD"
            and quote.current_price == Decimal(150)
        )
        positions = parsed.func(client, parsed)
        assert (
            len(positions) == 1
            and positions[0].quantity == Decimal("1.5")
            and positions[0].exchange == "NASD"
        )
        assert len(t.calls) == 2
    # Historical wrong venue spelling MUST reject, while correct region preserves US market.
    bad = shlex.split(command, comments=True)[1:]
    bad[bad.index("--region")] = "--venue"
    with pytest.raises(SystemExit):
        build_parser().parse_args(bad)


def test_explicit_legacy_attestation_tutorial_preserves_pending_and_scope(tmp_path):
    from kis_trader import OrderStore
    from kis_trader.errors import KISUsageError
    from kis_trader.order import Order

    setup = next(
        s.text for s in blocks("docs/quickstart.md") if "kis = KISClient(" in s.text
    )
    constructor = next(
        n
        for n in ast.walk(ast.parse(setup))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "KISClient"
    )
    _, source_values = literal_args(constructor)
    cano, product = source_values["account"].split("-")
    # Synthetic operator knows this fixture's owner. Filename intentionally supplies no ownership proof.
    owner = {
        "cano": cano, "product_code": product, "environment": source_values["environment"]
    }
    path = tmp_path / "unrelated-profile-hint.json"
    order = Order(
        symbol="005930",
        side="buy",
        order_type="limit",
        quantity=10,
        limit_price=70000,
        client_order_id="synthetic-legacy",
    )
    with OrderStore(path=path) as raw:
        raw.try_claim(order.client_order_id, order.fingerprint)
    data = json.loads(path.read_text())
    data["schema_version"] = 10
    data.pop("scope", None)
    data.pop("submission_anchors", None)
    path.write_text(json.dumps(data))
    original_bytes = path.read_bytes()
    tutorial = next(
        s.text for s in blocks("docs/orders.md") if "adopt_legacy=True" in s.text
    )
    source_call = next(
        n
        for n in ast.walk(ast.parse(tutorial))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "bind_scope"
    )
    keyword = next(k for k in source_call.keywords if k.arg == "adopt_legacy")
    assert ast.literal_eval(keyword.value) is True
    with OrderStore(path=path) as store:
        before = store.snapshot_for(order.client_order_id)
        with pytest.raises(KISUsageError):
            store.bind_scope(**owner)
        assert path.read_bytes() == original_bytes
        operator_has_attested = False
        with pytest.raises(KISUsageError):
            store.bind_scope(**owner, adopt_legacy=operator_has_attested)
        assert path.read_bytes() == original_bytes
        # True is explicitly supplied after this synthetic operator's external ownership check.
        operator_has_attested = True
        store.bind_scope(**owner, adopt_legacy=operator_has_attested)
        assert store.snapshot_for(order.client_order_id) == before
        assert store.is_in_flight(order.client_order_id)
        adopted_bytes = path.read_bytes()
        store.bind_scope(**owner)
        assert path.read_bytes() == adopted_bytes
        with pytest.raises(KISUsageError):
            store.bind_scope(
                **{
                    **owner,
                    "environment": "real"
                    if owner["environment"] == "paper"
                    else "paper",
                },
                adopt_legacy=True,
            )
        assert path.read_bytes() == adopted_bytes
    with OrderStore(path=path) as reopened:
        reopened.bind_scope(**owner)
        assert reopened.is_in_flight(order.client_order_id)


def _read_order_book_recipe(source, stock):
    """호가 예제의 대입·측면 검사·0번 호가·출력만 실제 객체에 연결한다."""
    from kis_trader.order_book import OrderBook, PriceLevel

    values = {}
    output = []

    def value(expression):
        if isinstance(expression, ast.Name):
            return values[expression.id]
        if isinstance(expression, ast.Constant):
            return expression.value
        if isinstance(expression, ast.Attribute):
            owner = value(expression.value)
            allowed = (
                {"bids", "asks", "total_bid_quantity", "total_ask_quantity"}
                if isinstance(owner, OrderBook)
                else {"price", "quantity"} if isinstance(owner, PriceLevel) else set()
            )
            assert expression.attr in allowed, "unsupported order-book member"
            return getattr(owner, expression.attr)
        if isinstance(expression, ast.Subscript):
            assert ast.literal_eval(expression.slice) == 0
            return value(expression.value)[0]
        if isinstance(expression, ast.Call):
            assert ast.unparse(expression.func) == "stock.order_book"
            args, kwargs = literal_args(expression)
            return stock.order_book(*args, **kwargs)
        raise TypeError("unsupported order-book expression")

    def statements(entries):
        for entry in entries:
            if isinstance(entry, ast.Assign):
                assert len(entry.targets) == 1 and isinstance(entry.targets[0], ast.Name)
                values[entry.targets[0].id] = value(entry.value)
            elif isinstance(entry, ast.If):
                assert not entry.orelse and isinstance(entry.test, ast.Attribute)
                assert isinstance(entry.test.value, ast.Name) and entry.test.value.id == "order_book"
                assert entry.test.attr in {"bids", "asks"}
                if value(entry.test):
                    statements(entry.body)
            elif isinstance(entry, ast.Expr) and isinstance(entry.value, ast.Call):
                assert isinstance(entry.value.func, ast.Name) and entry.value.func.id == "print"
                assert not entry.value.keywords
                output.append(tuple(value(argument) for argument in entry.value.args))
            else:
                raise TypeError("unsupported order-book statement")

    statements(ast.parse(source).body)
    return values["order_book"], output


@pytest.mark.parametrize("bid_present,ask_present", [(False, False), (True, False), (False, True), (True, True)])
def test_documented_order_book_guards_real_empty_and_one_sided_parser(bid_present, ask_present):
    from tools.check_docs import extract_documents

    from kis_trader import KISClient, OrderStore
    from kis_trader.instrument import DomesticListingIndex, MasterIndex
    from kis_trader.transport import RawResponse

    spans, errors = extract_documents(ROOT, ["docs/quotes.md"])
    assert not errors
    factory_source = next(span.text for span in spans if span.kind == "fence" and "stock = kis.domestic.stock(" in span.text)
    factory = ast.parse(factory_source).body[0].value
    assert ast.unparse(factory.func) == "kis.domestic.stock"
    factory_args, factory_kwargs = literal_args(factory)
    symbol = factory_args[0]
    recipe = next(span.text for span in spans if span.kind == "fence" and "order_book = stock.order_book()" in span.text)
    constructor_source = next(span.text for span in blocks("docs/quickstart.md") if "kis = KISClient(" in span.text)
    constructor = next(node for node in ast.walk(ast.parse(constructor_source))
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "KISClient")
    _, client_kwargs = literal_args(constructor)
    response = {"total_bidp_rsqn": "150" if bid_present else "0",
                "total_askp_rsqn": "100" if ask_present else "0"}
    for level in range(1, 11):
        response[f"bidp{level}"] = "71500" if bid_present and level == 1 else "0"
        response[f"bidp_rsqn{level}"] = "150" if bid_present and level == 1 else "0"
        response[f"askp{level}"] = "71600" if ask_present and level == 1 else "0"
        response[f"askp_rsqn{level}"] = "100" if ask_present and level == 1 else "0"

    class Transport:
        environment = client_kwargs["environment"]

        def __init__(self):
            self.calls = 0

        def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
            assert (method, path, tr_id, params, body, idempotent, tr_cont) == (
                "GET", "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                "FHKST01010200", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
                None, True, "",
            )
            self.calls += 1
            assert self.calls == 1
            return RawResponse("0", "OK", "Synthetic", {"output1": response})

    transport = Transport()
    with OrderStore() as store, KISClient(
        **client_kwargs, transport=transport, store=store,
        master_index=MasterIndex([]), domestic_index=DomesticListingIndex([]),
    ) as client:
        stock = client.domestic.stock(*factory_args, **factory_kwargs)
        book, output = _read_order_book_recipe(recipe, stock)
        expected = []
        if bid_present:
            expected.append(("매수", Decimal(71500), 150))
        if ask_present:
            expected.append(("매도", Decimal(71600), 100))
        expected.append((150 if bid_present else 0, 100 if ask_present else 0))
        assert output == expected
        assert len(book.bids) == int(bid_present) and len(book.asks) == int(ask_present)
        assert transport.calls == 1

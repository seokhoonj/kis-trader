"""Synthetic regressions for bounded domestic stock history periods."""
from dataclasses import FrozenInstanceError, fields
from datetime import date, timedelta
from decimal import Decimal

import pytest

from kis_trader.cli.output import to_jsonable
from kis_trader.errors import KISError
from test_combined_stock_orders import HistoryTransport, query, response, row


def windows(start, end):
    current = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    result = []
    while True:
        stop = date.fromordinal(min(current.toordinal() + 364, finish.toordinal()))
        result.append((current, stop))
        if stop == finish:
            return result
        current = stop + timedelta(days=1)


def wire(value):
    return f"{value.year:04d}{value.month:02d}{value.day:02d}"


@pytest.mark.parametrize(("start", "end"), [
    ("2024-01-01", "2024-12-30"), ("2024-01-01", "2024-12-31"),
    ("2020-05-09", "2026-10-22"), ("0001-01-01", "0002-01-01"),
    ("9998-12-31", "9999-12-31"), ("2023-12-31", "2024-01-01"),
    ("2024-02-29", "2024-02-29"),
])
@pytest.mark.parametrize("environment", ["real", "paper"])
def test_inclusive_boundaries_and_original_range(start, end, environment):
    from kis_trader import StockOrderHistoryCollection, StockOrderHistorySeries
    expected = windows(start, end)
    transport = HistoryTransport([response() for _ in range(len(expected) * 2)])
    result = query(transport, start=wire(expected[0][0]), end=wire(expected[-1][1]),
                   environment=environment, side="sell", symbol="005930", unfilled_only=True)
    assert isinstance(result, StockOrderHistoryCollection if len(expected) == 1 else StockOrderHistorySeries)
    assert [(p["INQR_STRT_DT"], p["INQR_END_DT"]) for _, p, _ in transport.calls] == [
        (wire(first), wire(last)) for first, last in expected for _ in range(2)]
    assert [tr for tr, _, _ in transport.calls] == (["TTTC0081R", "CTSC9215R"] if environment == "real"
                                                    else ["VTTC0081R", "VTSC9215R"]) * len(expected)
    for _, params, _ in transport.calls:
        assert (params["SLL_BUY_DVSN_CD"], params["PDNO"], params["CCLD_DVSN"]) == ("01", "005930", "02")
    if len(expected) > 1:
        assert [(period.start, period.end) for period in result.periods] == expected


def test_periods_preserve_summaries_multiplicity_missing_identity_and_json():
    from kis_trader import StockOrderHistoryPeriod, StockOrderHistorySeries
    pages = []
    for day in ("20240101", "20241231"):
        item = row(ord_dt=day)
        missing = row(ord_dt=day, odno="")
        pages.extend([response([item, item, missing], average="11", expenses="2"),
                      response([item, missing], average="999", expenses="77")])
    result = query(HistoryTransport(pages), start="20240101", end="20241231")
    assert len(result.orders) == 8
    assert [field.name for field in fields(result)] == ["orders", "periods"]
    for period in result.periods:
        assert len(period.history.orders) == 4
        assert period.history.recent.average_purchase_price == Decimal(11)
        assert period.history.historical.average_purchase_price == Decimal(999)
        assert period.history.recent.estimated_expenses == Decimal(2)
        assert period.history.historical._raw["prsm_tlex_smtl"] == "77"
    data = to_jsonable(result)
    assert set(data) == {"orders", "periods"}
    assert set(data["periods"][0]) == {"start", "end", "history"}
    assert data["periods"][0]["start"] == "2024-01-01"
    assert data["periods"][1]["history"]["historical"]["estimated_expenses"] == "77"
    normalized = StockOrderHistorySeries(orders=list(result.orders), periods=list(result.periods))
    assert isinstance(normalized.orders, tuple) and isinstance(normalized.periods, tuple)
    assert isinstance(normalized.periods[0], StockOrderHistoryPeriod)
    with pytest.raises(FrozenInstanceError):
        normalized.periods[0].end = date(2025, 1, 1)


@pytest.mark.parametrize("bad_date", ["", "20231231", "20241231"])
@pytest.mark.parametrize("source", [0, 1])
def test_all_source_dates_must_belong_to_period_before_merge(bad_date, source):
    pages = [response(), response()]
    pages[source] = response([row(ord_dt=bad_date)])
    with pytest.raises(KISError):
        query(HistoryTransport(pages), start="20240101", end="20241231")


def test_each_period_source_paginates_independently_and_late_failure_aborts():
    pages = []
    for day in ("20240101", "20241231"):
        for source in ("recent", "historical"):
            pages.extend([response([row(ord_dt=day, odno=source)], cursor=source, continuation="F"),
                          response([row(ord_dt=day, odno=source + "2")])])
    transport = HistoryTransport(pages)
    result = query(transport, start="20240101", end="20241231")
    assert len(result.orders) == 8
    assert [(params["CTX_AREA_NK100"], cont) for _, params, cont in transport.calls] == [
        ("", ""), ("recent", "N"), ("", ""), ("historical", "N")] * 2
    pages[-1] = KISError("late final source page")
    with pytest.raises(KISError, match="late final source page"):
        query(HistoryTransport(pages), start="20240101", end="20241231")


@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_failure_in_any_period_source_aborts(position):
    pages = [response() for _ in range(4)]
    pages[position] = KISError("source unavailable")
    transport = HistoryTransport(pages)
    with pytest.raises(KISError, match="source unavailable"):
        query(transport, start="20240101", end="20241231")
    assert len(transport.calls) == position + 1


def test_later_period_conflicting_observations_fail_whole_result():
    item = row(ord_dt="20241231")
    transport = HistoryTransport([response(), response(), response([item]),
                                  response([item | {"tot_ccld_qty": "9"}])])
    with pytest.raises(KISError, match="관측"):
        query(transport, start="20240101", end="20241231")


def test_cli_fills_serializes_long_result_through_real_handler():
    from kis_trader import KISClient
    from kis_trader.cli.app import build_parser
    transport = HistoryTransport([response(), response(), response([row(ord_dt="20241231")]), response()])
    parsed = build_parser().parse_args(["account", "fills", "--start", "20240101", "--end", "20241231"])
    with KISClient(app_key="synthetic", app_secret="synthetic", account="12345678-01",
                   environment="real", transport=transport) as client:
        result = parsed.func(client, parsed)
    data = to_jsonable(result)
    assert set(data) == {"orders", "periods"}
    assert len(data["periods"]) == 2 and len(data["orders"]) == 1
    assert data["periods"][1]["start"] == "2024-12-31"


def test_explicit_single_family_long_range_stays_single_request():
    from kis_trader import KISClient
    transport = HistoryTransport([response()])
    with KISClient(app_key="synthetic", app_secret="synthetic", account="12345678-01",
                   environment="real", transport=transport) as client:
        client.account.domestic.order_history(start="20200509", end="20261022", older_than_three_months=True)
    assert len(transport.calls) == 1
    assert transport.calls[0][1]["INQR_STRT_DT"] == "20200509"


@pytest.mark.parametrize("missing", ["odno", "ord_gno_brno", "ord_orgno"])
@pytest.mark.parametrize("value", [None, "", " ", "0000000000"])
def test_long_period_keeps_missing_identity_observations(missing, value):
    item = row(ord_dt="20240101", **{missing: value})
    transport = HistoryTransport([response([item]), response([item]), response(), response()])
    result = query(transport, start="20240101", end="20241231")
    # All-zero order numbers are unknown, whereas nonblank organization strings are identities.
    expected = 2 if value != "0000000000" or missing == "odno" else 1
    assert len(result.orders) == expected


@pytest.mark.parametrize("bad_date", [None, "not-a-date", "20240230", "20240101"])
def test_late_period_unknown_or_out_of_range_date_aborts(bad_date):
    transport = HistoryTransport([response(), response(), response(), response([row(ord_dt=bad_date)])])
    with pytest.raises(KISError):
        query(transport, start="20240101", end="20241231")


def test_original_range_has_independent_exact_coverage_oracle():
    transport = HistoryTransport([response() for _ in range(14)])
    result = query(transport, start="20200509", end="20261022")
    assert [(period.start.isoformat(), period.end.isoformat()) for period in result.periods] == [
        ("2020-05-09", "2021-05-08"), ("2021-05-09", "2022-05-08"),
        ("2022-05-09", "2023-05-08"), ("2023-05-09", "2024-05-07"),
        ("2024-05-08", "2025-05-07"), ("2025-05-08", "2026-05-07"),
        ("2026-05-08", "2026-10-22"),
    ]
    lengths = [(period.end - period.start).days + 1 for period in result.periods]
    assert lengths == [365, 365, 365, 365, 365, 365, 168]
    covered = [ordinal for period in result.periods
               for ordinal in range(period.start.toordinal(), period.end.toordinal() + 1)]
    assert len(covered) == len(set(covered)) == 2358
    assert covered == list(range(date(2020, 5, 9).toordinal(), date(2026, 10, 22).toordinal() + 1))


@pytest.mark.parametrize(("end", "type_name", "calls"), [
    ("20241230", "StockOrderHistoryCollection", 2),
    ("20241231", "StockOrderHistorySeries", 4),
])
def test_365_and_366_days_have_explicit_result_contract(end, type_name, calls):
    transport = HistoryTransport([response() for _ in range(calls)])
    result = query(transport, start="20240101", end=end)
    assert type(result).__name__ == type_name
    assert len(transport.calls) == calls


def test_period_source_raw_summary_is_deeply_immutable():
    from kis_trader.transport import RawResponse
    nested = {"values": ["original"]}
    summary_body = dict(response().body["output2"]) | {"nested": nested}
    raw_response = RawResponse(rt_cd="0", msg_cd="OK", msg1="synthetic", body={
        "output1": [], "output2": summary_body,
    })
    result = query(HistoryTransport([raw_response, response(), response(), response()]),
                   start="20240101", end="20241231")
    summary = result.periods[0].history.recent._raw
    with pytest.raises(TypeError):
        summary["pchs_avg_pric"] = "changed"
    with pytest.raises(TypeError):
        summary["nested"]["values"][0] = "changed"
    nested["values"][0] = "mutated source"
    assert summary["nested"]["values"] == ("original",)


@pytest.mark.parametrize("late_failure", [False, True])
def test_real_cli_main_emits_complete_json_or_no_stdout_on_late_failure(monkeypatch, capsys, late_failure):
    import json

    from kis_trader import KISClient
    from kis_trader.cli import app
    pages = [response(), response(), response([row(ord_dt="20241231")]),
             response(cursor="last", continuation="F"), response()]
    if late_failure:
        pages[-1] = KISError("synthetic final page failure")
    transport = HistoryTransport(pages)
    monkeypatch.setattr(app, "build_client", lambda args: KISClient(
        app_key="synthetic", app_secret="synthetic", account="12345678-01",
        environment="real", transport=transport))
    exit_code = app.main(["account", "fills", "--start", "20240101", "--end", "20241231", "--format", "json"])
    captured = capsys.readouterr()
    if late_failure:
        assert exit_code == 5
        assert captured.out == ""
        assert "synthetic final page failure" in captured.err
    else:
        assert exit_code == 0 and captured.err == ""
        payload = json.loads(captured.out)
        assert len(payload["data"]["periods"]) == 2
        assert len(payload["data"]["orders"]) == 1
    assert len(transport.calls) == 5

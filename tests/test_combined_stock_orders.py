"""Synthetic transport regressions for both-family domestic stock history."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date
from decimal import Decimal

import pytest

from kis_trader import KISClient
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse


class HistoryTransport:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        self.calls.append((tr_id, dict(params), tr_cont))
        assert method == "GET"
        assert path == "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        assert idempotent is True
        assert self.pages, "unexpected additional history request"
        response = self.pages.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def row(**changes):
    result = {
        "ord_dt": "20260511", "ord_gno_brno": "06010", "odno": "0000000001",
        "orgn_odno": "", "ord_dvsn_name": "limit", "sll_buy_dvsn_cd": "02",
        "pdno": "005930", "prdt_name": "synthetic", "ord_qty": "10", "ord_unpr": "100",
        "ord_tmd": "131438", "tot_ccld_qty": "10", "avg_prvs": "100",
        "cncl_yn": "", "tot_ccld_amt": "1000", "loan_dt": "", "ord_dvsn_cd": "00",
        "rmn_qty": "0", "rjct_qty": "0", "cnc_cfrm_qty": "0", "excg_id_dvsn_cd": "KRX",
        "excg_dvsn_cd": "02", "ccld_cndt_name": "", "prdt_type_cd": "",
        "ord_orgno": "06010", "rsvn_ord_end_dt": "", "stpm_cndt_pric": "0",
        "stpm_efct_occr_dtmd": "",
    }
    return result | changes


def response(rows=(), *, average="100", expenses="3", cursor="", continuation=""):
    return RawResponse(rt_cd="0", msg_cd="OK", msg1="synthetic", tr_cont=continuation, body={
        "output1": list(rows), "output2": {
            "tot_ord_qty": "10", "tot_ccld_qty": "10", "tot_ccld_amt": "1000",
            "pchs_avg_pric": average, "prsm_tlex_smtl": expenses,
        }, "ctx_area_fk100": "F" if cursor else "", "ctx_area_nk100": cursor,
    })


def query(transport, *, environment="real", **kwargs):
    # Explicit synthetic transport and account: no auth, real config or persistent store.
    with KISClient(app_key="synthetic", app_secret="synthetic", account="12345678-01",
                   environment=environment, transport=transport) as client:
        return client.account.domestic.combined_order_history(
            **({"start": "20260101", "end": "20261021"} | kwargs))


@pytest.mark.parametrize(("environment", "want"), [
    ("real", ["TTTC0081R", "CTSC9215R"]),
    ("paper", ["VTTC0081R", "VTSC9215R"]),
])
@pytest.mark.parametrize("recent", [[], [row(odno="recent", ord_dt="20261020")]])
def test_always_queries_both_families_with_exact_short_range(environment, want, recent):
    transport = HistoryTransport([response(recent), response([row()])])
    result = query(transport, environment=environment, side="sell", symbol="005930", unfilled_only=True)
    assert [order.order_id for order in result.orders] == (["recent"] if recent else []) + ["0000000001"]
    assert result.orders[-1].order_date == date(2026, 5, 11)
    assert [call[0] for call in transport.calls] == want
    assert not transport.pages
    for _, params, continuation in transport.calls:
        assert params["INQR_STRT_DT"] == "20260101"
        assert params["INQR_END_DT"] == "20261021"
        assert params["SLL_BUY_DVSN_CD"] == "01"
        assert params["PDNO"] == "005930"
        assert params["CCLD_DVSN"] == "02"
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        assert continuation == ""


def test_pagination_resets_cursors_between_families():
    transport = HistoryTransport([
        response([row(odno="r1")], cursor="recent", continuation="F"),
        response([row(odno="r2")]),
        response([row(odno="h1")], cursor="historical", continuation="M"),
        response([row(odno="h2")]),
    ])
    result = query(transport)
    assert [order.order_id for order in result.orders] == ["r1", "r2", "h1", "h2"]
    assert [(tr_id, params["CTX_AREA_FK100"], params["CTX_AREA_NK100"], cont)
            for tr_id, params, cont in transport.calls] == [
        ("TTTC0081R", "", "", ""), ("TTTC0081R", "F", "recent", "N"),
        ("CTSC9215R", "", "", ""), ("CTSC9215R", "F", "historical", "N"),
    ]


@pytest.mark.parametrize(("start", "end"), [
    ("20260229", "20261021"), ("2026011", "20261021"), ("202601001", "20261021"),
    ("２０２６０１０１", "20261021"), ("2026-01-01", "20261021"),
    ("20261022", "20261021"), ("20260101", "20261301"),
    ("20260101", "20261021 "), ("00000101", "20261021"),
])
def test_invalid_dates_fail_before_any_request(start, end):
    transport = HistoryTransport([])
    with pytest.raises(KISUsageError):
        query(transport, start=start, end=end)
    assert transport.calls == []


def test_leap_date_and_same_day_are_accepted():
    transport = HistoryTransport([response(), response()])
    assert query(transport, start="20240229", end="20240229").orders == ()
    assert [params["INQR_STRT_DT"] for _, params, _ in transport.calls] == ["20240229"] * 2


@pytest.mark.parametrize(("recent_count", "historical_count", "want_count"), [(1, 1, 1), (1, 2, 2), (2, 1, 2), (2, 0, 2)])
def test_complete_equal_duplicates_preserve_maximum_source_multiplicity(recent_count, historical_count, want_count):
    result = query(HistoryTransport([response([row()] * recent_count), response([row()] * historical_count)]))
    assert len(result.orders) == want_count
    assert len(result.recent.orders) == recent_count
    assert len(result.historical.orders) == historical_count


@pytest.mark.parametrize("changes", [
    {"ord_dt": "20260512"}, {"ord_gno_brno": "06011"}, {"ord_orgno": "06011"},
    {"orgn_odno": "original"}, {"pdno": "000660"}, {"sll_buy_dvsn_cd": "01"},
    {"excg_dvsn_cd": "12"}, {"excg_id_dvsn_cd": "NXT"},
])
def test_complete_identity_includes_date_both_organizations_and_order_context(changes):
    result = query(HistoryTransport([response([row()]), response([row(**changes)])]))
    assert len(result.orders) == 2


@pytest.mark.parametrize("missing", ["ord_dt", "odno", "ord_gno_brno", "ord_orgno"])
@pytest.mark.parametrize("blank", ["", " "])
def test_incomplete_identity_keeps_both_observations(missing, blank):
    # A blank date uses the parser's missing-date representation.
    if missing == "ord_dt" and blank == " ":
        blank = ""
    item = row(**{missing: blank})
    result = query(HistoryTransport([response([item]), response([item])]))
    assert len(result.orders) == 2


@pytest.mark.parametrize("changes", [{"tot_ccld_qty": "9"}, {"new_raw_field": "different"}])
@pytest.mark.parametrize("conflict_position", ["only", "after_equal", "recent_after_equal"])
def test_all_cross_source_observations_must_agree_before_deduplication(changes, conflict_position):
    recent = [row(), row(**changes)] if conflict_position == "recent_after_equal" else [row()]
    historical = [row(), row(**changes)] if conflict_position == "after_equal" else [row(**changes)]
    if conflict_position == "recent_after_equal":
        historical = [row()]
    transport = HistoryTransport([response(recent), response(historical)])
    with pytest.raises(KISError, match="관측|observation"):
        query(transport)


def test_within_source_only_differences_remain_untouched():
    result = query(HistoryTransport([response([row(), row(tot_ccld_qty="9")]), response()]))
    assert [order.filled_quantity for order in result.orders] == [Decimal(10), Decimal(9)]


@pytest.mark.parametrize("late_page", [False, True])
def test_second_source_and_late_page_failures_never_return_partial_success(late_page):
    pages = [response([row()])]
    if late_page:
        pages.append(response([row(odno="old")], cursor="next", continuation="F"))
    pages.append(KISError("synthetic source failure"))
    transport = HistoryTransport(pages)
    with pytest.raises(KISError, match="synthetic source failure"):
        query(transport)
    assert len(transport.calls) == (3 if late_page else 2)


def test_collection_preserves_source_summaries_and_serializes_the_exact_public_shape():
    from kis_trader import StockOrderHistoryCollection
    from kis_trader.cli.output import to_jsonable

    result = query(HistoryTransport([response([row()], average="11", expenses="2"),
                                    response([row()], average="999", expenses="77")]))
    assert isinstance(result, StockOrderHistoryCollection)
    assert [field.name for field in fields(result)] == ["orders", "recent", "historical"]
    assert result.recent.average_purchase_price == Decimal(11)
    assert result.historical.average_purchase_price == Decimal(999)
    assert result.recent.estimated_expenses == Decimal(2)
    assert result.historical.estimated_expenses == Decimal(77)
    assert result.recent._raw["pchs_avg_pric"] == "11"
    assert result.historical._raw["prsm_tlex_smtl"] == "77"
    data = to_jsonable(result)
    assert set(data) == {"orders", "recent", "historical"}
    assert len(data["orders"]) == 1
    assert data["recent"]["average_purchase_price"] == "11"
    assert data["historical"]["estimated_expenses"] == "77"
    collection = StockOrderHistoryCollection(orders=list(result.orders), recent=result.recent, historical=result.historical)
    assert isinstance(collection.orders, tuple)
    with pytest.raises(FrozenInstanceError):
        collection.orders = ()


def test_combined_keeps_meaningful_missing_ids_but_ignores_wholly_empty_padding():
    padding = [{}, {"odno": "", "ord_dt": None, "pdno": " "}]
    transport = HistoryTransport([response([*padding, row(odno="")]), response([*padding, row(odno="")])])
    result = query(transport)
    assert len(result.orders) == 2
    assert result.orders[0].order_id == result.orders[1].order_id == ""
    assert len(result.recent.orders) == len(result.historical.orders) == 1


def test_meaningful_missing_id_does_not_hide_malformed_other_fields():
    transport = HistoryTransport([response([row(odno="", tot_ccld_qty="invalid")]), response()])
    with pytest.raises(KISError):
        query(transport)


@pytest.mark.parametrize("older", [False, True])
def test_explicit_single_family_remains_single_family_and_keeps_padding_behavior(older):
    transport = HistoryTransport([response([row(odno=""), row()])])
    with KISClient(app_key="synthetic", app_secret="synthetic", account="12345678-01",
                   environment="real", transport=transport) as client:
        result = client.account.domestic.order_history(start="20260501", end="20260531", older_than_three_months=older)
    assert len(result.orders) == 1
    assert [call[0] for call in transport.calls] == ["CTSC9215R" if older else "TTTC0081R"]


@pytest.mark.parametrize("missing", [True, False])
def test_absent_or_none_order_id_is_incomplete_and_never_invented(missing):
    item = row(odno=None)
    if missing:
        del item["odno"]
    result = query(HistoryTransport([response([item]), response([item])]))
    assert [order.order_id for order in result.orders] == ["", ""]


@pytest.mark.parametrize("identity_field", ["ord_gno_brno", "ord_orgno"])
def test_none_organizational_identity_is_not_a_literal_identifier(identity_field):
    item = row(**{identity_field: None})
    result = query(HistoryTransport([response([item]), response([item])]))
    assert len(result.orders) == 2
    order = result.orders[0]
    assert (order.branch_number if identity_field == "ord_gno_brno" else order.order_organization_number) == ""


@pytest.mark.parametrize("order_id", ["0", "0000000000"])
def test_all_zero_order_id_remains_an_unidentified_observation(order_id):
    result = query(HistoryTransport([response([row(odno=order_id)]), response([row(odno=order_id)])]))
    assert [order.order_id for order in result.orders] == [order_id, order_id]

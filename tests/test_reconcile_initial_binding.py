from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kis_trader import ExecutionReport, KISClient, OrderStatus, OrderStore
from kis_trader.domestic._engine import derivative_orders as derivative
from kis_trader.domestic._engine import orders as domestic
from kis_trader.domestic._engine import reserved_orders as reserved
from kis_trader.order import ImmediateOrderFingerprint, ReservedOrderFingerprint
from kis_trader.overseas._engine import orders as overseas
from kis_trader.overseas._engine import reserved_orders as overseas_reserved


class ReadReached(RuntimeError):
    pass


class NoWire:
    def __init__(self):
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        raise ReadReached("synthetic read seam")


def immediate(**kwargs):
    values = {
        "symbol": "005930",
        "side": "buy",
        "order_type": "limit",
        "quantity": "1",
        "limit_price": "70000",
        "stop_price": "",
        "time_in_force": "day",
        "region": "domestic",
        "asset_class": "stock",
        "exchange": "KRX",
    }
    values.update(kwargs)
    return ImmediateOrderFingerprint(**values)


def reservation(exchange):
    return ReservedOrderFingerprint(
        symbol="005930",
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="70000",
        end_date="",
        exchange=exchange,
        overseas_exchange="HKS" if exchange.endswith("asia") else "NASD",
    )


LANES = [
    ("cash", immediate(), domestic.reconcile),
    ("credit", immediate(credit_type="21"), domestic.reconcile),
    (
        "day",
        immediate(symbol="101S03", asset_class="derivative", exchange="XKFE", derivative_item="01"),
        derivative.reconcile,
    ),
    (
        "night",
        immediate(
            symbol="101S03",
            asset_class="derivative",
            exchange="XKFE",
            derivative_item="01",
            session="night",
        ),
        derivative.reconcile,
    ),
    ("overseas", immediate(symbol="AAPL", region="overseas", exchange="NAS"), overseas.reconcile),
    (
        "overnight",
        immediate(symbol="AAPL", region="overseas", exchange="NAS", session="overnight"),
        overseas.reconcile,
    ),
    ("domestic-reserved", reservation("reserved"), reserved.reconcile_reserved_order),
    (
        "us-reserved",
        reservation("overseas-reserved"),
        overseas_reserved.reconcile_us_reserved_order,
    ),
    (
        "asia-reserved",
        reservation("overseas-reserved-asia"),
        overseas_reserved.reconcile_asia_reserved_order,
    ),
]


@pytest.mark.parametrize("name,fingerprint,consumer", LANES, ids=[x[0] for x in LANES])
@pytest.mark.parametrize("public", [False, True])
def test_fresh_unknown_preserves_pending_without_any_read_or_resend(
    name, fingerprint, consumer, public
):
    wire = NoWire()
    store = OrderStore()
    client = KISClient(
        app_key="synthetic",
        app_secret="synthetic",
        account="synthetic-01",
        environment="real",
        transport=wire,
        store=store,
    )
    store.try_claim("unknown", fingerprint)
    if public:
        result = client.orders.reconcile("unknown", refresh=True)
    else:
        result = consumer(
            wire,
            store,
            "unknown",
            cano="synthetic",
            product_code="01",
            environment="real",
            force_refresh=True,
        )
    assert result is None
    assert store.is_in_flight("unknown") and store.report_for("unknown") is None
    assert wire.calls == []
    assert type(store.classify("unknown", fingerprint)).__name__ == "InFlight"


@pytest.mark.parametrize(
    "name,fingerprint,consumer",
    [x for x in LANES if x[0] != "overnight"],
    ids=[x[0] for x in LANES if x[0] != "overnight"],
)
def test_confirmed_broker_number_still_reaches_existing_refresh_consumer(
    name, fingerprint, consumer
):
    store = OrderStore()
    wire = NoWire()
    report = ExecutionReport(
        client_order_id="known",
        order_id="000123",
        symbol=fingerprint.symbol,
        side="buy",
        status=OrderStatus.NEW,
        filled_quantity=Decimal(0),
        average_price=None,
        recorded_at=datetime.now(UTC),
    )
    store.record(report, fingerprint)
    with pytest.raises(ReadReached):
        consumer(
            wire,
            store,
            "known",
            cano="synthetic",
            product_code="01",
            environment="real",
            force_refresh=True,
        )
    assert wire.calls and all(call["method"] == "GET" for call in wire.calls)
    assert store.report_for("known") is report


@pytest.mark.parametrize("order_id", [None, "", "000000", "   "])
def test_report_without_valid_broker_number_cannot_refresh_by_economic_guess(order_id):
    store = OrderStore()
    wire = NoWire()
    fingerprint = immediate()
    report = ExecutionReport(
        client_order_id="unknown",
        order_id=order_id,
        symbol="005930",
        side="buy",
        status=OrderStatus.NEW,
        filled_quantity=Decimal(0),
        average_price=None,
        recorded_at=datetime.now(UTC),
    )
    store.record(report, fingerprint)
    assert (
        domestic.reconcile(
            wire,
            store,
            "unknown",
            cano="synthetic",
            product_code="01",
            environment="real",
            force_refresh=True,
        )
        is None
    )
    assert store.report_for("unknown") is report and wire.calls == []


def test_single_economically_equal_other_order_never_binds_fresh_request():
    from kis_trader.transport import RawResponse

    class EconomicSibling(NoWire):
        def request(self, **kwargs):
            self.calls.append(kwargs)
            return RawResponse(
                rt_cd="0",
                msg_cd="",
                msg1="",
                body={
                    "output1": [
                        {
                            "odno": "999",
                            "pdno": "005930",
                            "sll_buy_dvsn_cd": "02",
                            "ord_dvsn_cd": "00",
                            "ord_qty": "1",
                            "ord_unpr": "70000",
                            "tot_ccld_qty": "1",
                            "avg_prvs": "70000",
                            "ord_gno_brno": "",
                            "rjct_qty": "0",
                            "cncl_yn": "N",
                            "excg_id_dvsn_cd": "KRX",
                        }
                    ]
                },
            )

    store = OrderStore()
    fingerprint = immediate()
    wire = EconomicSibling()
    store.try_claim("lost-response", fingerprint)
    result = domestic.reconcile(
        wire, store, "lost-response", cano="synthetic", product_code="01", environment="real"
    )
    assert result is None
    assert store.is_in_flight("lost-response") and store.report_for("lost-response") is None
    assert wire.calls == []


def test_public_cash_unresolved_blocks_same_id_resubmission_and_wrappers():
    from argparse import Namespace

    from kis_trader.cli.commands.order import cmd_reconcile
    from kis_trader.errors import KISUsageError
    from kis_trader.mcp import handlers

    wire = NoWire()
    store = OrderStore()
    client = KISClient(
        app_key="synthetic",
        app_secret="synthetic",
        account="synthetic-01",
        environment="real",
        transport=wire,
        store=store,
    )
    fingerprint = immediate()
    store.try_claim("pending", fingerprint)
    assert cmd_reconcile(client, Namespace(client_order_id="pending")) is None
    result = handlers.reconcile(client, client_order_id="pending")
    assert result == {"client_order_id": "pending", "resolved": False, "report": None}
    with pytest.raises(KISUsageError):
        client.domestic.stock("005930").buy(
            quantity=1, limit_price=70000, client_order_id="pending"
        )
    assert store.is_in_flight("pending") and wire.calls == []


def test_private_night_consumer_without_broker_number_is_also_guarded():
    wire = NoWire()
    store = OrderStore()
    fingerprint = LANES[3][1]
    store.try_claim("night-pending", fingerprint)
    prior, _, anchor, generation = store._recovery_snapshot_for("night-pending")
    result = derivative._night_union_reconcile(
        wire,
        store,
        "night-pending",
        fingerprint,
        prior=prior,
        query_anchor=anchor,
        generation=generation,
        cano="synthetic",
        product_code="01",
        environment="real",
        now=None,
    )
    assert result is None and store.is_in_flight("night-pending") and wire.calls == []

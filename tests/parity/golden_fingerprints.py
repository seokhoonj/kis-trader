"""Golden on-disk fingerprint rows and wire-param dicts -- the byte-level safety net.

This module pins, as literal constants, two things produced for a representative order in every
codec branch:

* ``GOLDEN_FINGERPRINT[label]`` -- the 19-slot positional on-disk encoding
  (:func:`~kis_trader.order.encode_fingerprint`) of that order's request fingerprint.
* ``GOLDEN_WIRE[label]`` -- the outgoing wire request as a flat ``dict[str, str]``: the HTTP
  ``method``/``path``/``tr_id`` envelope plus every KIS body field (all KIS body keys are
  uppercase, so the three lowercase envelope keys never collide with a body field).

Immediate lanes use the production order factories and wire builders. Change and reserved lanes
are captured by driving the production submit paths through a recording real ``OrderStore`` and a
capture-then-stop transport. Equal output against these fixed literals proves a refactor changed
only names, not the bytes that reach disk or the wire.

The fixed account/environment inputs below (``_CANO``/``_PRODUCT``/``_ENV``) make the captured
wire deterministic; none of them affects the fingerprint (the request fingerprint is independent
of ``client_order_id`` and account). The literals are immutable: never regenerate an existing one
from HEAD; a new case is added with an independently verified expected encoding.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kis_trader import _order_core as _core
from kis_trader.domestic._engine import bond_orders as _bond
from kis_trader.domestic._engine import derivative_orders as _dom_deriv
from kis_trader.domestic._engine import orders as _dom
from kis_trader.domestic._engine import reserved_orders as _dom_resv
from kis_trader.order import (
    Order,
    OrderFingerprint,
    WireRequest,
)
from kis_trader.overseas._engine import derivative_orders as _ovs_fo
from kis_trader.overseas._engine import orders as _ovs
from kis_trader.overseas._engine import reserved_orders as _ovs_resv
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import Binding, OrderStore

# --- fixed capture inputs (deterministic; none affect the fingerprint) ------------------------
_CANO = "12345678"
_PRODUCT = "01"
_ENV = "real"
#: Fixed original client-order-id for the CHANGE lanes -- it IS slot 0 of a change fingerprint,
#: so it must be stable across captures.
_ORIG_CID = "20261006-000000000000cafe"
#: Fixed id for the captured change request or reserved order. It does not enter either captured
#: fingerprint, but keeping it fixed makes every production-entry-point invocation deterministic.
_CAPTURE_CID = "20261006-000000000000beef"
#: Fixed original exchange order-number / organization-number for the synthetic prior report the
#: change-wire builders read (ORGN_ODNO / KRX_FWDG_ORD_ORGNO). Neither affects the fingerprint.
_ORIG_ODNO = "0000012345"
_ORIG_ORG = "06010"
_ORIG_RECEIPT_DATE = "20261006"
#: Any value -- recorded_at never reaches the wire or the fingerprint.
_RECORDED_AT = datetime(2026, 10, 6, tzinfo=UTC)


class _Captured(Exception):
    """Private capture-then-stop sentinel raised at the production transport boundary."""


class _CapturingTransport:
    """Record the production wire call, then stop before response parsing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []

    def request(
        self, *, method: str, path: str, tr_id: str, body: dict[str, str], idempotent: bool,
    ) -> None:
        self.calls.append((method, path, tr_id, dict(body)))
        raise _Captured


class _RecordingStore:
    """Delegate to a real store while recording fingerprints passed to ``try_claim``."""

    def __init__(self, store: OrderStore) -> None:
        self._store = store
        self.fingerprints: list[OrderFingerprint] = []

    def try_claim(self, key: str, fingerprint: OrderFingerprint) -> object:
        self.fingerprints.append(fingerprint)
        return self._store.try_claim(key, fingerprint)

    def try_claim_change(self, key: str, fingerprint: OrderFingerprint, *,
                         original_client_order_id: str,
                         expected_original: Binding | None = None) -> object:
        # 정정/취소 경로(submit_change)는 원자적 try_claim_change 를 쓴다 -- 지문 캡처는 동일.
        self.fingerprints.append(fingerprint)
        return self._store.try_claim_change(
            key, fingerprint,
            original_client_order_id=original_client_order_id,
            expected_original=expected_original)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


# --- order matrix: one representative order per codec branch ----------------------------------
#: ``(label, kwargs)`` describing every representative order. ``kwargs["lane"]`` selects the
#: construction path in :func:`_build`; the remaining keys are that lane's semantic parameters.
#: CHANGE lanes carry an ``original`` sub-dict (the prior order to reconstruct) plus the change
#: ``action``/``quantity``/``limit_price``.
ORDER_MATRIX: list[tuple[str, dict[str, Any]]] = [
    ("domestic_stock_krx_limit",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000, "exchange": "KRX"}),
    ("domestic_stock_nxt_limit",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000, "exchange": "NXT"}),
    ("domestic_stock_unified_market",
     {"lane": "domestic_stock_market", "symbol": "005930", "side": "sell",
      "quantity": 7, "exchange": "SOR"}),
    ("domestic_stock_credit_krx",
     {"lane": "domestic_credit", "symbol": "005930", "side": "buy", "quantity": 10,
      "limit_price": 70000, "credit_type": "21", "loan_date": "20261006", "exchange": "KRX"}),
    ("domestic_derivative_night",
     {"lane": "domestic_derivative", "symbol": "101V06", "side": "buy", "quantity": 1,
      "limit_price": "320.50", "derivative_item": "01", "session": "night"}),
    ("domestic_bond",
     {"lane": "domestic_bond", "symbol": "KR6095572D93", "side": "buy",
      "quantity": 10, "limit_price": 10200}),
    ("overseas_stock_nas_limit",
     {"lane": "overseas_stock", "symbol": "AAPL", "side": "buy",
      "quantity": 10, "limit_price": 150, "exchange": "NAS"}),
    ("overseas_stock_nys_overnight",
     {"lane": "overseas_overnight", "symbol": "IBM", "side": "buy",
      "quantity": 5, "limit_price": 140, "exchange": "NYS"}),
    ("overseas_derivative_osfo",
     {"lane": "overseas_fo", "symbol": "6AU24", "side": "buy",
      "quantity": 2, "limit_price": "0.655"}),
    ("us_algo_twap_window",
     {"lane": "overseas_stock", "symbol": "TSLA", "side": "buy", "quantity": 20,
      "limit_price": 250, "exchange": "NAS", "algo_strategy": "twap",
      "algo_start": "100000", "algo_end": "110000"}),
    ("reserved_domestic",
     {"lane": "reserved_domestic", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000}),
    ("reserved_overseas_us",
     {"lane": "reserved_us", "symbol": "AAPL", "side": "buy",
      "quantity": 3, "limit_price": 150, "exchange": "NAS"}),
    ("reserved_overseas_asia_hk",
     {"lane": "reserved_asia", "symbol": "00700", "side": "buy",
      "quantity": 100, "limit_price": 350, "exchange": "HKS", "currency": "CNY"}),
    # CHANGE variants for EVERY lane the double-send fix protects.
    ("domestic_stock_change",
     {"lane": "change_domestic", "action": "modify", "quantity": 5, "limit_price": 71000,
      "original": {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
                   "quantity": 10, "limit_price": 70000, "exchange": "KRX"}}),
    ("domestic_stock_nxt_change",
     {"lane": "change_domestic", "action": "modify", "quantity": 5, "limit_price": 71000,
      "original": {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
                   "quantity": 10, "limit_price": 70000, "exchange": "NXT"}}),
    ("domestic_stock_cancel",
     {"lane": "change_domestic", "action": "cancel", "quantity": 10, "limit_price": None,
      "original": {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
                   "quantity": 10, "limit_price": 70000, "exchange": "KRX"}}),
    ("domestic_stock_sor_cancel",
     {"lane": "change_domestic", "action": "cancel", "quantity": 10, "limit_price": None,
      "original": {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
                   "quantity": 10, "limit_price": 70000, "exchange": "SOR"}}),
    ("overseas_stock_change",
     {"lane": "change_overseas", "action": "modify", "quantity": 4, "limit_price": 151,
      "original": {"lane": "overseas_stock", "symbol": "AAPL", "side": "buy",
                   "quantity": 10, "limit_price": 150, "exchange": "NAS"}}),
    ("domestic_derivative_change",
     {"lane": "change_derivative", "action": "modify", "quantity": 1, "limit_price": "321.00",
      "original": {"lane": "domestic_derivative", "symbol": "101V06", "side": "buy",
                   "quantity": 1, "limit_price": "320.50", "derivative_item": "01",
                   "session": "regular"}}),
    ("domestic_bond_change",
     {"lane": "change_bond", "action": "modify", "quantity": 5, "limit_price": 10250,
      "original": {"lane": "domestic_bond", "symbol": "KR6095572D93", "side": "buy",
                   "quantity": 10, "limit_price": 10200}}),
    ("overseas_fo_change",
     {"lane": "change_overseas_fo", "action": "modify", "quantity": 2, "limit_price": "0.660",
      "original": {"lane": "overseas_fo", "symbol": "6AU24", "side": "buy",
                   "quantity": 2, "limit_price": "0.655"}}),
    ("overseas_overnight_change",
     {"lane": "change_overseas_overnight", "action": "modify", "quantity": 3,
      "limit_price": 141,
      "original": {"lane": "overseas_overnight", "symbol": "IBM", "side": "buy",
                   "quantity": 5, "limit_price": 140, "exchange": "NYS"}}),
    ("overseas_overnight_cancel",
     {"lane": "change_overseas_overnight", "action": "cancel", "quantity": 5,
      "limit_price": None,
      "original": {"lane": "overseas_overnight", "symbol": "IBM", "side": "buy",
                   "quantity": 5, "limit_price": 140, "exchange": "NYS"}}),
    ("domestic_derivative_night_change",
     {"lane": "change_derivative_night", "action": "modify", "quantity": 1,
      "limit_price": "321.00",
      "original": {"lane": "domestic_derivative", "symbol": "101V06", "side": "buy",
                   "quantity": 1, "limit_price": "320.50", "derivative_item": "01",
                   "session": "night"}}),
    ("domestic_derivative_night_cancel",
     {"lane": "change_derivative_night", "action": "cancel", "quantity": 1,
      "limit_price": None,
      "original": {"lane": "domestic_derivative", "symbol": "101V06", "side": "buy",
                   "quantity": 1, "limit_price": "320.50", "derivative_item": "01",
                   "session": "night"}}),
    ("domestic_stock_stop_limit",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy", "quantity": 10,
      "limit_price": 70000, "stop_price": 69000, "exchange": "KRX"}),
    ("domestic_stock_limit_ioc",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy", "quantity": 10,
      "limit_price": 70000, "time_in_force": "ioc", "exchange": "KRX"}),
    ("domestic_stock_best_quote",
     {"lane": "domestic_stock_market", "symbol": "005930", "side": "buy", "quantity": 10,
      "division": "immediate_limit", "exchange": "KRX"}),
    ("reserved_domestic_dated",
     {"lane": "reserved_domestic", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000, "end_date": "20261031"}),
    ("reserved_overseas_us_twap",
     {"lane": "reserved_us", "symbol": "AAPL", "side": "buy",
      "quantity": 3, "limit_price": 150, "exchange": "NAS", "algo_strategy": "twap"}),
]

_MATRIX: dict[str, dict[str, Any]] = dict(ORDER_MATRIX)


# --- construction (the only API-touching code) ------------------------------------------------
def _immediate_order(kwargs: dict[str, Any]) -> Order:
    """Reconstruct an immediate (non-change, non-reserved) :class:`Order` for one lane."""
    lane = kwargs["lane"]
    if lane == "domestic_stock":
        if "stop_price" in kwargs:
            return Order.stop_limit(
                kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                limit_price=kwargs["limit_price"], stop_price=kwargs["stop_price"],
                exchange=kwargs["exchange"], client_order_id=_ORIG_CID,
            )
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], exchange=kwargs["exchange"],
                           time_in_force=kwargs.get("time_in_force", "day"),
                           client_order_id=_ORIG_CID)
    if lane == "domestic_stock_market":
        return Order.market(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                            division=kwargs.get("division"), exchange=kwargs["exchange"],
                            client_order_id=_ORIG_CID)
    if lane == "domestic_credit":
        return Order.credit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                            credit_type=kwargs["credit_type"], limit_price=kwargs["limit_price"],
                            loan_date=kwargs["loan_date"], exchange=kwargs["exchange"],
                            client_order_id=_ORIG_CID)
    if lane == "domestic_derivative":
        return Order(symbol=kwargs["symbol"], side=kwargs["side"], order_type="limit",
                     quantity=Decimal(str(kwargs["quantity"])),
                     limit_price=Decimal(str(kwargs["limit_price"])),
                     asset_class="derivative", session=kwargs["session"],
                     derivative_item=kwargs["derivative_item"], client_order_id=_ORIG_CID)
    if lane == "domestic_bond":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], asset_class="bond",
                           client_order_id=_ORIG_CID)
    if lane == "overseas_stock":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], region="overseas",
                           asset_class="stock", exchange=kwargs["exchange"],
                           algo_strategy=kwargs.get("algo_strategy"),
                           algo_start=kwargs.get("algo_start", ""),
                           algo_end=kwargs.get("algo_end", ""), client_order_id=_ORIG_CID)
    if lane == "overseas_overnight":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], region="overseas",
                           asset_class="stock", exchange=kwargs["exchange"],
                           session="overnight", client_order_id=_ORIG_CID)
    if lane == "overseas_fo":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], region="overseas",
                           asset_class="derivative", client_order_id=_ORIG_CID)
    raise ValueError(f"unknown immediate lane: {lane!r}")


def _immediate_wire(order: Order, lane: str) -> WireRequest:
    """The wire request the safety core would assemble for an immediate lane's ``order``."""
    if lane in ("domestic_stock", "domestic_stock_market"):
        return _dom._make_order_cash_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "domestic_credit":
        return _dom.make_credit_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "domestic_derivative":
        return _dom_deriv.make_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "domestic_bond":
        return _bond.make_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "overseas_stock":
        return _ovs.make_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "overseas_overnight":
        return _ovs.make_overnight_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    if lane == "overseas_fo":
        return _ovs_fo.make_order_request(order, cano=_CANO, product_code=_PRODUCT, environment=_ENV)
    raise ValueError(f"unknown immediate lane: {lane!r}")


def _synthetic_prior(order: Order) -> ExecutionReport:
    """A prior NEW report for the original order, carrying the fixed exchange order-number /
    organization-number / receipt-date the change-wire builders read."""
    return ExecutionReport(
        client_order_id=_ORIG_CID, order_id=_ORIG_ODNO, symbol=order.symbol, side=order.side,
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=_RECORDED_AT, organization_number=_ORIG_ORG, receipt_date=_ORIG_RECEIPT_DATE,
    )


def _captured_result(
    transport: _CapturingTransport, store: _RecordingStore, captured: bool,
) -> tuple[OrderFingerprint, WireRequest]:
    """Assert the production boundary fired once and return its fingerprint and wire."""
    assert captured, "production entry point returned without reaching the transport boundary"
    assert len(store.fingerprints) == 1
    assert len(transport.calls) == 1
    method, path, tr_id, body = transport.calls[0]
    return store.fingerprints[0], WireRequest(method, path, tr_id, body)


def _capture_change(kwargs: dict[str, Any]) -> tuple[OrderFingerprint, WireRequest]:
    """Capture a change fingerprint and wire by driving production ``submit_change``."""
    original_order = _immediate_order(kwargs["original"])
    quantity = Decimal(str(kwargs["quantity"]))
    limit_price = None if kwargs["limit_price"] is None else Decimal(str(kwargs["limit_price"]))
    builders = {
        "change_domestic": _dom._make_domestic_change_request,
        "change_overseas": _ovs.make_change_request,
        "change_overseas_overnight": _ovs.make_overnight_change_request,
        "change_derivative": _dom_deriv.make_change_request,
        "change_derivative_night": _dom_deriv.make_night_change_request,
        "change_bond": _bond.make_change_request,
        "change_overseas_fo": _ovs_fo.make_change_request,
    }
    try:
        builder = builders[kwargs["lane"]]
    except KeyError:
        raise ValueError(f"unknown change lane: {kwargs['lane']!r}") from None

    # retention_days=0 로 시간 기반 정리를 끈다 -- 합성 원주문(_RECORDED_AT 고정일)이 벽시계 기준
    # 보존창을 넘겨 조용히 정리되면 change 경로가 '원주문 없음'으로 터져 와이어 캡처가 날짜에 의존하게
    # 된다(이 하네스는 와이어 바이트만 본다 -- 보존 정책과 무관해야 한다).
    with TemporaryDirectory() as tmp_dir, \
            OrderStore(Path(tmp_dir) / "orders.json", retention_days=0) as real_store:
        store = _RecordingStore(real_store)
        store.record(_synthetic_prior(original_order), original_order.fingerprint)
        transport = _CapturingTransport()
        captured = False
        try:
            _core.submit_change(
                transport, store,
                original_client_order_id=_ORIG_CID,
                request_id=_CAPTURE_CID,
                action=kwargs["action"],
                quantity=quantity,
                limit_price=limit_price,
                cano=_CANO,
                product_code=_PRODUCT,
                environment=_ENV,
                build_request=builder,
                expected_original=store.binding_for(_ORIG_CID),
            )
        except _Captured:
            captured = True
        return _captured_result(transport, store, captured)


def _capture_reserved(kwargs: dict[str, Any]) -> tuple[OrderFingerprint, WireRequest]:
    """Capture a reserved fingerprint and wire by driving its production facade."""
    with TemporaryDirectory() as tmp_dir, OrderStore(Path(tmp_dir) / "orders.json") as real_store:
        store = _RecordingStore(real_store)
        transport = _CapturingTransport()
        captured = False
        try:
            if kwargs["lane"] == "reserved_domestic":
                _dom_resv.place_reserved_order(
                    transport, store,
                    symbol=kwargs["symbol"],
                    side=kwargs["side"],
                    quantity=kwargs["quantity"],
                    limit_price=kwargs["limit_price"],
                    end_date=kwargs.get("end_date"),
                    client_order_id=_CAPTURE_CID,
                    cano=_CANO,
                    product_code=_PRODUCT,
                    environment=_ENV,
                )
            elif kwargs["lane"] in ("reserved_us", "reserved_asia"):
                _ovs_resv.place_overseas_reserved_order(
                    transport, store,
                    symbol=kwargs["symbol"],
                    side=kwargs["side"],
                    quantity=kwargs["quantity"],
                    limit_price=kwargs["limit_price"],
                    exchange=kwargs["exchange"],
                    currency=kwargs.get("currency"),
                    algo_strategy=kwargs.get("algo_strategy"),
                    client_order_id=_CAPTURE_CID,
                    cano=_CANO,
                    product_code=_PRODUCT,
                    environment=_ENV,
                )
            else:
                raise ValueError(f"unknown reserved lane: {kwargs['lane']!r}")
        except _Captured:
            captured = True
        return _captured_result(transport, store, captured)


def _build(label: str) -> tuple[OrderFingerprint, WireRequest]:
    """Build or capture ``(fingerprint, wire)`` for one matrix label from production code.

    This is the single capture locus for the wire/fingerprint; the public
    :func:`build_order` / :func:`build_wire` wrap it and keep their signatures fixed, so the
    captured ``GOLDEN_*`` literals can prove byte invariance across the rename.
    """
    kwargs = _MATRIX[label]
    lane = kwargs["lane"]
    if lane.startswith("change_"):
        return _capture_change(kwargs)
    if lane.startswith("reserved_"):
        return _capture_reserved(kwargs)
    order = _immediate_order(kwargs)
    return order.fingerprint, _immediate_wire(order, lane)


def _wire_to_dict(wire: WireRequest) -> dict[str, str]:
    """Flatten a :class:`WireRequest` to ``dict[str, str]``: the method/path/tr_id envelope plus
    the body (KIS body keys are uppercase, so the lowercase envelope keys never collide)."""
    return {"method": wire.method, "path": wire.path, "tr_id": wire.tr_id, **wire.body}


def build_order(label: str) -> OrderFingerprint:
    """The request fingerprint for one matrix label, built via the current order API."""
    return _build(label)[0]


def build_wire(label: str) -> dict[str, str]:
    """The outgoing wire request for one matrix label as a flat ``dict[str, str]``."""
    return _wire_to_dict(_build(label)[1])


# --- immutable golden literals (independently verified expected encodings) --------------------
GOLDEN_FINGERPRINT: dict[str, tuple[str, ...]] = {
    'domestic_stock_krx_limit': ('005930', 'buy', 'limit', '10', '70000', '', 'day', 'XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_nxt_limit': ('005930', 'buy', 'limit', '10', '70000', '', 'day', 'XKRX', '', '', 'regular', '', 'NXT', '', '', 'HKD', '', '', ''),
    'domestic_stock_unified_market': ('005930', 'sell', 'market', '7', '', '', 'day', 'XKRX', '', '', 'regular', '', 'UN', '', '', 'HKD', '', '', ''),
    'domestic_stock_credit_krx': ('005930', 'buy', 'limit', '10', '70000', '', 'day', 'XKRX', '21', '20261006', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_derivative_night': ('101V06', 'buy', 'limit', '1', '320.50', '', 'day', 'XKFE', '', '', 'night', '', 'KRX', '01', '', 'HKD', '', '', ''),
    'domestic_bond': ('KR6095572D93', 'buy', 'limit', '10', '10200', '', 'day', 'BOND', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_stock_nas_limit': ('AAPL', 'buy', 'limit', '10', '150', '', 'day', 'NAS', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_stock_nys_overnight': ('IBM', 'buy', 'limit', '5', '140', '', 'day', 'NYS', '', '', 'overnight', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_derivative_osfo': ('6AU24', 'buy', 'limit', '2', '0.655', '', 'day', 'OSFO', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'us_algo_twap_window': ('TSLA', 'buy', 'limit', '20', '250', '', 'day', 'NAS', '', '', 'regular', '', 'KRX', '', '', 'HKD', 'twap', '100000', '110000'),
    'reserved_domestic': ('005930', 'buy', 'limit', '10', '70000', '', 'day', 'reserved', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'reserved_overseas_us': ('AAPL', 'buy', 'limit', '3', '150', '', 'day', 'overseas-reserved', '', '', 'regular', '', 'KRX', '', 'NASD', 'HKD', '', '', ''),
    'reserved_overseas_asia_hk': ('00700', 'buy', 'limit', '100', '350', '', 'day', 'overseas-reserved-asia', '', '', 'regular', '', 'KRX', '', 'HKS', 'CNY', '', '', ''),
    'domestic_stock_change': ('20261006-000000000000cafe', 'buy', 'limit', '5', '71000', 'modify', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_nxt_change': ('20261006-000000000000cafe', 'buy', 'limit', '5', '71000', 'modify', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_cancel': ('20261006-000000000000cafe', 'buy', 'limit', '10', '', 'cancel', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_sor_cancel': ('20261006-000000000000cafe', 'buy', 'limit', '10', '', 'cancel', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_stock_change': ('20261006-000000000000cafe', 'buy', 'limit', '4', '151', 'modify', 'day', 'action:NAS', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_derivative_change': ('20261006-000000000000cafe', 'buy', 'limit', '1', '321.00', 'modify', 'day', 'action:XKFE', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_bond_change': ('20261006-000000000000cafe', 'buy', 'limit', '5', '10250', 'modify', 'day', 'action:BOND', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_fo_change': ('20261006-000000000000cafe', 'buy', 'limit', '2', '0.660', 'modify', 'day', 'action:OSFO', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_overnight_change': ('20261006-000000000000cafe', 'buy', 'limit', '3', '141', 'modify', 'day', 'action:NYS', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_overnight_cancel': ('20261006-000000000000cafe', 'buy', 'limit', '5', '', 'cancel', 'day', 'action:NYS', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_derivative_night_change': ('20261006-000000000000cafe', 'buy', 'limit', '1', '321.00', 'modify', 'day', 'action:XKFE', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_derivative_night_cancel': ('20261006-000000000000cafe', 'buy', 'limit', '1', '', 'cancel', 'day', 'action:XKFE', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_stop_limit': ('005930', 'buy', 'stop_limit', '10', '70000', '69000', 'day', 'XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_limit_ioc': ('005930', 'buy', 'limit', '10', '70000', '', 'ioc', 'XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_best_quote': ('005930', 'buy', 'market', '10', '', '', 'day', 'XKRX', '', '', 'regular', 'immediate_limit', 'KRX', '', '', 'HKD', '', '', ''),
    'reserved_domestic_dated': ('005930', 'buy', 'limit', '10', '70000', '20261031', 'day', 'reserved', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'reserved_overseas_us_twap': ('AAPL', 'buy', 'limit', '3', '150', '', 'day', 'overseas-reserved', '', '', 'regular', '', 'KRX', '', 'NASD', 'HKD', 'twap', '', ''),
}

GOLDEN_WIRE: dict[str, dict[str, str]] = {
    'domestic_stock_krx_limit': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0012U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '00', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_stock_nxt_limit': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0012U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '00', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'EXCG_ID_DVSN_CD': 'NXT'},
    'domestic_stock_unified_market': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0011U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '01', 'ORD_QTY': '7', 'ORD_UNPR': '0', 'CNDT_PRIC': '0', 'EXCG_ID_DVSN_CD': 'SOR'},
    'domestic_stock_credit_krx': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-credit', 'tr_id': 'TTTC0052U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'SLL_TYPE': '', 'CRDT_TYPE': '21', 'LOAN_DT': '20261006', 'ORD_DVSN': '00', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'RSVN_ORD_YN': 'N', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_derivative_night': {'method': 'POST', 'path': '/uapi/domestic-futureoption/v1/trading/order', 'tr_id': 'STTN1101U', 'ORD_PRCS_DVSN_CD': '02', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'SLL_BUY_DVSN_CD': '02', 'SHTN_PDNO': '101V06', 'ORD_QTY': '1', 'UNIT_PRICE': '320.50', 'NMPR_TYPE_CD': '01', 'KRX_NMPR_CNDT_CD': '0', 'CTAC_TLNO': '', 'FUOP_ITEM_DVSN_CD': '01', 'ORD_DVSN_CD': '01'},
    'domestic_bond': {'method': 'POST', 'path': '/uapi/domestic-bond/v1/trading/buy', 'tr_id': 'TTTC0952U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': 'KR6095572D93', 'ORD_QTY2': '10', 'BOND_ORD_UNPR': '10200', 'SAMT_MKET_PTCI_YN': 'N', 'BOND_RTL_MKET_YN': 'N', 'IDCR_STFNO': '', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0', 'CTAC_TLNO': ''},
    'overseas_stock_nas_limit': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order', 'tr_id': 'TTTT1002U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NASD', 'PDNO': 'AAPL', 'ORD_QTY': '10', 'OVRS_ORD_UNPR': '150', 'ORD_SVR_DVSN_CD': '0', 'ORD_DVSN': '00'},
    'overseas_stock_nys_overnight': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/daytime-order', 'tr_id': 'TTTS6036U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NYSE', 'PDNO': 'IBM', 'ORD_QTY': '5', 'OVRS_ORD_UNPR': '140', 'CTAC_TLNO': '', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0', 'ORD_DVSN': '00'},
    'overseas_derivative_osfo': {'method': 'POST', 'path': '/uapi/overseas-futureoption/v1/trading/order', 'tr_id': 'OTFM3001U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_FUTR_FX_PDNO': '6AU24', 'SLL_BUY_DVSN_CD': '02', 'FM_LQD_USTL_CCLD_DT': '', 'FM_LQD_USTL_CCNO': '', 'PRIC_DVSN_CD': '1', 'FM_LIMIT_ORD_PRIC': '0.655', 'FM_STOP_ORD_PRIC': '', 'FM_ORD_QTY': '2', 'FM_LQD_LMT_ORD_PRIC': '', 'FM_LQD_STOP_ORD_PRIC': '', 'CCLD_CNDT_CD': '6', 'CPLX_ORD_DVSN_CD': '0', 'ECIS_RSVN_ORD_YN': 'N', 'FM_HDGE_ORD_SCRN_YN': 'N'},
    'us_algo_twap_window': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order', 'tr_id': 'TTTT1002U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NASD', 'PDNO': 'TSLA', 'ORD_QTY': '20', 'OVRS_ORD_UNPR': '250', 'ORD_SVR_DVSN_CD': '0', 'ORD_DVSN': '35', 'ALGO_ORD_TMD_DVSN_CD': '00', 'START_TIME': '100000', 'END_TIME': '110000'},
    'reserved_domestic': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-resv', 'tr_id': 'CTSC0008U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'SLL_BUY_DVSN_CD': '02', 'ORD_DVSN_CD': '00', 'ORD_OBJT_CBLC_DVSN_CD': '10', 'LOAN_DT': '', 'RSVN_ORD_END_DT': ''},
    'reserved_overseas_us': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order-resv', 'tr_id': 'TTTT3014U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': 'AAPL', 'OVRS_EXCG_CD': 'NASD', 'FT_ORD_QTY': '3', 'FT_ORD_UNPR3': '150', 'ORD_SVR_DVSN_CD': '0', 'ORD_DVSN': '00'},
    'reserved_overseas_asia_hk': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order-resv', 'tr_id': 'TTTS3013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '00700', 'SLL_BUY_DVSN_CD': '02', 'RVSE_CNCL_DVSN_CD': '00', 'PRDT_TYPE_CD': '543', 'OVRS_EXCG_CD': 'SEHK', 'FT_ORD_QTY': '100', 'FT_ORD_UNPR3': '350', 'ORD_SVR_DVSN_CD': '0'},
    'domestic_stock_change': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'KRX_FWDG_ORD_ORGNO': '06010', 'ORGN_ODNO': '0000012345', 'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '01', 'ORD_QTY': '5', 'ORD_UNPR': '71000', 'CNDT_PRIC': '0', 'QTY_ALL_ORD_YN': 'N', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_stock_nxt_change': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'KRX_FWDG_ORD_ORGNO': '06010', 'ORGN_ODNO': '0000012345', 'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '01', 'ORD_QTY': '5', 'ORD_UNPR': '71000', 'CNDT_PRIC': '0', 'QTY_ALL_ORD_YN': 'N', 'EXCG_ID_DVSN_CD': 'NXT'},
    'domestic_stock_cancel': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'KRX_FWDG_ORD_ORGNO': '06010', 'ORGN_ODNO': '0000012345', 'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_stock_sor_cancel': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'KRX_FWDG_ORD_ORGNO': '06010', 'ORGN_ODNO': '0000012345', 'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'SOR'},
    'overseas_stock_change': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTT1004U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NASD', 'PDNO': 'AAPL', 'ORGN_ODNO': '0000012345', 'RVSE_CNCL_DVSN_CD': '01', 'ORD_QTY': '4', 'OVRS_ORD_UNPR': '151', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0'},
    'domestic_derivative_change': {'method': 'POST', 'path': '/uapi/domestic-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'TTTO1103U', 'ORD_PRCS_DVSN_CD': '02', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'RVSE_CNCL_DVSN_CD': '01', 'ORGN_ODNO': '0000012345', 'ORD_QTY': '1', 'UNIT_PRICE': '321.00', 'NMPR_TYPE_CD': '01', 'KRX_NMPR_CNDT_CD': '0', 'RMN_QTY_YN': 'N', 'FUOP_ITEM_DVSN_CD': '', 'ORD_DVSN_CD': '01'},
    'domestic_bond_change': {'method': 'POST', 'path': '/uapi/domestic-bond/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0953U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': 'KR6095572D93', 'ORGN_ODNO': '0000012345', 'ORD_QTY2': '5', 'BOND_ORD_UNPR': '10250', 'RVSE_CNCL_DVSN_CD': '01', 'QTY_ALL_ORD_YN': 'N', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0', 'CTAC_TLNO': ''},
    'overseas_fo_change': {'method': 'POST', 'path': '/uapi/overseas-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'OTFM3002U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'ORGN_ORD_DT': '20261006', 'ORGN_ODNO': '0000012345', 'FM_LIMIT_ORD_PRIC': '0.660', 'FM_STOP_ORD_PRIC': '', 'FM_LQD_LMT_ORD_PRIC': '', 'FM_LQD_STOP_ORD_PRIC': '', 'FM_HDGE_ORD_SCRN_YN': 'N'},
    'overseas_overnight_change': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl', 'tr_id': 'TTTS6038U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NYSE', 'PDNO': 'IBM', 'ORGN_ODNO': '0000012345', 'RVSE_CNCL_DVSN_CD': '01', 'ORD_QTY': '3', 'OVRS_ORD_UNPR': '141', 'CTAC_TLNO': '', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0'},
    'overseas_overnight_cancel': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl', 'tr_id': 'TTTS6038U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NYSE', 'PDNO': 'IBM', 'ORGN_ODNO': '0000012345', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '5', 'OVRS_ORD_UNPR': '0', 'CTAC_TLNO': '', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0'},
    'domestic_derivative_night_change': {'method': 'POST', 'path': '/uapi/domestic-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'TTTN1103U', 'ORD_PRCS_DVSN_CD': '02', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'RVSE_CNCL_DVSN_CD': '01', 'ORGN_ODNO': '0000012345', 'ORD_QTY': '1', 'UNIT_PRICE': '321.00', 'NMPR_TYPE_CD': '01', 'KRX_NMPR_CNDT_CD': '0', 'RMN_QTY_YN': 'Y', 'FUOP_ITEM_DVSN_CD': '01', 'ORD_DVSN_CD': '01'},
    'domestic_derivative_night_cancel': {'method': 'POST', 'path': '/uapi/domestic-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'TTTN1103U', 'ORD_PRCS_DVSN_CD': '02', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'RVSE_CNCL_DVSN_CD': '02', 'ORGN_ODNO': '0000012345', 'ORD_QTY': '1', 'UNIT_PRICE': '0', 'NMPR_TYPE_CD': '01', 'KRX_NMPR_CNDT_CD': '0', 'RMN_QTY_YN': 'Y', 'FUOP_ITEM_DVSN_CD': '01', 'ORD_DVSN_CD': '01'},
    'domestic_stock_stop_limit': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0012U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '22', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '69000', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_stock_limit_ioc': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0012U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '11', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'EXCG_ID_DVSN_CD': 'KRX'},
    'domestic_stock_best_quote': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-cash', 'tr_id': 'TTTC0012U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_DVSN': '03', 'ORD_QTY': '10', 'ORD_UNPR': '0', 'CNDT_PRIC': '0', 'EXCG_ID_DVSN_CD': 'KRX'},
    'reserved_domestic_dated': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-resv', 'tr_id': 'CTSC0008U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': '005930', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'SLL_BUY_DVSN_CD': '02', 'ORD_DVSN_CD': '00', 'ORD_OBJT_CBLC_DVSN_CD': '10', 'LOAN_DT': '', 'RSVN_ORD_END_DT': '20261031'},
    'reserved_overseas_us_twap': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order-resv', 'tr_id': 'TTTT3014U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': 'AAPL', 'OVRS_EXCG_CD': 'NASD', 'FT_ORD_QTY': '3', 'FT_ORD_UNPR3': '150', 'ORD_SVR_DVSN_CD': '0', 'ORD_DVSN': '35', 'ALGO_ORD_TMD_DVSN_CD': '02'},
}


# --- legacy-width decode fixtures -------------------------------------------------------------
#: Short on-disk rows from older schema widths (v1=8, v5-v7=13, v8=14, v9=16 slots). Decoding
#: pads each to the canonical 19 slots. The on-disk row bytes stay unchanged, but decoding and
#: re-encoding normalizes legacy slot-7 MIC values ``XKOS`` and ``NXTE`` to ``XKRX``.
LEGACY_ROWS: dict[str, list[str]] = {
    "v1_xkrx_8slot": ["005930", "buy", "limit", "10", "70000", "0", "day", "XKRX"],
    "v5_xkos_13slot": ["035720", "sell", "limit", "3", "55000", "0", "day", "XKOS",
                       "", "", "regular", "", "KRX"],
    "v8_xkfe_14slot": ["101V06", "buy", "limit", "1", "320.50", "0", "day", "XKFE",
                       "", "", "night", "", "KRX", "01"],
    "v9_xkos_16slot": ["005930", "buy", "limit", "10", "70000", "0", "day", "XKOS",
                       "", "", "regular", "", "UN", "", "", "HKD"],
    "v9_nxte_16slot": ["005930", "buy", "limit", "10", "70000", "0", "day", "NXTE",
                       "", "", "regular", "", "NXT", "", "", "HKD"],
}

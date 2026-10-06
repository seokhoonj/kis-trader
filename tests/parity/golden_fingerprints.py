"""Golden on-disk fingerprint rows and wire-param dicts captured from the current order code.

This module is the byte-level safety net for the exchange-axis redesign. It pins, as literal
constants, two things produced by reconstructing a representative order for every codec branch:

* ``GOLDEN_FINGERPRINT[label]`` -- the 19-slot positional on-disk encoding
  (:func:`~kis_trader.order.encode_fingerprint`) of that order's request fingerprint.
* ``GOLDEN_WIRE[label]`` -- the outgoing wire request as a flat ``dict[str, str]``: the HTTP
  ``method``/``path``/``tr_id`` envelope plus every KIS body field (all KIS body keys are
  uppercase, so the three lowercase envelope keys never collide with a body field).

``build_order(label)`` and ``build_wire(label)`` are the ONLY functions here that touch the
order-construction API (via the shared :func:`_build`). A later step swaps that construction to
the renamed API while these golden literals stay fixed -- equal output then proves the rename
changed only names, not the bytes that reach disk or the wire.

The fixed account/environment inputs below (``_CANO``/``_PRODUCT``/``_ENV``) make the captured
wire deterministic; none of them affects the fingerprint (the request fingerprint is independent
of ``client_order_id`` and account). Run ``python tests/parity/golden_fingerprints.py`` to
re-capture the literals from the current code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from kis_trader.domestic._engine import bond_orders as _bond
from kis_trader.domestic._engine import derivative_orders as _dom_deriv
from kis_trader.domestic._engine import orders as _dom
from kis_trader.domestic._engine import reserved_orders as _dom_resv
from kis_trader.order import (
    ChangeActionFingerprint,
    ImmediateOrderFingerprint,
    Order,
    OrderFingerprint,
    ReservedOrderFingerprint,
    WireRequest,
    encode_fingerprint,
    format_wire_decimal,
)
from kis_trader.overseas._engine import derivative_orders as _ovs_fo
from kis_trader.overseas._engine import orders as _ovs
from kis_trader.overseas._engine import reserved_orders as _ovs_resv
from kis_trader.report import ExecutionReport, OrderStatus

# --- fixed capture inputs (deterministic; none affect the fingerprint) ------------------------
_CANO = "12345678"
_PRODUCT = "01"
_ENV = "real"
#: Fixed original client-order-id for the CHANGE lanes -- it IS slot 0 of a change fingerprint,
#: so it must be stable across captures.
_ORIG_CID = "20261006-000000000000cafe"
#: Fixed original exchange order-number / organization-number for the synthetic prior report the
#: change-wire builders read (ORGN_ODNO / KRX_FWDG_ORD_ORGNO). Neither affects the fingerprint.
_ORIG_ODNO = "0000012345"
_ORIG_ORG = "06010"
_ORIG_RECEIPT_DATE = "20261006"
#: Any value -- recorded_at never reaches the wire or the fingerprint.
_RECORDED_AT = datetime(2026, 10, 6, tzinfo=UTC)


# --- order matrix: one representative order per codec branch ----------------------------------
#: ``(label, kwargs)`` describing every representative order. ``kwargs["lane"]`` selects the
#: construction path in :func:`_build`; the remaining keys are that lane's semantic parameters.
#: CHANGE lanes carry an ``original`` sub-dict (the prior order to reconstruct) plus the change
#: ``action``/``quantity``/``limit_price``.
ORDER_MATRIX: list[tuple[str, dict[str, Any]]] = [
    ("domestic_stock_krx_limit",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000, "board": "KRX"}),
    ("domestic_stock_nxt_limit",
     {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
      "quantity": 10, "limit_price": 70000, "board": "NXT"}),
    ("domestic_stock_unified_market",
     {"lane": "domestic_stock_market", "symbol": "005930", "side": "sell",
      "quantity": 7, "board": "UN"}),
    ("domestic_stock_credit_krx",
     {"lane": "domestic_credit", "symbol": "005930", "side": "buy", "quantity": 10,
      "limit_price": 70000, "credit_type": "21", "loan_date": "20261006", "board": "KRX"}),
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
      "quantity": 2, "limit_price": "0.655", "exchange": "OSFO"}),
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
                   "quantity": 10, "limit_price": 70000, "board": "KRX"}}),
    ("domestic_stock_cancel",
     {"lane": "change_domestic", "action": "cancel", "quantity": 10, "limit_price": None,
      "original": {"lane": "domestic_stock", "symbol": "005930", "side": "buy",
                   "quantity": 10, "limit_price": 70000, "board": "KRX"}}),
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
                   "quantity": 2, "limit_price": "0.655", "exchange": "OSFO"}}),
]

_MATRIX: dict[str, dict[str, Any]] = dict(ORDER_MATRIX)


# --- construction (the only API-touching code; the redesign edits this) -----------------------
def _immediate_order(kwargs: dict[str, Any]) -> Order:
    """Reconstruct an immediate (non-change, non-reserved) :class:`Order` for one lane."""
    lane = kwargs["lane"]
    if lane == "domestic_stock":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], board=kwargs["board"],
                           client_order_id=_ORIG_CID)
    if lane == "domestic_stock_market":
        return Order.market(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                            board=kwargs["board"], client_order_id=_ORIG_CID)
    if lane == "domestic_credit":
        return Order.credit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                            credit_type=kwargs["credit_type"], limit_price=kwargs["limit_price"],
                            loan_date=kwargs["loan_date"], board=kwargs["board"],
                            client_order_id=_ORIG_CID)
    if lane == "domestic_derivative":
        return Order(symbol=kwargs["symbol"], side=kwargs["side"], order_type="limit",
                     quantity=Decimal(str(kwargs["quantity"])),
                     limit_price=Decimal(str(kwargs["limit_price"])),
                     exchange=_dom_deriv._DERIVATIVE_EXCHANGE, session=kwargs["session"],
                     derivative_item=kwargs["derivative_item"], client_order_id=_ORIG_CID)
    if lane == "domestic_bond":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], exchange=_bond._BOND_EXCHANGE,
                           client_order_id=_ORIG_CID)
    if lane == "overseas_stock":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], exchange=kwargs["exchange"],
                           algo_strategy=kwargs.get("algo_strategy"),
                           algo_start=kwargs.get("algo_start", ""),
                           algo_end=kwargs.get("algo_end", ""), client_order_id=_ORIG_CID)
    if lane == "overseas_overnight":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"], exchange=kwargs["exchange"],
                           session="overnight", client_order_id=_ORIG_CID)
    if lane == "overseas_fo":
        return Order.limit(kwargs["symbol"], side=kwargs["side"], quantity=kwargs["quantity"],
                           limit_price=kwargs["limit_price"],
                           exchange=_ovs_fo._OVERSEAS_FO_EXCHANGE, client_order_id=_ORIG_CID)
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


def _change_fingerprint(
    original: ImmediateOrderFingerprint, *, action: str, quantity: Decimal, limit_price: Decimal | None
) -> ChangeActionFingerprint:
    """Build the change-action fingerprint exactly as the safety core does from the original
    fingerprint plus the change intent (action / new quantity / new price)."""
    return ChangeActionFingerprint(
        original_client_order_id=_ORIG_CID, side=original.side, order_type=original.order_type,
        quantity=format_wire_decimal(quantity),
        limit_price="" if limit_price is None else format_wire_decimal(limit_price),
        action=action, time_in_force=original.time_in_force, exchange=original.exchange,
    )


def _change_wire(lane: str, *, report: ExecutionReport, original: ImmediateOrderFingerprint,
                 action: str, quantity: Decimal, limit_price: Decimal | None) -> WireRequest:
    kw = {"original_report": report, "original_fingerprint": original, "action": action,
          "quantity": quantity, "limit_price": limit_price,
          "cano": _CANO, "product_code": _PRODUCT, "environment": _ENV}
    if lane == "change_domestic":
        return _dom._make_domestic_change_request(**kw)
    if lane == "change_overseas":
        return _ovs.make_change_request(**kw)
    if lane == "change_derivative":
        return _dom_deriv.make_change_request(**kw)
    if lane == "change_bond":
        return _bond.make_change_request(**kw)
    if lane == "change_overseas_fo":
        return _ovs_fo.make_change_request(**kw)
    raise ValueError(f"unknown change lane: {lane!r}")


def _reserved(kwargs: dict[str, Any]) -> tuple[ReservedOrderFingerprint, WireRequest]:
    """Reconstruct a reserved-order fingerprint and its place wire for one reserved lane."""
    lane = kwargs["lane"]
    if lane == "reserved_domestic":
        terms = _dom_resv._coerce_reserved_order_terms(
            side=kwargs["side"], quantity=kwargs["quantity"],
            limit_price=kwargs["limit_price"], end_date=None)
        fingerprint = ReservedOrderFingerprint(
            symbol=kwargs["symbol"], side=kwargs["side"], order_type=terms.order_type,
            quantity=format_wire_decimal(terms.quantity),
            limit_price="" if terms.limit_price is None else format_wire_decimal(terms.limit_price),
            end_date="", exchange=_dom_resv._RESERVED_EXCHANGE)
        body = _dom_resv._make_reserved_order_fields(
            cano=_CANO, product_code=_PRODUCT, symbol=kwargs["symbol"], side=kwargs["side"],
            terms=terms, end_date=None)
        wire = WireRequest("POST", _dom_resv._PLACE_PATH, _dom_resv._PLACE_TR, body)
        return fingerprint, wire
    if lane == "reserved_us":
        qty = Decimal(str(kwargs["quantity"]))
        limit = Decimal(str(kwargs["limit_price"]))
        order_exchange = _ovs._ORDER_EXCHANGE[kwargs["exchange"]][0]
        fingerprint = ReservedOrderFingerprint(
            symbol=kwargs["symbol"], side=kwargs["side"], order_type="limit",
            quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
            end_date="", exchange=_ovs_resv._RESERVED_EXCHANGE, algo_strategy="")
        body = {
            "CANO": _CANO, "ACNT_PRDT_CD": _PRODUCT, "PDNO": kwargs["symbol"],
            "OVRS_EXCG_CD": order_exchange,
            "FT_ORD_QTY": format_wire_decimal(qty), "FT_ORD_UNPR3": format_wire_decimal(limit),
            "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": _ovs_resv._ORD_DVSN_LIMIT,
        }
        tr_id = _ovs_resv._PLACE_TR["US"][kwargs["side"]][_ENV]
        return fingerprint, WireRequest("POST", _ovs_resv._PLACE_PATH, tr_id, body)
    if lane == "reserved_asia":
        qty = Decimal(str(kwargs["quantity"]))
        limit = Decimal(str(kwargs["limit_price"]))
        order_exchange = _ovs._ORDER_EXCHANGE[kwargs["exchange"]][0]
        currency = kwargs.get("currency")
        prdt_type_cd = _ovs_resv._asia_prdt_type_cd(kwargs["exchange"], currency)
        fingerprint = ReservedOrderFingerprint(
            symbol=kwargs["symbol"], side=kwargs["side"], order_type="limit",
            quantity=format_wire_decimal(qty), limit_price=format_wire_decimal(limit),
            end_date="", exchange=_ovs_resv._ASIA_RESERVED_EXCHANGE,
            overseas_exchange=kwargs["exchange"], currency=currency or "HKD")
        body = {
            "CANO": _CANO, "ACNT_PRDT_CD": _PRODUCT, "PDNO": kwargs["symbol"],
            "SLL_BUY_DVSN_CD": _ovs_resv._SIDE_CODE[kwargs["side"]], "RVSE_CNCL_DVSN_CD": "00",
            "PRDT_TYPE_CD": prdt_type_cd, "OVRS_EXCG_CD": order_exchange,
            "FT_ORD_QTY": format_wire_decimal(qty), "FT_ORD_UNPR3": format_wire_decimal(limit),
            "ORD_SVR_DVSN_CD": "0",
        }
        tr_id = _ovs_resv._PLACE_TR["ASIA"]["any"][_ENV]
        return fingerprint, WireRequest("POST", _ovs_resv._PLACE_PATH, tr_id, body)
    raise ValueError(f"unknown reserved lane: {lane!r}")


def _build(label: str) -> tuple[OrderFingerprint, WireRequest]:
    """Reconstruct ``(fingerprint, wire)`` for one matrix label from the current order API.

    This is the single construction locus the exchange-axis redesign edits; the public
    :func:`build_order` / :func:`build_wire` wrap it and keep their signatures fixed, so the
    captured ``GOLDEN_*`` literals can prove byte invariance across the rename.
    """
    kwargs = _MATRIX[label]
    lane = kwargs["lane"]
    if lane.startswith("change_"):
        original_order = _immediate_order(kwargs["original"])
        original_fp = original_order.fingerprint
        report = _synthetic_prior(original_order)
        action = kwargs["action"]
        quantity = Decimal(str(kwargs["quantity"]))
        limit_price = None if kwargs["limit_price"] is None else Decimal(str(kwargs["limit_price"]))
        fingerprint: OrderFingerprint = _change_fingerprint(
            original_fp, action=action, quantity=quantity, limit_price=limit_price)
        wire = _change_wire(lane, report=report, original=original_fp, action=action,
                            quantity=quantity, limit_price=limit_price)
        return fingerprint, wire
    if lane.startswith("reserved_"):
        return _reserved(kwargs)
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


# --- captured golden literals (from `python tests/parity/golden_fingerprints.py`) -------------
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
    'reserved_overseas_us': ('AAPL', 'buy', 'limit', '3', '150', '', 'day', 'overseas-reserved', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'reserved_overseas_asia_hk': ('00700', 'buy', 'limit', '100', '350', '', 'day', 'overseas-reserved-asia', '', '', 'regular', '', 'KRX', '', 'HKS', 'CNY', '', '', ''),
    'domestic_stock_change': ('20261006-000000000000cafe', 'buy', 'limit', '5', '71000', 'modify', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_stock_cancel': ('20261006-000000000000cafe', 'buy', 'limit', '10', '', 'cancel', 'day', 'action:XKRX', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_stock_change': ('20261006-000000000000cafe', 'buy', 'limit', '4', '151', 'modify', 'day', 'action:NAS', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_derivative_change': ('20261006-000000000000cafe', 'buy', 'limit', '1', '321.00', 'modify', 'day', 'action:XKFE', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'domestic_bond_change': ('20261006-000000000000cafe', 'buy', 'limit', '5', '10250', 'modify', 'day', 'action:BOND', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
    'overseas_fo_change': ('20261006-000000000000cafe', 'buy', 'limit', '2', '0.660', 'modify', 'day', 'action:OSFO', '', '', 'regular', '', 'KRX', '', '', 'HKD', '', '', ''),
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
    'domestic_stock_cancel': {'method': 'POST', 'path': '/uapi/domestic-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0013U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'KRX_FWDG_ORD_ORGNO': '06010', 'ORGN_ODNO': '0000012345', 'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '10', 'ORD_UNPR': '70000', 'CNDT_PRIC': '0', 'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'KRX'},
    'overseas_stock_change': {'method': 'POST', 'path': '/uapi/overseas-stock/v1/trading/order-rvsecncl', 'tr_id': 'TTTT1004U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'OVRS_EXCG_CD': 'NASD', 'PDNO': 'AAPL', 'ORGN_ODNO': '0000012345', 'RVSE_CNCL_DVSN_CD': '01', 'ORD_QTY': '4', 'OVRS_ORD_UNPR': '151', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0'},
    'domestic_derivative_change': {'method': 'POST', 'path': '/uapi/domestic-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'TTTO1103U', 'ORD_PRCS_DVSN_CD': '02', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'RVSE_CNCL_DVSN_CD': '01', 'ORGN_ODNO': '0000012345', 'ORD_QTY': '1', 'UNIT_PRICE': '321.00', 'NMPR_TYPE_CD': '01', 'KRX_NMPR_CNDT_CD': '0', 'RMN_QTY_YN': 'N', 'FUOP_ITEM_DVSN_CD': '', 'ORD_DVSN_CD': '01'},
    'domestic_bond_change': {'method': 'POST', 'path': '/uapi/domestic-bond/v1/trading/order-rvsecncl', 'tr_id': 'TTTC0953U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'PDNO': 'KR6095572D93', 'ORGN_ODNO': '0000012345', 'ORD_QTY2': '5', 'BOND_ORD_UNPR': '10250', 'RVSE_CNCL_DVSN_CD': '01', 'QTY_ALL_ORD_YN': 'N', 'MGCO_APTM_ODNO': '', 'ORD_SVR_DVSN_CD': '0', 'CTAC_TLNO': ''},
    'overseas_fo_change': {'method': 'POST', 'path': '/uapi/overseas-futureoption/v1/trading/order-rvsecncl', 'tr_id': 'OTFM3002U', 'CANO': '12345678', 'ACNT_PRDT_CD': '01', 'ORGN_ORD_DT': '20261006', 'ORGN_ODNO': '0000012345', 'FM_LIMIT_ORD_PRIC': '0.660', 'FM_STOP_ORD_PRIC': '', 'FM_LQD_LMT_ORD_PRIC': '', 'FM_LQD_STOP_ORD_PRIC': '', 'FM_HDGE_ORD_SCRN_YN': 'N'},
}


# --- legacy-width decode fixtures (Step 5) ----------------------------------------------------
#: Short on-disk rows from older schema widths (v1=8, v5-v7=13, v8=14, v9=16 slots). Decoding
#: pads each to the canonical 19 slots; the slot-7 exchange namespace is preserved verbatim
#: (byte invariance), including the legacy MIC values ``XKOS`` and ``NXTE``.
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


def _capture() -> None:
    """Print Python literals for ``GOLDEN_FINGERPRINT`` / ``GOLDEN_WIRE`` captured from the
    current code. Paste the output above as the module constants."""
    import pprint

    fingerprints = {label: tuple(encode_fingerprint(build_order(label))) for label, _ in ORDER_MATRIX}
    wires = {label: build_wire(label) for label, _ in ORDER_MATRIX}
    print("GOLDEN_FINGERPRINT = " + pprint.pformat(fingerprints, width=100, sort_dicts=False))
    print()
    print("GOLDEN_WIRE = " + pprint.pformat(wires, width=100, sort_dicts=False))


if __name__ == "__main__":
    _capture()

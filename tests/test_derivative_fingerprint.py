from decimal import Decimal

import pytest

from kis_trader.errors import KISUsageError
from kis_trader.order import (
    _DERIVATIVE_EXCHANGE,
    _FINGERPRINT_SLOTS,
    ImmediateOrderFingerprint,
    Order,
    decode_fingerprint,
    encode_fingerprint,
)


def _fo_order(**kw):
    base = {"symbol": "101S03", "side": "buy", "order_type": "limit", "quantity": Decimal(1),
            "limit_price": Decimal("400.00"), "exchange": _DERIVATIVE_EXCHANGE,
            "session": "night", "derivative_item": "01", "client_order_id": "cid-1"}
    base.update(kw)
    return Order(**base)


def test_slot_count_is_15():
    assert _FINGERPRINT_SLOTS == 15


def test_fo_fingerprint_roundtrip_encodes_session_and_item():
    fp = _fo_order().fingerprint
    row = encode_fingerprint(fp)
    assert len(row) == 15
    back = decode_fingerprint(row)
    assert back == fp
    assert back.session == "night"
    assert back.derivative_item == "01"


def test_old_13_slot_record_decodes_with_empty_item():
    # 구 주식 지문(13슬롯) -> derivative_item "" 로 복원, 나머지 동등
    stock_fp = Order(symbol="005930", side="buy", order_type="limit", quantity=Decimal(1),
                     limit_price=Decimal(70000), client_order_id="c").fingerprint
    row13 = encode_fingerprint(stock_fp)[:13]
    assert len(row13) == 13
    back = decode_fingerprint(row13)
    assert isinstance(back, ImmediateOrderFingerprint)
    assert back.derivative_item == ""
    assert back == stock_fp


def test_day_vs_night_and_call_vs_put_fingerprints_differ():
    assert _fo_order(session="regular").fingerprint != _fo_order(session="night").fingerprint
    assert _fo_order(derivative_item="02").fingerprint != _fo_order(derivative_item="03").fingerprint


def test_xkfe_rejects_priority_limit():
    with pytest.raises(KISUsageError):
        _fo_order(order_type="market", limit_price=None, division="priority_limit")


def test_xkfe_allows_immediate_and_conditional_division():
    _fo_order(order_type="market", limit_price=None, division="immediate_limit")
    _fo_order(order_type="limit", division="conditional_limit")  # conditional_limit 은 지정가


def test_xkfe_rejects_overnight_session():
    with pytest.raises(KISUsageError):
        _fo_order(session="overnight")

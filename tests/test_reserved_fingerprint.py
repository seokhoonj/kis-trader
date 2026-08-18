"""예약 지문의 해석된 해외 거래소(`overseas_exchange`) -- dedup 정체성 + 온-디스크 왕복.

아시아 예약 취소는 발주 TR 에 원주문 전체(거래소/상품유형 포함)를 다시 실어야 하므로, 발주
시점에 해석한 거래소코드(예: "HKS")를 지문에 함께 영속한다. 같은 심볼·수량·가격이라도 해석된
거래소가 다르면 다른 주문이라 지문 정체성에 포함되며, 구(14-슬롯 이하) 레코드는 기본값 ``""``
으로 로드된다.
"""

from kis_trader.order import (
    ReservedOrderFingerprint,
    decode_fingerprint,
    encode_fingerprint,
)

_BASE = {"symbol": "00700", "side": "buy", "order_type": "limit", "quantity": "100",
         "limit_price": "350.00", "end_date": "", "exchange": "overseas-reserved-asia"}


def test_overseas_exchange_defaults_empty_and_is_in_identity():
    a = ReservedOrderFingerprint(**_BASE, overseas_exchange="HKS")
    b = ReservedOrderFingerprint(**_BASE, overseas_exchange="SHS")
    c = ReservedOrderFingerprint(**_BASE)   # 기본 ""
    assert a != b                           # 해석된 거래소가 다르면 다른 주문
    assert a != c
    assert c.overseas_exchange == ""


def test_overseas_exchange_round_trips_through_codec():
    fingerprint = ReservedOrderFingerprint(**_BASE, overseas_exchange="HKS")
    decoded = decode_fingerprint(encode_fingerprint(fingerprint))
    assert isinstance(decoded, ReservedOrderFingerprint)
    assert decoded == fingerprint
    assert decoded.overseas_exchange == "HKS"


def test_legacy_14_slot_reserved_record_defaults_overseas_exchange():
    """v8 이하(14-슬롯 이하) 예약 레코드는 overseas_exchange 가 "" 로 채워져 그대로 읽힌다."""
    row = ["AAPL", "buy", "limit", "1", "150", "", "day", "overseas-reserved",
           "", "", "regular", "", "KRX", ""]
    fingerprint = decode_fingerprint(row)
    assert isinstance(fingerprint, ReservedOrderFingerprint)
    assert fingerprint.overseas_exchange == ""


def test_asia_namespace_decodes_as_reserved_variant():
    """"overseas-reserved-asia" 도 예약 네임스페이스다 -- 즉시 지문으로 오판되면 reconcile/취소
    라우팅이 깨진다."""
    fingerprint = decode_fingerprint(encode_fingerprint(ReservedOrderFingerprint(**_BASE)))
    assert isinstance(fingerprint, ReservedOrderFingerprint)
    assert fingerprint.exchange == "overseas-reserved-asia"

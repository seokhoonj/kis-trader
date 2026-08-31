"""스키마 v9(16-슬롯 지문) -> v10(19-슬롯, 미국주식 algo) 하위호환 dedup 회귀.

핵심 불변식: v10 에서 지문 위치 인코딩이 16-슬롯에서 19-슬롯(algo 전략/시작/종료)으로 늘었지만,
**v9 저장소에 있던 비-algo 주문의 dedup 정체성은 그대로**여야 한다. 즉 재기동 후 같은 비-algo
주문의 재전송은 replay(InFlight)로 걸러지고(가짜 Conflict 로 이중발주를 막지 않아야 함), 반대로
같은 id 로 온 **algo** 주문은 서로 다른 주문이라 Conflict 여야 한다. 16-슬롯 레코드가 뒤쪽 algo
슬롯을 ""로 채워 새 19-슬롯 비-algo 인코딩과 바이트 동일해지는 것을 저장소 왕복으로 고정한다.
"""

import json
from datetime import datetime, timedelta, timezone

from kis_trader.order import Order, ReservedOrderFingerprint, decode_fingerprint
from kis_trader.store import Conflict, InFlight, OrderStore

_KST = timezone(timedelta(hours=9))


def _clock():
    return datetime(2026, 8, 31, 10, 0, tzinfo=_KST)


def _v9_store(**fingerprints):
    """schema_version=9 저장소 스냅샷(16-슬롯 지문, in_flight dict). fingerprints = {cid: 16-슬롯 행}."""
    return {
        "schema_version": 9,
        "in_flight": {cid: _clock().isoformat() for cid in fingerprints},
        "fingerprints": dict(fingerprints),
        "reports": {},
    }


#: v9 당시 비-algo 미국 즉시주문 지문의 16-슬롯 온-디스크 행(AAPL buy limit 150 @ NAS).
_V9_IMMEDIATE = ["AAPL", "buy", "limit", "1", "150", "", "day", "NAS",
                 "", "", "regular", "", "KRX", "", "", "HKD"]
#: v9 당시 비-algo 미국 예약주문 지문의 16-슬롯 행.
_V9_RESERVED = ["AAPL", "buy", "limit", "1", "150", "", "day", "overseas-reserved",
                "", "", "regular", "", "KRX", "", "", "HKD"]


def test_v9_16_slot_immediate_nonalgo_replays_and_algo_conflicts(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_v9_store(**{"cid-1": _V9_IMMEDIATE})))
    with OrderStore(p, now=_clock) as store:
        # 같은 비-algo 주문 재전송 -> 지문 동일 -> InFlight(replay), 가짜 Conflict 아님.
        same = Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                           client_order_id="cid-1").fingerprint
        assert isinstance(store.try_claim("cid-1", same), InFlight)
        # 같은 id 로 온 algo 주문 -> 서로 다른 주문(뒤쪽 algo 슬롯이 다름) -> Conflict.
        algo = Order.limit("AAPL", side="buy", quantity=1, limit_price=150, exchange="NAS",
                           algo_strategy="twap", client_order_id="cid-1").fingerprint
        assert isinstance(store.try_claim("cid-1", algo), Conflict)


def test_v9_16_slot_reserved_nonalgo_replays(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_v9_store(**{"cid-2": _V9_RESERVED})))
    # 16-슬롯 예약 행이 새 19-슬롯 예약 인코딩(algo_strategy="")과 동일 정체성이어야 한다.
    fresh_reserved = ReservedOrderFingerprint(
        symbol="AAPL", side="buy", order_type="limit", quantity="1", limit_price="150",
        end_date="", exchange="overseas-reserved")
    assert decode_fingerprint(_V9_RESERVED) == fresh_reserved
    with OrderStore(p, now=_clock) as store:
        assert isinstance(store.try_claim("cid-2", fresh_reserved), InFlight)

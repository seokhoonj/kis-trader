import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kis_trader.order import ChangeActionFingerprint, Order
from kis_trader.store import _SCHEMA_VERSION, Claimed, OrderStore

_KST = timezone(timedelta(hours=9))


def _clock():
    return datetime(2026, 8, 17, 19, 30, tzinfo=_KST)


def _fp():
    return Order(symbol="101S03", side="buy", order_type="limit", quantity=Decimal(1),
                 limit_price=Decimal("400.00"), exchange="XKFE", session="night",
                 derivative_item="01", client_order_id="cid-1").fingerprint


def test_claim_records_claim_time(tmp_path):
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        assert isinstance(store.try_claim("cid-1", _fp()), Claimed)
        assert store.claim_time_for("cid-1") == _clock().isoformat()


def test_v8_persist_and_reload_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    with OrderStore(p, now=_clock) as store:
        store.try_claim("cid-1", _fp())
    data = json.loads(p.read_text())
    assert data["schema_version"] == _SCHEMA_VERSION   # v8 에서 도입된 in_flight dict 형식이 대상
    assert data["in_flight"] == {"cid-1": _clock().isoformat()}
    with OrderStore(p, now=_clock) as reloaded:
        assert reloaded.is_in_flight("cid-1")
        assert reloaded.claim_time_for("cid-1") == _clock().isoformat()


def test_v7_file_migrates_inflight_list_to_dict(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema_version": 7, "in_flight": ["old-cid"],
                             "fingerprints": {}, "reports": {}}))
    with OrderStore(p, now=_clock) as store:
        assert store.is_in_flight("old-cid")
        assert store.claim_time_for("old-cid") == ""   # 구 레코드 = 시각 미상 -> 폴백 앵커


def test_in_flight_change_for_finds_pending_change(tmp_path):
    with OrderStore(tmp_path / "s.json", now=_clock) as store:
        change_fp = ChangeActionFingerprint(
            original_client_order_id="orig-1", side="buy", order_type="limit",
            quantity="1", limit_price="400.00", action="cancel", time_in_force="day", exchange="XKFE")
        store.try_claim("req-1", change_fp)
        assert store.in_flight_change_for("orig-1") == "req-1"
        assert store.in_flight_change_for("orig-2") is None

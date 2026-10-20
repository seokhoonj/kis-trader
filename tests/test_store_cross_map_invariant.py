"""로드 시 크로스-맵 불변식 검증 회귀 -- 세 맵(in_flight/fingerprints/reports)을 독립 디코드하면
reports 키 != 내장 client_order_id 인 손상 파일이 try_claim 에서 다른 주문의 Completed 리포트를
재생(오확정)할 수 있다. 손상 파일은 레코드-손상과 같은 fail-closed(KISError)로 거부해야 한다."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import KISError
from kis_trader.order import ChangeActionFingerprint, Order
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import OrderStore

_KST = timezone(timedelta(hours=9))


def _clock() -> datetime:
    return datetime(2026, 8, 31, 10, 0, tzinfo=_KST)


def _valid_v10_file(tmp_path, cid: str = "cid-done"):
    """API 로 쓴 유효 v10 저장소 -- 완료 리포트 1건(+지문), in_flight 비어 있음."""
    path = tmp_path / "s.json"
    order = Order.limit("005930", side="buy", quantity=10, limit_price=70000,
                        region="domestic", asset_class="stock", exchange="KRX",
                        client_order_id=cid)
    report = ExecutionReport(
        client_order_id=cid, order_id="0001", symbol="005930", side="buy",
        status=OrderStatus.FILLED, filled_quantity=Decimal(10), average_price=Decimal(70000),
        recorded_at=_clock(),
    )
    with OrderStore(path, now=_clock) as store:
        store.record(report, order.fingerprint)
    return path


def _reload_mutated(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 12
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return OrderStore(path, now=_clock)


def test_valid_v10_file_loads():
    # 자기검증: 변조 전 유효 파일은 통과해야 한다(불변식이 정상 스토어를 막지 않음).
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        path = _valid_v10_file(Path(d))
        with OrderStore(path, now=_clock):
            pass


def _rekey_report_mismatch(data):               # (a1) reports 키 != report.client_order_id
    [(_cid, v)] = list(data["reports"].items())
    data["reports"] = {"OTHER-ID": v}            # 키만 바꿔 내장 client_order_id 와 어긋나게
    data["fingerprints"] = {"OTHER-ID": next(iter(data["fingerprints"].values()))}


def _report_also_in_flight(data):               # (a2) reports 와 in_flight 가 겹침
    cid = next(iter(data["reports"]))
    data["in_flight"] = {cid: _clock().isoformat()}
    data["fingerprints"][cid] = next(iter(data["fingerprints"].values()))


def _fingerprint_extra(data):                   # 유령 지문(대응 claim 없음) -> 거부
    data["fingerprints"]["GHOST"] = next(iter(data["fingerprints"].values()))


def _report_cid_null(data):                     # client_order_id null -> str(None) 흡수 금지
    cid = next(iter(data["reports"]))
    data["reports"][cid]["client_order_id"] = None


def _report_symbol_null(data):                  # symbol null
    cid = next(iter(data["reports"]))
    data["reports"][cid]["symbol"] = None


def _missing_in_flight(data):                   # 필수 맵 in_flight 누락 -> 빈 dedup 로 조용히 시작 금지
    del data["in_flight"]


def _missing_fingerprints(data):                # dedup 장벽(지문 맵) 누락 -> 중복 체결 위험
    del data["fingerprints"]


def _missing_reports(data):                     # 리포트 맵 누락
    del data["reports"]


@pytest.mark.parametrize("mutate", [
    _rekey_report_mismatch,
    _report_also_in_flight,
    _fingerprint_extra,
    _report_cid_null,
    _report_symbol_null,
    _missing_in_flight,
    _missing_fingerprints,
    _missing_reports,
])
def test_corrupt_v10_cross_map_rejected(tmp_path, mutate):
    path = _valid_v10_file(tmp_path)
    with pytest.raises(KISError):
        _reload_mutated(path, mutate)


def test_fingerprintless_claim_is_legal_and_conflicts_on_resubmit(tmp_path):
    # 지문 없는 claim 은 적법하다(레거시 마이그레이션) -- 불변식은 '유령 지문 금지'(부분집합)일 뿐
    # '모든 claim 에 지문'을 강요하지 않는다. 재제출 시 저장 지문 None != 제출 지문 -> Conflict 로 fail-safe.
    from kis_trader.store import Conflict
    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "schema_version": 10,
        "in_flight": {"cid-legacy": _clock().isoformat()},
        "fingerprints": {},                      # claim 은 있는데 지문 없음 -> 적법
        "reports": {},
    }), encoding="utf-8")
    with OrderStore(path, now=_clock) as store:
        order = Order.limit("005930", side="buy", quantity=1, limit_price=70000,
                            region="domestic", asset_class="stock", exchange="KRX",
                            client_order_id="cid-legacy")
        assert isinstance(store.try_claim("cid-legacy", order.fingerprint), Conflict)


def test_legacy_v9_survives_mutate_and_reopen(tmp_path):
    # 회귀: v9(지문 없는 in_flight) 로드 -> 변경(try_claim) -> v10 으로 재기록 -> 재오픈. 완전일치
    # 불변식이면 재기록된 v10 의 지문이 in_flight 를 덮지 못해 재오픈이 KISError 로 벽돌이 됐다.
    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "schema_version": 9,
        "in_flight": {"cid-legacy": _clock().isoformat()},
        "fingerprints": {},
        "reports": {},
    }), encoding="utf-8")
    with OrderStore(path, now=_clock) as store:
        order = Order.limit("005930", side="buy", quantity=1, limit_price=70000,
                            region="domestic", asset_class="stock", exchange="KRX",
                            client_order_id="cid-new")
        store.try_claim("cid-new", order.fingerprint)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 12
    with OrderStore(path, now=_clock):           # 재오픈이 벽돌이 되면 안 된다
        pass


def test_legacy_v9_without_fingerprints_still_loads(tmp_path):
    # v1..v9 레거시는 지문 결여가 적법 -- v10 전용 지문-완전성 불변식이 레거시 로드를 막지 않는다.
    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "schema_version": 9,
        "in_flight": {"cid-x": _clock().isoformat()},
        "fingerprints": {},                      # 레거시: 지문 없음
        "reports": {},
    }), encoding="utf-8")
    with OrderStore(path, now=_clock):
        pass


_OMITTED = object()


def _valid_report_and_pending_file(tmp_path):
    path = _valid_v10_file(tmp_path)
    pending = Order.market("005930", side="buy", quantity=1, client_order_id="cid-pending")
    with OrderStore(path, now=_clock, retention_days=0) as store:
        store.try_claim(pending.client_order_id, pending.fingerprint)
    return path


def _assert_bad_report_preserves_file_and_releases_lock(path, valid_bytes):
    corrupted_bytes = path.read_bytes()
    accepted = None
    try:
        with pytest.raises(KISError, match="손상"):
            accepted = OrderStore(path, now=_clock, retention_days=0)
    finally:
        if accepted is not None:
            accepted.close()  # RED 실행에서 잘못 수용한 저장소도 잠금을 회수한다
    assert path.read_bytes() == corrupted_bytes
    path.write_bytes(valid_bytes)
    with OrderStore(path, now=_clock, retention_days=0) as corrected:
        assert corrected.report_for("cid-done") is not None
        assert corrected.fingerprint_for("cid-done") is not None
        assert corrected.is_in_flight("cid-pending")
        assert corrected.fingerprint_for("cid-pending") is not None


@pytest.mark.parametrize("status", list(OrderStatus))
@pytest.mark.parametrize("field, value", [
    ("filled_quantity", "NaN"), ("filled_quantity", "sNaN"),
    ("filled_quantity", "Infinity"), ("filled_quantity", "-Infinity"), ("filled_quantity", "-1"),
    ("average_price", "NaN"), ("average_price", "sNaN"),
    ("average_price", "Infinity"), ("average_price", "-Infinity"),
])
def test_persisted_nonfinite_or_negative_fill_rejected_in_every_status(tmp_path, status, field, value):
    path = _valid_report_and_pending_file(tmp_path)
    valid_bytes = path.read_bytes()
    data = json.loads(valid_bytes)
    data["reports"]["cid-done"].update(status=status.value)
    data["reports"]["cid-done"][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    _assert_bad_report_preserves_file_and_releases_lock(path, valid_bytes)


@pytest.mark.parametrize("field", ["filled_quantity", "average_price"])
@pytest.mark.parametrize("value", [_OMITTED, "", "   ", "bad-number", True, {}, []])
def test_persisted_numeric_field_invalid_shapes_rejected(tmp_path, field, value):
    path = _valid_report_and_pending_file(tmp_path)
    valid_bytes = path.read_bytes()
    data = json.loads(valid_bytes)
    record = data["reports"]["cid-done"]
    if value is _OMITTED:
        record.pop(field)
    else:
        record[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    _assert_bad_report_preserves_file_and_releases_lock(path, valid_bytes)


def test_persisted_null_fill_is_not_optional(tmp_path):
    path = _valid_report_and_pending_file(tmp_path)
    valid_bytes = path.read_bytes()
    data = json.loads(valid_bytes)
    data["reports"]["cid-done"]["filled_quantity"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    _assert_bad_report_preserves_file_and_releases_lock(path, valid_bytes)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("value", [_OMITTED, None, "", "not-a-date", "2026-08-31", "2026-08-31T10:00:00"])
def test_persisted_report_timestamp_requires_timezone_aware_value(tmp_path, legacy, value):
    path = _valid_report_and_pending_file(tmp_path)
    valid_bytes = path.read_bytes()
    data = json.loads(valid_bytes)
    record = data["reports"]["cid-done"]
    record.pop("recorded_at")
    key = "submitted_at" if legacy else "recorded_at"
    if legacy:
        data["schema_version"] = 6
    if value is not _OMITTED:
        record[key] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    _assert_bad_report_preserves_file_and_releases_lock(path, valid_bytes)


@pytest.mark.parametrize("status", list(OrderStatus))
@pytest.mark.parametrize("filled", [0, "0", "3.25"])
@pytest.mark.parametrize("average", [None, "0", "-10", "70000"])
def test_persisted_finite_report_accepts_status_neutral_price_and_fill_contract(tmp_path, status, filled, average):
    path = _valid_v10_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data["reports"]["cid-done"]
    record.update(status=status.value, filled_quantity=filled, average_price=average)
    if status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
        record["order_id"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    with OrderStore(path, now=_clock, retention_days=0) as store:
        report = store.report_for("cid-done")
        assert report is not None
        assert report.status is status
        assert report.filled_quantity == Decimal(str(filled))
        assert report.average_price == (None if average is None else Decimal(average))
        assert report.order_id == record["order_id"]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("offset, minutes", [("+00:00", 0), ("+09:00", 540), ("+05:30", 330)])
def test_persisted_report_preserves_aware_offsets_and_legacy_key(tmp_path, legacy, offset, minutes):
    path = _valid_v10_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data["reports"]["cid-done"]
    record.pop("recorded_at")
    key = "submitted_at" if legacy else "recorded_at"
    if legacy:
        data["schema_version"] = 6
    record[key] = "2026-08-31T10:00:00" + offset
    path.write_text(json.dumps(data), encoding="utf-8")
    with OrderStore(path, now=_clock, retention_days=0) as store:
        report = store.report_for("cid-done")
        assert report.recorded_at.utcoffset() == timedelta(minutes=minutes)
        assert report.recorded_at.hour == 10


def test_persisted_action_report_fill_can_exceed_requested_change_quantity(tmp_path):
    path = tmp_path / "action.json"
    fingerprint = ChangeActionFingerprint(
        original_client_order_id="original", side="buy", order_type="limit",
        quantity="1", limit_price="100", action="modify", time_in_force="day",
        region="domestic", asset_class="derivative", exchange="KRX",
    )
    report = ExecutionReport(client_order_id="change", order_id="new-order", symbol="101W09", side="buy",
                             status=OrderStatus.PENDING_REPLACE, filled_quantity=Decimal(100),
                             average_price=None, recorded_at=_clock())
    with OrderStore(path, now=_clock, retention_days=0) as store:
        store.record(report, fingerprint)
    with OrderStore(path, now=_clock, retention_days=0) as store:
        assert store.report_for("change").filled_quantity == 100

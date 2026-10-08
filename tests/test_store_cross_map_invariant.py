"""로드 시 크로스-맵 불변식 검증 회귀 -- 세 맵(in_flight/fingerprints/reports)을 독립 디코드하면
reports 키 != 내장 client_order_id 인 손상 파일이 try_claim 에서 다른 주문의 Completed 리포트를
재생(오확정)할 수 있다. 손상 파일은 레코드-손상과 같은 fail-closed(KISError)로 거부해야 한다."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kis_trader.errors import KISError
from kis_trader.order import Order
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
    assert data["schema_version"] == 10
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


@pytest.mark.parametrize("mutate", [
    _rekey_report_mismatch,
    _report_also_in_flight,
    _fingerprint_extra,
    _report_cid_null,
    _report_symbol_null,
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
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 10
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

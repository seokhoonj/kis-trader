"""스키마 v9 -- 리포트에 해외 예약 접수일자(`receipt_date`, RSVN_ORD_RCIT_DT) 영속.

아시아 예약 취소는 예약번호(OVRS_RSVN_ODNO)와 접수일자를 함께 다시 실어야 하므로, 접수일자가
`_raw` 가 아니라 영속 필드여야 재기동 후에도 취소가 가능하다(`organization_number` 와 같은 급).
v8 이하 레코드는 `receipt_date` 키가 없어 None 으로 로드된다(하위호환).
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kis_trader.order import ReservedOrderFingerprint
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import OrderStore

_KST = timezone(timedelta(hours=9))
#: 고정 기록 시각 -- datetime.now 를 쓰면 영속 fixture 가 실행 시각에 따라 달라진다(결정성).
_RECORDED_AT = datetime(2026, 8, 18, 9, 0, tzinfo=_KST)


def _asia_fingerprint() -> ReservedOrderFingerprint:
    return ReservedOrderFingerprint(
        symbol="00700", side="buy", order_type="limit", quantity="100",
        limit_price="350.00", end_date="", exchange="overseas-reserved-asia",
    )


def _asia_report() -> ExecutionReport:
    return ExecutionReport(
        client_order_id="c1", order_id="0030138295", symbol="00700", side="buy",
        status=OrderStatus.PENDING_NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=_RECORDED_AT, receipt_date="20260818",
    )


def test_receipt_date_round_trips(tmp_path):
    # retention_days=0 으로 보존 정리를 끈다 -- 고정 기록 시각이 기본 보존창(7일)을 넘겨도
    # c1 이 정리되면 안 된다(이 테스트는 receipt_date 왕복만 본다).
    path = tmp_path / "orders.json"
    with OrderStore(path=path, retention_days=0) as store:
        store.record(_asia_report(), _asia_fingerprint())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 9
    assert data["reports"]["c1"]["receipt_date"] == "20260818"
    with OrderStore(path=path, retention_days=0) as reloaded:
        report = reloaded.report_for("c1")
        assert report is not None
        assert report.receipt_date == "20260818"


def test_v8_record_loads_with_receipt_date_none(tmp_path):
    """v8 저장소(receipt_date 키 없음)는 재작성 없이 열리고 receipt_date=None 으로 로드된다."""
    path = tmp_path / "orders.json"
    path.write_text(json.dumps({
        "schema_version": 8,
        "in_flight": {},
        "fingerprints": {
            "c1": ["AAPL", "buy", "limit", "1", "150", "", "day", "overseas-reserved",
                   "", "", "regular", "", "KRX", ""],
        },
        "reports": {
            "c1": {
                "client_order_id": "c1", "order_id": "0030138295", "symbol": "AAPL",
                "side": "buy", "status": "pending_new", "filled_quantity": "0",
                "average_price": None, "recorded_at": "2026-08-17T09:00:00+09:00",
                "organization_number": None,
            },
        },
    }), encoding="utf-8")
    # retention_days=0 으로 보존 정리를 끈다 -- 이 테스트는 하위호환 로드만 보며,
    # fixture 의 고정 날짜가 기본 보존창(7일)을 넘겨도 c1 이 정리되면 안 된다.
    with OrderStore(path=path, retention_days=0) as store:
        report = store.report_for("c1")
        assert report is not None
        assert report.receipt_date is None

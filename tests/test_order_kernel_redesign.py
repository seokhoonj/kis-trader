"""주문 안전 커널 재설계(A-08/A-16/A-17/A-18)의 계약을 고정하는 테스트.

- A-16: `submitted_at` -> `recorded_at` + 스키마 v6 -> v7, 구 저장소(옛 키) 하위호환 로드.
- A-17: 인메모리 지문 태그드 유니온 + 온-디스크 위치 코덱(왕복 + 바이트 동결).
- A-18: `try_claim` 판별 결과(완료엔 리포트가 항상 있어 "리포트 없는 완료"가 표현 불가능).
- A-08: 공개 주문 write 표면의 `limit_price`, 주문 결과 타입의 `order_price`.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest

from kis_trader.order import (
    ChangeActionFingerprint,
    ImmediateOrderFingerprint,
    Order,
    OrderFingerprint,
    ReservedOrderFingerprint,
    decode_fingerprint,
    encode_fingerprint,
)
from kis_trader.report import ExecutionReport, OrderStatus
from kis_trader.store import (
    _SCHEMA_VERSION,
    Claimed,
    ClaimResult,
    Completed,
    Conflict,
    InFlight,
    OrderStore,
)

# --- A-17: 지문 코덱 왕복 + 온-디스크 바이트 동결 ---------------------------------
#: 각 변형의 인코딩이 재설계 전(``list(Fingerprint(...))``)이 내던 **정확한** 13-슬롯 바이트다.
#: 이 값이 바뀌면 dedup 정체성/재조회 매칭/이중전송 장벽이 기존 저장소와 어긋난다(회귀 감지).
_FROZEN_BYTES = {
    "immediate": (
        ImmediateOrderFingerprint(
            symbol="005930", side="buy", order_type="limit", quantity="10",
            limit_price="70000", stop_price="", time_in_force="day", exchange="XKRX",
        ),
        ["005930", "buy", "limit", "10", "70000", "", "day", "XKRX", "", "", "regular", "", "KRX"],
    ),
    "immediate_credit": (
        ImmediateOrderFingerprint(
            symbol="009150", side="buy", order_type="limit", quantity="1", limit_price="130000",
            stop_price="", time_in_force="day", exchange="XKRX", credit_type="26", loan_date="20211103",
        ),
        ["009150", "buy", "limit", "1", "130000", "", "day", "XKRX", "26", "20211103", "regular", "", "KRX"],
    ),
    "immediate_daytime": (  # session 슬롯(idx 10) 을 non-default 로 고정 -- 미국 오버나이트 거래
        ImmediateOrderFingerprint(
            symbol="AAPL", side="buy", order_type="limit", quantity="5", limit_price="150",
            stop_price="", time_in_force="day", exchange="NAS", session="overnight",
        ),
        ["AAPL", "buy", "limit", "5", "150", "", "day", "NAS", "", "", "overnight", "", "KRX"],
    ),
    "immediate_division_board": (
        ImmediateOrderFingerprint(
            symbol="005930", side="buy", order_type="market", quantity="10", limit_price="",
            stop_price="", time_in_force="day", exchange="NXTE", division="immediate_limit", board="NXT",
        ),
        ["005930", "buy", "market", "10", "", "", "day", "NXTE", "", "", "regular", "immediate_limit", "NXT"],
    ),
    "reserved_domestic": (
        ReservedOrderFingerprint(
            symbol="005930", side="buy", order_type="limit", quantity="10",
            limit_price="70000", end_date="20240610", exchange="reserved",
        ),
        ["005930", "buy", "limit", "10", "70000", "20240610", "day", "reserved", "", "", "regular", "", "KRX"],
    ),
    "reserved_overseas": (
        ReservedOrderFingerprint(
            symbol="AAPL", side="buy", order_type="limit", quantity="1",
            limit_price="150", end_date="", exchange="overseas-reserved",
        ),
        ["AAPL", "buy", "limit", "1", "150", "", "day", "overseas-reserved", "", "", "regular", "", "KRX"],
    ),
    "change_cancel": (
        ChangeActionFingerprint(
            original_client_order_id="orig-1", side="buy", order_type="limit", quantity="10",
            limit_price="", action="cancel", time_in_force="day", exchange="XKRX",
        ),
        ["orig-1", "buy", "limit", "10", "", "cancel", "day", "action:XKRX", "", "", "regular", "", "KRX"],
    ),
    "change_modify_overseas": (
        ChangeActionFingerprint(
            original_client_order_id="ov-1", side="sell", order_type="limit", quantity="3",
            limit_price="412.5", action="modify", time_in_force="day", exchange="NAS",
        ),
        ["ov-1", "sell", "limit", "3", "412.5", "modify", "day", "action:NAS", "", "", "regular", "", "KRX"],
    ),
}


@pytest.mark.parametrize("name", list(_FROZEN_BYTES))
def test_fingerprint_encode_matches_frozen_on_disk_bytes(name):
    """encode(fp) 는 재설계 전 코드가 내던 위치 튜플과 **바이트 동일**해야 한다."""
    fingerprint, frozen = _FROZEN_BYTES[name]
    assert encode_fingerprint(fingerprint) == frozen


@pytest.mark.parametrize("name", list(_FROZEN_BYTES))
def test_fingerprint_round_trips_and_preserves_type(name):
    """decode(encode(fp)) == fp -- 값·변형(type)·해시가 모두 보존된다(dedup 정체성 유지)."""
    fingerprint, frozen = _FROZEN_BYTES[name]
    decoded = decode_fingerprint(encode_fingerprint(fingerprint))
    assert decoded == fingerprint
    assert type(decoded) is type(fingerprint)
    assert hash(decoded) == hash(fingerprint)
    # 동결 바이트에서 바로 디코딩해도(예전 저장소 재로드) 같은 논리 지문이 된다.
    assert decode_fingerprint(frozen) == fingerprint


def test_decode_pads_short_legacy_record_with_defaults():
    """구버전 짧은 레코드(v3=11슬롯)는 뒤쪽 필드가 기본값으로 채워져 즉시 지문으로 디코딩된다."""
    fp = decode_fingerprint(["005930", "buy", "market", "10", "", "", "day", "XKRX", "", "", "regular"])
    assert isinstance(fp, ImmediateOrderFingerprint)
    assert (fp.division, fp.session, fp.board) == ("", "regular", "KRX")


def test_decode_too_short_record_is_rejected():
    """8슬롯 미만은 손상으로 거부한다(예전 Fingerprint(*fp) 의 필수 필드 부족과 같은 fail-closed)."""
    with pytest.raises(ValueError):
        decode_fingerprint(["005930", "buy", "limit"])


def test_decode_too_long_record_is_rejected():
    """13슬롯 초과는 손상/변조로 거부한다(예전 Fingerprint(*fp) 가 인자 과다로 실패하던 fail-closed)."""
    with pytest.raises(ValueError):
        decode_fingerprint(
            ["005930", "buy", "limit", "10", "70000", "", "day", "XKRX", "", "", "regular", "", "KRX", "EXTRA"]
        )


def test_fingerprint_equality_is_positional_and_cross_variant_safe():
    """동등성 = 위치 인코딩 동등성. 변형이 달라도 인코딩이 겹치지 않아 교차 충돌이 없다."""
    imm = _FROZEN_BYTES["immediate"][0]
    resd = _FROZEN_BYTES["reserved_domestic"][0]
    chg = _FROZEN_BYTES["change_cancel"][0]
    assert imm != resd and imm != chg and resd != chg          # exchange 슬롯이 갈라 준다
    # 같은 변형·같은 필드면 같다.
    twin = ImmediateOrderFingerprint(
        symbol="005930", side="buy", order_type="limit", quantity="10",
        limit_price="70000", stop_price="", time_in_force="day", exchange="XKRX",
    )
    assert imm == twin and hash(imm) == hash(twin)
    assert isinstance(imm, OrderFingerprint)


# --- A-16: 스키마 v7 + 구 저장소(submitted_at 키) 하위호환 로드/마이그레이션 --------
def _v6_store_json() -> dict:
    """옛 키(`submitted_at`)와 13-슬롯 지문을 쓰는 v6 저장소 스냅샷(재작성 전 형식)."""
    return {
        "schema_version": 6,
        "in_flight": ["ID-inflight"],
        "fingerprints": {
            "ID-done": ["005930", "buy", "limit", "10", "70000", "", "day", "XKRX", "", "", "regular", "", "KRX"],
            "ID-inflight": ["000660", "sell", "market", "3", "", "", "day", "XKRX", "", "", "regular", "", "KRX"],
        },
        "reports": {
            "ID-done": {
                "client_order_id": "ID-done", "order_id": "0000117057", "symbol": "005930",
                "side": "buy", "status": "new", "filled_quantity": "0", "average_price": None,
                "submitted_at": "2026-08-11T09:00:00+09:00", "organization_number": "01790",
            },
        },
    }


def test_v6_store_loads_and_maps_submitted_at_to_recorded_at(tmp_path):
    """v6 저장소(옛 submitted_at 키)를 재작성 없이 열고, 모든 레코드가 로드되며 recorded_at 이 채워진다."""
    path = tmp_path / "orders.json"
    path.write_text(json.dumps(_v6_store_json()), encoding="utf-8")
    with OrderStore(path=path) as store:
        report = store.report_for("ID-done")
        assert report is not None
        assert report.recorded_at == datetime.fromisoformat("2026-08-11T09:00:00+09:00")
        assert report.order_id == "0000117057"
        assert report.organization_number == "01790"
        # 지문·in-flight 도 모두 로드된다.
        assert isinstance(store.fingerprint_for("ID-done"), ImmediateOrderFingerprint)
        assert store.is_in_flight("ID-inflight")
        assert store.fingerprint_for("ID-inflight") is not None


def test_v6_store_resaves_as_schema_7_with_recorded_at_key(tmp_path):
    """v6 를 로드해 저장하면 스키마 7 과 `recorded_at` 키로 재기록된다(마이그레이션 경로)."""
    path = tmp_path / "orders.json"
    path.write_text(json.dumps(_v6_store_json()), encoding="utf-8")
    with OrderStore(path=path) as store:
        store.clear_in_flight("ID-inflight")   # 아무 write 나 -> _save_locked 로 재기록
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 7 == _SCHEMA_VERSION
    record = data["reports"]["ID-done"]
    assert "recorded_at" in record and "submitted_at" not in record
    assert record["recorded_at"] == "2026-08-11T09:00:00+09:00"


def test_v7_report_dict_omits_submitted_at_key(tmp_path):
    """새로 기록한 리포트의 온-디스크 레코드 키는 recorded_at(옛 submitted_at 키가 없다)."""
    path = tmp_path / "orders.json"
    fp = Order.limit("005930", side="buy", quantity=10, limit_price=70000).fingerprint
    report = ExecutionReport(
        client_order_id="ID-new", order_id="0000117057", symbol="005930", side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.fromisoformat("2026-08-12T09:00:00+09:00"),
    )
    with OrderStore(path=path) as store:
        store.record(report, fp)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 7
    assert set(data["reports"]["ID-new"]) >= {"recorded_at"}
    assert "submitted_at" not in data["reports"]["ID-new"]


# --- A-18: try_claim 판별 결과(완료엔 리포트가 항상 존재) -------------------------
def _report(cid: str) -> ExecutionReport:
    return ExecutionReport(
        client_order_id=cid, order_id="0000117057", symbol="005930", side="buy",
        status=OrderStatus.NEW, filled_quantity=Decimal(0), average_price=None,
        recorded_at=datetime.fromisoformat("2026-08-12T09:00:00+09:00"),
    )


def test_try_claim_returns_claimed_then_completed_carrying_report():
    store = OrderStore()
    fp = Order.limit("005930", side="buy", quantity=10, limit_price=70000).fingerprint
    first = store.try_claim("cid", fp)
    assert isinstance(first, Claimed)
    store.record(_report("cid"), fp)
    again = store.try_claim("cid", fp)
    # "완료인데 리포트 없음" 은 표현 불가능 -- Completed 는 항상 리포트를 싣는다.
    assert isinstance(again, Completed)
    assert again.report.client_order_id == "cid"


def test_try_claim_in_flight_and_conflict_variants():
    store = OrderStore()
    fp = Order.limit("005930", side="buy", quantity=10, limit_price=70000).fingerprint
    store.try_claim("cid", fp)                       # in-flight 로 만든다
    assert isinstance(store.try_claim("cid", fp), InFlight)
    other = Order.limit("005930", side="buy", quantity=99, limit_price=70000).fingerprint
    conflict = store.try_claim("cid", other)         # 같은 id, 다른 지문
    assert isinstance(conflict, Conflict)
    assert conflict.prior is None                    # in-flight 충돌엔 완료 리포트가 없다


def test_claim_result_variants_are_exhaustive():
    """판별 유니온은 정확히 이 네 변형이다(패턴 매칭이 빠짐없이 처리되게)."""
    assert set(ClaimResult.__args__) == {Claimed, Completed, InFlight, Conflict}


# --- A-08: 공개 write 표면의 limit_price / 결과 타입의 order_price -------------------
def test_public_write_surface_uses_limit_price():
    """대표 write 진입점들이 limit_price 를 받는다(price 가 아니라)."""
    import inspect

    from kis_trader.domestic.stock import DomesticStock
    from kis_trader.namespaces import DomesticAccount, OrdersNamespace
    from kis_trader.overseas.stock import OverseasStock

    def params(func):
        return set(inspect.signature(func).parameters)

    assert "limit_price" in params(Order.credit) and "price" not in params(Order.credit)
    assert "limit_price" in params(DomesticStock.buy)
    assert "limit_price" in params(DomesticStock.credit_buy)
    assert "limit_price" in params(DomesticStock.reserve_buy)
    assert "limit_price" in params(OverseasStock.overnight_buy)
    assert "limit_price" in params(OrdersNamespace.modify) and "price" not in params(OrdersNamespace.modify)
    assert "limit_price" in params(DomesticAccount.modify_reserved_order)


def test_order_result_types_expose_order_price():
    """주문 결과 타입은 주문단가를 order_price 로 노출한다(체결단가 price 와 구분)."""
    from kis_trader.open_order import OpenOrder
    from kis_trader.overseas_items import (
        OverseasAlgoOrder,
        OverseasOpenOrder,
        OverseasReservedOrder,
    )
    from kis_trader.pension_items import PensionOrder
    from kis_trader.reserved_order import ReservedOrder

    for dto in (OpenOrder, OverseasOpenOrder, OverseasAlgoOrder, OverseasReservedOrder,
                PensionOrder, ReservedOrder):
        fields = dto.__dataclass_fields__
        assert "order_price" in fields, dto.__name__
        assert "price" not in fields and "reserved_price" not in fields, dto.__name__

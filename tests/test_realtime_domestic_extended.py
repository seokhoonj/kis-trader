"""국내 index/ELW/bond 실시간 타입드 표면 테스트.

핸들의 각 잎이 정확한 ``(tr_id, tr_key)`` 로 구독하는지, 재명명한 엔티티가
``realtime.__all__`` 에 노출되는지를 검증한다. 파서/엔티티 자체는 별도 테스트가
있으므로 여기서는 표면(namespace)만 본다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kis_trader import realtime
from kis_trader.realtime.client import RealtimeClient
from kis_trader.realtime.domestic_namespace import RealtimeDomesticNamespace
from kis_trader.realtime.subscription import RealtimeSubscription


def _ns() -> tuple[RealtimeClient, RealtimeDomesticNamespace]:
    c = RealtimeClient("approval", "wss://example/ws", connect=None)
    return c, RealtimeDomesticNamespace(c)


# (id, ns -> subscription, expected tr_id) -- index/ELW/bond 모든 잎의 TR 매핑을 한 표로 검증.
_TR_ROWS: list[
    tuple[str, Callable[[RealtimeDomesticNamespace], RealtimeSubscription[object]], str]
] = [
    # 국내지수
    ("index_trades", lambda ns: ns.index("0001").trades(), "H0UPCNT0"),
    ("index_expected", lambda ns: ns.index("0001").expected_conclusion(), "H0UPANC0"),
    ("index_program", lambda ns: ns.index("0001").program_trade(), "H0UPPGM0"),
    # ELW
    ("elw_trades", lambda ns: ns.elw("58J297").trades(), "H0EWCNT0"),
    ("elw_book", lambda ns: ns.elw("58J297").order_book(), "H0EWASP0"),
    ("elw_expected", lambda ns: ns.elw("58J297").expected_conclusion(), "H0EWANC0"),
    # 일반채권
    ("bond_trades", lambda ns: ns.bond("KR103501GA34").trades(), "H0BJCNT0"),
    ("bond_book", lambda ns: ns.bond("KR103501GA34").order_book(), "H0BJASP0"),
    # 채권지수
    ("bond_index_trades", lambda ns: ns.bond_index("KBPR01").trades(), "H0BICNT0"),
]


@pytest.mark.parametrize(
    ("build", "expected_tr"),
    [pytest.param(build, tr, id=name) for name, build, tr in _TR_ROWS],
)
def test_every_mapping_subscribes_exact_tr(
    build: Callable[[RealtimeDomesticNamespace], RealtimeSubscription[object]], expected_tr: str
) -> None:
    _, ns = _ns()
    sub = build(ns)
    assert sub.tr_id == expected_tr


def test_tr_key_is_the_code_directly() -> None:
    c, ns = _ns()
    sub = ns.elw("58J297").order_book()
    assert (sub.tr_id, sub.tr_key) == ("H0EWASP0", "58J297")
    assert (sub.tr_id, sub.tr_key) in c._subscriptions


def test_bond_and_bond_index_use_separate_code_spaces() -> None:
    _, ns = _ns()
    bond = ns.bond("KR103501GA34").trades()
    bond_index = ns.bond_index("KBPR01").trades()
    assert (bond.tr_id, bond.tr_key) == ("H0BJCNT0", "KR103501GA34")
    assert (bond_index.tr_id, bond_index.tr_key) == ("H0BICNT0", "KBPR01")


@pytest.mark.parametrize(
    "name",
    [
        "IndexTick",
        "IndexExpectedConclusion",
        "IndexProgramTrade",
        "ELWTick",
        "ELWOrderBook",
        "ELWExpectedConclusion",
        "BondTick",
        "BondOrderBook",
        "BondIndexTick",
    ],
)
def test_renamed_and_new_entities_exported(name: str) -> None:
    assert name in realtime.__all__
    assert hasattr(realtime, name)

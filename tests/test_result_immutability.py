"""프로즌 result 타입의 실질 불변성 계약 -- deep freeze + tuple 정규화 + hashable.

``freeze_vendor_payload`` 이 벤더 원본 payload 를 어떤 깊이에서도 변경 불가능하게 얼리는지,
result 타입의 ``_raw`` 가 :class:`~types.MappingProxyType` 로 노출되며 그 안의 리스트가
``tuple`` 이 되는지, tuple 로 선언된 필드에 리스트를 넣어도 tuple 로 저장되는지, 그리고
매핑 필드를 가진 프로즌 result 를 ``hash()`` 할 수 있는지(예전에는 ``TypeError``)를 고정한다.
값 자체는 컨테이너만 불변형으로 바뀔 뿐 그대로임을 라운드트립으로 확인한다.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from kis_trader._internal._freeze import freeze_vendor_payload
from kis_trader.broker import BrokerActivitySummary
from kis_trader.market_items import MarketInvestorSnapshot


def test_freeze_vendor_payload_deep_and_readonly() -> None:
    frozen = freeze_vendor_payload(
        {"scalar": 5, "text": "abc", "seq": [1, {"inner": [2, 3]}]}
    )
    # 중첩 dict -> MappingProxyType, 중첩 list -> tuple (재귀적으로)
    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["seq"], tuple)
    assert isinstance(frozen["seq"][1], MappingProxyType)
    assert isinstance(frozen["seq"][1]["inner"], tuple)
    # 스칼라/문자열은 그대로
    assert frozen["scalar"] == 5
    assert frozen["text"] == "abc"
    # 어떤 깊이에서도 in-place 변경 불가
    with pytest.raises(TypeError):
        frozen["scalar"] = 9  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen["seq"][1]["inner"] = 0  # type: ignore[index]


def test_freeze_vendor_payload_value_roundtrip() -> None:
    # 컨테이너 타입만 바뀌고 값(내용)은 동일해야 한다.
    payload = {"a": [1, 2, 3], "b": {"c": ["x", "y"]}}
    frozen = freeze_vendor_payload(payload)
    assert frozen["a"] == (1, 2, 3)
    assert list(frozen["a"]) == payload["a"]
    assert frozen["b"]["c"] == ("x", "y")
    assert list(frozen["b"]["c"]) == payload["b"]["c"]


def test_result_raw_is_deep_frozen_and_immutable() -> None:
    snap = MarketInvestorSnapshot(
        market_code="0001",
        industry_code="0001",
        participants={},
        _raw={"output2": [{"k": "v"}], "nested": {"list": [1, 2]}},
    )
    # _raw 는 MappingProxyType, 내부 리스트는 tuple 로 깊게 얼려진다.
    assert isinstance(snap._raw, MappingProxyType)
    assert isinstance(snap._raw["output2"], tuple)
    assert isinstance(snap._raw["output2"][0], MappingProxyType)
    assert isinstance(snap._raw["nested"]["list"], tuple)
    # 값은 그대로.
    assert snap._raw["output2"][0]["k"] == "v"
    assert snap._raw["nested"]["list"] == (1, 2)
    # 호출자가 _raw 를 in-place 로 바꿀 수 없다.
    with pytest.raises(TypeError):
        snap._raw["output2"] = []  # type: ignore[index]


def test_frozen_result_with_mapping_field_is_hashable() -> None:
    # 매핑 필드(participants)를 가진 프로즌 result 도 hash()/set 멤버십이 된다(예전엔 TypeError).
    snap = MarketInvestorSnapshot(
        market_code="0001", industry_code="0001", participants={}, _raw={}
    )
    assert isinstance(hash(snap), int)
    assert snap in {snap}


def test_tuple_field_normalizes_list_input() -> None:
    # tuple 로 선언된 필드에 리스트를 넣어도 tuple 로 저장된다(진짜 불변).
    summary = BrokerActivitySummary(
        symbol="005930",
        sellers=[1],
        buyers=[2],
        _raw={},
    )
    assert isinstance(summary.sellers, tuple)
    assert isinstance(summary.buyers, tuple)
    assert isinstance(hash(summary), int)

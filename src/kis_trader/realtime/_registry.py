"""실시간 TR 레지스트리 -- tr_id -> (파서, 필드 수, 암호화 여부).

연결 계층(:mod:`._connection`)이 수신 프레임의 ``tr_id`` 로 파서를 찾는다. 파서가 아직 없으면
레지스트리에 없을 수 있고, 그 경우 연결 계층은 원시 레코드(``list[str]``)를 그대로 흘려보낸다
(파서는 자산군별로 나중에 등록된다). 파서는 ``list[str]`` (한 레코드의 ``^`` 필드) 를 받아
결과 엔티티를 돌려주는 순수 함수다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: 한 레코드(필드 리스트)를 결과 엔티티로 바꾸는 순수 함수.
RecordParser = Callable[[list[str]], Any]


@dataclass(frozen=True, slots=True)
class TRSpec:
    """한 실시간 TR 의 명세.

    ``field_count`` 는 멀티레코드 분해에 필요한 레코드당 필드 수. ``encrypted`` 는 통보류
    (체결통보/주문통보)처럼 프레임이 암호화되는지 -- 다만 실제 암/평문은 프레임 flag 로도
    판별되므로 이 값은 참고/검증용이다.
    """

    tr_id: str
    field_count: int
    parser: RecordParser
    encrypted: bool = False


_REGISTRY: dict[str, TRSpec] = {}


def register(spec: TRSpec) -> None:
    """TR 명세를 등록한다(자산군 파서 모듈이 import 시 호출)."""
    _REGISTRY[spec.tr_id] = spec


def lookup(tr_id: str) -> TRSpec | None:
    """등록된 TR 명세를 찾는다. 없으면 ``None`` (원시 레코드로 흘려보냄)."""
    return _REGISTRY.get(tr_id)

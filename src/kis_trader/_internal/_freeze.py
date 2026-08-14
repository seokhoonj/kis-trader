"""벤더 원본 payload 를 깊은 불변(deep-immutable) 구조로 얼린다.

프로즌 result 타입은 ``_raw`` 에 KIS 응답의 파싱된 JSON 조각을 그대로 담는다. 얕은
``MappingProxyType(dict(...))`` 만으로는 중첩된 ``dict``/``list`` 가 여전히 변경 가능해
'불변'이라는 계약이 깨진다(예: ``result._raw["output"].clear()``). :func:`freeze_vendor_payload`
는 중첩 ``Mapping`` 을 재귀적으로 :class:`~types.MappingProxyType` 로, ``Sequence`` 를
``tuple`` 로 바꿔 어떤 깊이에서도 in-place 변경을 막는다. 스칼라(숫자/불리언/``None`` 등)는
그대로 둔다. 문자열/바이트열은 시퀀스이지만 원자값이므로 재귀 대상에서 제외한다.

값 자체는 바뀌지 않는다 -- 컨테이너 타입만 불변형으로 감싼다. 자산군과 무관한 공용 헬퍼이므로
result 타입을 정의하는 모든 모듈이 import 한다(순환 import 금지: 표준 라이브러리에만 의존).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any


def freeze_vendor_payload(payload: Any) -> Any:
    """파싱된 JSON payload 를 깊은 불변 구조로 재귀 변환한다.

    - ``Mapping`` -> :class:`~types.MappingProxyType` (값도 재귀적으로 freeze)
    - ``str``/``bytes``/``bytearray`` 아닌 ``Sequence`` -> ``tuple`` (원소도 재귀적으로 freeze)
    - 그 외(스칼라) -> 그대로 반환

    반환값은 어떤 깊이에서도 in-place 로 변경할 수 없다. 이미 얼린 구조를 다시 넣어도
    (idempotent) 안전하다.
    """
    if isinstance(payload, Mapping):
        return MappingProxyType(
            {key: freeze_vendor_payload(value) for key, value in payload.items()}
        )
    if isinstance(payload, (str, bytes, bytearray)):
        return payload
    if isinstance(payload, Sequence):
        return tuple(freeze_vendor_payload(item) for item in payload)
    return payload

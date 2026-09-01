"""공개 표면 가드 -- ``__all__`` 의 모든 이름이 실제로 루트에서 import 되는지, 그리고 이 브랜치가
공개 시그니처 타입(입력 어휘)을 루트로 승격한 것이 유지되는지 고정한다(수출 드리프트 방지)."""

from __future__ import annotations

import pytest

import kis_trader


@pytest.mark.parametrize("name", kis_trader.__all__)
def test_every_exported_name_resolves(name):
    # __all__ 에 있는 이름은 반드시 kis_trader 에서 실제로 접근 가능해야 한다(오타·삭제 드리프트 차단).
    assert hasattr(kis_trader, name), f"{name} 은 __all__ 에 있으나 kis_trader 에서 접근 불가"


# 공개 메서드 시그니처가 이름으로 참조하는 입력 어휘 타입 -- py.typed 하에서 사용자가 명명/주석할 수
# 있도록 루트로 승격됨. (session 종류 Session, 옵션 Right 는 루트에서 이름 충돌이라 order 서브모듈로만 둔다.)
_INPUT_VOCABULARY = [
    "Numeric", "Side", "CreditType", "Environment", "OrderType", "TimeInForce",
    "Transport", "RawResponse", "AccountKind",
]


@pytest.mark.parametrize("name", _INPUT_VOCABULARY)
def test_input_vocabulary_is_root_reachable(name):
    assert name in kis_trader.__all__ and hasattr(kis_trader, name)


@pytest.mark.parametrize("name", ["Session", "Right"])
def test_colliding_names_are_not_root_exported(name):
    # order 서브모듈로만 도달(kis_trader.order.Session/.Right); 루트 수출은 requests.Session /
    # AccountRight·RightsOffering 과 충돌하므로 제외한다.
    assert name not in kis_trader.__all__
    assert hasattr(__import__("kis_trader.order", fromlist=[name]), name)

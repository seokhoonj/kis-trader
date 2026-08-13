"""와이어 값 강제변환 -- 전일대비 부호 복원의 fail-closed 계약.

``_apply_change_sign`` 은 KIS 대비부호(prdy_vrss_sign)로 크기의 방향을 정한다. 하락(4/5)은
음수, 상승/보합(1/2/3)과 빈 부호("")는 양수로 두되, **알 수 없는** 부호는 조용히 양수로 두지
않고 예외로 막는다(잘못된 부호는 대비값을 통째로 뒤집으므로).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kis_trader._wire import _apply_change_sign
from kis_trader.errors import KISError


@pytest.mark.parametrize("sign", ["1", "2", "3"])
def test_apply_change_sign_up_and_flat_stay_positive(sign):
    assert _apply_change_sign(Decimal(400), sign) == Decimal(400)
    assert _apply_change_sign(Decimal(-400), sign) == Decimal(400)   # 크기로 정규화 후 방향


@pytest.mark.parametrize("sign", ["4", "5"])
def test_apply_change_sign_down_codes_go_negative(sign):
    assert _apply_change_sign(Decimal(400), sign) == Decimal(-400)
    assert _apply_change_sign(Decimal(-400), sign) == Decimal(-400)


def test_apply_change_sign_empty_is_no_change_positive():
    assert _apply_change_sign(Decimal(400), "") == Decimal(400)


@pytest.mark.parametrize("sign", ["0", "6", "9", "x", "12"])
def test_apply_change_sign_unknown_code_fails_closed(sign):
    with pytest.raises(KISError):
        _apply_change_sign(Decimal(400), sign)

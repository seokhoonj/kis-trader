"""매매구분코드(sll_buy_dvsn_cd) -> 방향 파싱은 fail-closed -- 알 수 없는 코드는 raise.

주문/체결/예약 DATA 의 ``side`` 는 ``Side``(buy/sell)로 타입돼 있고, 파서가 이를 보장한다:
매핑되지 않는 코드는 ``""`` 로 뭉개지 않고 :class:`KISError` 를 던진다(오귀속/오매칭 방지).
국내·해외·연금 각 엔진의 공용 헬퍼가 같은 규칙을 공유하는지 고정한다.
"""

from __future__ import annotations

import pytest

from kis_trader.domestic._engine._parse import _side_from_code as domestic_side
from kis_trader.errors import KISError
from kis_trader.overseas._engine._parse import _side_from_code as overseas_side
from kis_trader.pension._engine import _side_from_code as pension_side

_HELPERS = [domestic_side, overseas_side, pension_side]


@pytest.mark.parametrize("side_from_code", _HELPERS)
def test_maps_known_codes(side_from_code):
    assert side_from_code("01") == "sell"
    assert side_from_code("02") == "buy"


@pytest.mark.parametrize("side_from_code", _HELPERS)
@pytest.mark.parametrize("code", ["", "00", "03", "99", None, "sell"])
def test_unknown_code_fails_closed(side_from_code, code):
    with pytest.raises(KISError):
        side_from_code(code)

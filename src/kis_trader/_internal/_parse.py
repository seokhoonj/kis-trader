"""엔진 공용 저수준 파서 -- 지역/자산군을 가리지 않는 순수 변환 헬퍼.

매매구분코드 같은 와이어 어휘 매핑을 한 곳에 둬 domestic/overseas/pension 엔진이 같은 규칙으로
응답을 읽게 한다. ``_wire.py`` 가 아니라 여기 두는 이유: ``Side`` 는 ``order`` 에 있고 ``_wire`` 를
``order`` 가 import 하므로, ``_wire`` 에서 ``order`` 를 import 하면 순환이 된다.
"""

from __future__ import annotations

from ..errors import KISError
from ..order import Side

#: KIS 매매구분코드 -> 방향. 01 매도 / 02 매수.
_SIDE: dict[str, Side] = {"01": "sell", "02": "buy"}


def _side_from_code(code: object) -> Side:
    """벤더 매매구분코드(01 매도 / 02 매수)를 방향으로 -- 알 수 없는 코드는 fail-closed(:class:`KISError`).
    ``side=""`` 로 뭉개면 buy/sell 어느 쪽도 아닌 주문 레코드가 새어 이후 오귀속/오매칭을 부른다."""
    text = str(code or "").strip()
    try:
        return _SIDE[text]
    except KeyError:
        raise KISError(f"알 수 없는 매매구분코드: {text!r} (01 매도 / 02 매수만 유효).") from None

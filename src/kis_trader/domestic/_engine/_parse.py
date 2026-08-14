"""국내 계좌/주문 엔진 공용 파서 -- account/reserved_orders 가 함께 쓰는 매매구분 헬퍼.

KIS 매매구분코드(01 매도 / 02 매수)를 방향으로 옮기는 규칙을 한 곳에 둔다.
"""

from __future__ import annotations

from ...errors import KISError
from ...order import Side

_SIDE: dict[str, Side] = {"01": "sell", "02": "buy"}


def _side_from_code(code: object) -> Side:
    """벤더 매매구분코드(01 매도 / 02 매수)를 방향으로 -- 알 수 없는 코드는 fail-closed(:class:`KISError`).
    ``side=""`` 로 뭉개면 buy/sell 어느 쪽도 아닌 주문 레코드가 새어 이후 오귀속/오매칭을 부른다."""
    text = str(code or "").strip()
    try:
        return _SIDE[text]
    except KeyError:
        raise KISError(f"알 수 없는 매매구분코드: {text!r} (01 매도 / 02 매수만 유효).") from None

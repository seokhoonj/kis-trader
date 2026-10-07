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


#: 선물옵션 잔고는 코드(sll_buy_dvsn_cd) 대신 매도매수구분명("매수"/"매도")만 주는 행이 있어 이름으로 옮긴다.
_SIDE_BY_NAME: dict[str, Side] = {"매수": "buy", "매도": "sell"}


def _side_from_name(name: object) -> Side | None:
    """벤더 매도매수구분명("매수"/"매도")을 방향으로 옮긴다 -- 코드 경로(:func:`_side_from_code`)와 달리
    이름만 주는 잔고 행에 쓴다. 비거나 알 수 없으면 None(방향 없는 정산 잔여 lot 등); 원문은 ``_raw`` 로 남는다."""
    return _SIDE_BY_NAME.get(str(name or "").strip())

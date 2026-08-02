"""국내주식 자산군 세그먼트 파사드 -- :class:`DomesticStock`.

KIS URL 구조(``/uapi/domestic-stock/v1/{quotations,trading}/...``)를 그대로 반영해, 자산군
아래에 기능(capability) 세그먼트를 둔다. 지금은 시세(``quotations``)만 노출한다. 매매
(``trading``)는 기존 주문 안전 코어를 이 아래로 옮길 때 붙는다.

사용례: ``client.domestic_stock.quotations.quote("005930")``.
"""

from __future__ import annotations

from ..transport import Transport
from .quotations.facade import Quotations


class DomesticStock:
    """국내주식 세그먼트 루트. 하나의 :class:`Transport` 를 기능 파사드들과 공유한다."""

    def __init__(self, transport: Transport) -> None:
        self.quotations = Quotations(transport)

"""주문 도메인(자산군 무관 제네릭) -- 요청(:class:`Order`), 결과(:class:`ExecutionReport`),
멱등 저장소(:class:`OrderStore`).

실행 엔진 :class:`~kis_openapi.orders.facade.Orders` 는 안전 코어의 내부 구현이다 -- 공개
진입점은 ``client.domestic_stock.trading`` 이므로 여기서 재-노출하지 않는다(주문 넣는 길을
하나로). 직접 엔진이 필요하면 ``kis_openapi.orders.facade`` 에서 가져올 수는 있다.
"""

from __future__ import annotations

from .order import Order, mint_client_order_id
from .report import TERMINAL_STATUSES, ExecutionReport, OrderStatus
from .store import OrderStore

__all__ = [
    "TERMINAL_STATUSES",
    "ExecutionReport",
    "Order",
    "OrderStatus",
    "OrderStore",
    "mint_client_order_id",
]

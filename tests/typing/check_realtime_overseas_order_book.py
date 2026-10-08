"""정적 타입 픽스처 -- `.venv/bin/mypy --strict tests/typing` 로 검사(CI 스텝과 동일).

해외주식 실시간 ``order_book(zone=...)`` 의 @overload 가 zone 리터럴로 결과 원소 타입을 정하는지
확인한다(호출부가 유니온을 좁힐 필요가 없어야 함). 런타임 실행 대상이 아니다.
"""

from typing import assert_type

from kis_trader.realtime import (
    OverseasAsiaDelayedOrderBook,
    OverseasOrderBook,
    RealtimeSubscription,
)
from kis_trader.realtime.overseas_namespace import OverseasStockHandle


def _check(handle: OverseasStockHandle) -> None:
    assert_type(handle.order_book(), RealtimeSubscription[OverseasOrderBook])
    assert_type(handle.order_book(zone="global"), RealtimeSubscription[OverseasOrderBook])
    assert_type(
        handle.order_book(zone="asia"),
        RealtimeSubscription[OverseasAsiaDelayedOrderBook],
    )

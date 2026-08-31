"""국내주식 TWAP 분할주문의 블로킹 러너 -- 스케줄의 각 슬라이스 시각까지 대기해 시장가로 발주한다.

포그라운드 블로킹: 호출 스레드가 스케줄 기간 내내 점유된다(라이브러리 사용자가 논블로킹을 원하면 순수
:mod:`.schedule` 플래너를 자기 루프/스레드에 임베드하라). 각 슬라이스는 고유 client_order_id 를 가진
일반 국내주문이라 이중체결 방지·재시도 금지·재조회는 기존 안전 코어가 그대로 보장한다. 시각 소스(``now_fn``)와
대기(``sleep_fn``)를 주입받아 테스트에서 실제 sleep 없이 결정적으로 검증한다."""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from ..errors import OrderRejectedError, OrderTimeoutError
from .schedule import TwapSchedule

if TYPE_CHECKING:
    from ..client import KISClient
    from ..report import ExecutionReport

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class SliceOutcome:
    """한 슬라이스의 실행 결과 -- 접수 리포트(성공) 또는 오류 메시지(거부/타임아웃)."""

    at: datetime
    quantity: int
    report: ExecutionReport | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TwapExecutionResult:
    """TWAP 실행 결과(불변) -- 스케줄과 슬라이스별 결과, 집계 프로퍼티."""

    schedule: TwapSchedule
    outcomes: tuple[SliceOutcome, ...]

    @property
    def submitted_quantity(self) -> int:
        """접수된 슬라이스 수량 합(거부/미시도 제외)."""
        return sum(o.quantity for o in self.outcomes if o.report is not None)

    @property
    def filled_quantity(self) -> Decimal:
        """체결 수량 합(재조회 리포트의 filled_quantity)."""
        return sum((o.report.filled_quantity for o in self.outcomes if o.report is not None),
                   Decimal(0))

    @property
    def shortfall(self) -> Decimal:
        """목표 대비 미체결 수량(총 수량 - 체결 수량)."""
        return Decimal(self.schedule.total_quantity) - self.filled_quantity

    @property
    def average_price(self) -> Decimal | None:
        """체결 수량 가중 평균 단가. 체결 0 이면 None."""
        filled = self.filled_quantity
        if filled <= 0:
            return None
        weighted = sum(
            (o.report.filled_quantity * o.report.average_price
             for o in self.outcomes
             if o.report is not None and o.report.average_price is not None),
            Decimal(0),
        )
        return weighted / filled


def run_twap(
    kis: KISClient, schedule: TwapSchedule, *,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = _time.sleep,
    reconcile: bool = True,
) -> TwapExecutionResult:
    """``schedule`` 을 블로킹으로 실행한다 -- 각 슬라이스 시각까지 ``sleep_fn`` 으로 대기한 뒤 시장가로
    발주하고, ``reconcile`` 이면 재조회로 체결을 확정해 :class:`TwapExecutionResult` 로 모은다.

    슬라이스 발주가 :class:`~kis_trader.errors.OrderRejectedError`/:class:`~kis_trader.errors.
    OrderTimeoutError` 로 실패하면 그 슬라이스만 오류로 기록하고 **다음 슬라이스를 계속**한다(부분 실행,
    최종 ``shortfall`` 로 미달 보고). 그 밖의 오류(조회전용 계좌·인증·설정 등 매 슬라이스 반복될 오류)는
    **전파**한다. ``KeyboardInterrupt`` (Ctrl-C)면 남은 슬라이스를 멈추고 여기까지의 부분 결과를 반환한다
    (이미 낸 시장가 주문은 되돌리지 않는다). ``now_fn``/``sleep_fn`` 은 시각·대기 주입점(테스트 결정성)."""
    resolved_now = now_fn if now_fn is not None else (lambda: datetime.now(_KST))
    stock = kis.domestic.stock(schedule.symbol)
    place = stock.buy if schedule.side == "buy" else stock.sell
    outcomes: list[SliceOutcome] = []
    try:
        for entry in schedule.slices:
            while True:
                delay = (entry.at - resolved_now()).total_seconds()
                if delay <= 0:
                    break
                sleep_fn(delay)
            try:
                report = place(quantity=entry.quantity)
            except (OrderRejectedError, OrderTimeoutError) as error:
                outcomes.append(SliceOutcome(entry.at, entry.quantity, None, str(error)))
                continue
            if reconcile:
                confirmed = kis.orders.reconcile(report.client_order_id)
                if confirmed is not None:
                    report = confirmed
            outcomes.append(SliceOutcome(entry.at, entry.quantity, report, None))
    except KeyboardInterrupt:
        pass  # 남은 슬라이스 중단, 부분 결과 반환(이미 낸 시장가 주문은 체결됨)
    return TwapExecutionResult(schedule=schedule, outcomes=tuple(outcomes))

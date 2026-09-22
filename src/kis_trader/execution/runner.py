"""국내주식 TWAP 분할주문의 블로킹 러너 -- 스케줄의 각 슬라이스 시각까지 대기해 시장가로 발주한다.

포그라운드 블로킹: 호출 스레드가 스케줄 기간 내내 점유된다(라이브러리 사용자가 논블로킹을 원하면 순수
:mod:`.schedule` 플래너를 자기 루프/스레드에 임베드하라). 각 슬라이스는 고유 client_order_id 를 가진
일반 국내주문이라 이중체결 방지·재시도 금지·재조회는 기존 안전 코어가 그대로 보장한다. 시각 소스(``now_fn``)와
대기(``sleep_fn``)를 주입받아 테스트에서 실제 sleep 없이 결정적으로 검증한다."""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from .._internal._datetime import _KST
from ..errors import KISUsageError, OrderRejectedError, OrderTimeoutError
from .schedule import TWAPSchedule

if TYPE_CHECKING:
    from ..client import KISClient
    from ..report import ExecutionReport


def _now_kst() -> datetime:
    """기본 시각 소스 -- 현재 KST. 테스트는 ``now_fn`` 으로 결정적 시각을 주입한다."""
    return datetime.now(_KST)


@dataclass(frozen=True, slots=True)
class TWAPSliceOutcome:
    """한 슬라이스의 실행 결과. ``report``/``error`` 중 **정확히 하나**만 설정된다 -- 접수 성공이면
    ``report`` 가 차고 ``error`` 는 ``None``, 접수 거부/타임아웃이면 ``report`` 가 ``None`` 이고
    ``error`` 에 사유가 담긴다(그래서 ``report is None`` 이 곧 그 슬라이스의 실패를 뜻한다).

    ``client_order_id`` 는 **타임아웃(체결 불명)** 슬라이스에만 실린다 -- 그 주문은 살아 있을 수 있어
    ``kis.orders.reconcile(client_order_id)`` 로 사후 확인해야 하므로, 그 id 를 유실하지 않고 실어 둔다.
    거부/성공 슬라이스는 ``None``(거부는 재조회 불필요, 성공은 ``report`` 에 id 가 있다)."""

    at: datetime
    quantity: int
    report: ExecutionReport | None
    error: str | None
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class TWAPExecutionResult:
    """TWAP 실행 결과(불변) -- 스케줄과 슬라이스별 결과, 집계 프로퍼티. 집계는 각 슬라이스를 발주 직후
    재조회(reconcile)한 **스냅샷** 기준이라, 발주 후 지연 체결된 물량은 ``shortfall`` 을 일시적으로
    과대계상할 수 있다(국내 시장가는 대체로 즉시 체결)."""

    schedule: TWAPSchedule
    outcomes: tuple[TWAPSliceOutcome, ...]

    @property
    def submitted_quantity(self) -> int:
        """접수된 슬라이스 수량 합(거부/미시도 제외)."""
        return sum(o.quantity for o in self.outcomes if o.report is not None)

    @property
    def filled_quantity(self) -> Decimal:
        """체결 수량 합(재조회 스냅샷의 filled_quantity)."""
        return sum((o.report.filled_quantity for o in self.outcomes if o.report is not None),
                   Decimal(0))

    @property
    def shortfall(self) -> Decimal:
        """목표 대비 미체결 수량(총 수량 - 체결 수량, 재조회 스냅샷 기준)."""
        return Decimal(self.schedule.total_quantity) - self.filled_quantity

    @property
    def pending_reconcile_ids(self) -> tuple[str, ...]:
        """사후 재조회가 필요한 슬라이스의 ``client_order_id`` 들 -- 타임아웃(체결 불명) 슬라이스.
        이 목록이 비지 않으면 그 주문들이 살아 있을 수 있으니 ``kis order reconcile <id>`` 로 확인해야 한다."""
        return tuple(o.client_order_id for o in self.outcomes if o.client_order_id is not None)

    @property
    def average_price(self) -> Decimal | None:
        """체결 수량 가중 평균 단가. 단가가 보고된(``average_price`` 가 not None) 체결만으로 계산하며
        (분자·분모 동일 모집단), 그런 체결이 없으면 None. 국내 체결 리포트는 filled>0 이라도 단가가
        None 일 수 있어(와이어 평균가 공백), 그 물량은 가중평균에서 함께 제외한다."""
        priced = [
            (o.report.filled_quantity, o.report.average_price)
            for o in self.outcomes
            if o.report is not None and o.report.average_price is not None
        ]
        quantity = sum((qty for qty, _ in priced), Decimal(0))
        if quantity <= 0:
            return None
        weighted = sum((qty * price for qty, price in priced), Decimal(0))
        return weighted / quantity


def execute_twap(
    kis: KISClient, schedule: TWAPSchedule, *,
    now_fn: Callable[[], datetime] = _now_kst,
    sleep_fn: Callable[[float], None] = _time.sleep,
    reconcile: bool = True,
) -> TWAPExecutionResult:
    """``schedule`` 을 블로킹으로 실행한다 -- 각 슬라이스 시각까지 ``sleep_fn`` 으로 대기한 뒤 시장가로
    발주하고, ``reconcile`` 이면 재조회로 체결을 확정해 :class:`TWAPExecutionResult` 로 모은다.

    슬라이스 발주가 :class:`~kis_trader.errors.OrderRejectedError`/:class:`~kis_trader.errors.
    OrderTimeoutError` 로 실패하면 그 슬라이스만 오류로 기록하고 **다음 슬라이스를 계속**한다(부분 실행,
    최종 ``shortfall`` 로 미달 보고). ``KeyboardInterrupt`` (Ctrl-C)면 남은 슬라이스를 멈추고 여기까지의
    부분 결과를 반환한다(이미 낸 시장가 주문은 되돌리지 않는다). 그 밖의 오류(조회전용 계좌·인증·설정
    오류나 KIS 스로틀 등 매 슬라이스 반복될 오류)는 **전파**한다 -- 단, 이미 발주된 슬라이스는 안전 코어가
    로컬 저장소에 기록해 두므로(``kis.orders.reconcile`` 로 사후 확인 가능) 유실되지 않으며, 몇 개까지
    발주됐는지를 예외 노트로 덧붙인다. ``now_fn``/``sleep_fn`` 은 시각·대기 주입점(테스트 결정성)이며,
    ``sleep_fn`` 은 ``now_fn`` 을 전진시켜야 한다(멈춘 시계면 무한 대기를 막고자 오류로 올린다)."""
    stock = kis.domestic.stock(schedule.symbol)
    place = stock.buy if schedule.side == "buy" else stock.sell
    outcomes: list[TWAPSliceOutcome] = []
    try:
        for entry in schedule.slices:
            while True:
                current = now_fn()
                delay = (entry.at - current).total_seconds()
                if delay <= 0:
                    break
                sleep_fn(delay)
                if now_fn() <= current:  # 시계가 전진하지 않음 -> 무한 대기 방지(주입점 오용)
                    raise KISUsageError(
                        "sleep_fn 이 now_fn 을 전진시키지 않았다 -- 시각/대기 주입이 일관돼야 한다."
                    )
            try:
                report = place(quantity=entry.quantity)
            except OrderTimeoutError as error:
                # 타임아웃 슬라이스는 살아 있을 수 있다 -- client_order_id 를 실어 사후 reconcile 가능하게.
                outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, None, str(error),
                                                 client_order_id=error.client_order_id))
                continue
            except OrderRejectedError as error:
                outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, None, str(error)))
                continue
            if reconcile:
                confirmed = kis.orders.reconcile(report.client_order_id)
                if confirmed is not None:
                    report = confirmed
            outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, report, None))
    except KeyboardInterrupt:
        pass  # 남은 슬라이스 중단, 부분 결과 반환(이미 낸 시장가 주문은 체결됨)
    except Exception as error:
        # 조회전용/인증/스로틀 등 전파 오류: 이미 발주된 슬라이스가 유실로 보이지 않게 몇 개까지
        # 접수됐는지 노트를 남기고 재전파한다(발주분은 저장소에 기록돼 reconcile 로 사후 확인 가능).
        submitted = sum(1 for o in outcomes if o.report is not None)
        if submitted:
            error.add_note(f"TWAP 부분 실행됨: {submitted}/{len(schedule.slices)} 슬라이스 접수 후 중단.")
        raise
    return TWAPExecutionResult(schedule=schedule, outcomes=tuple(outcomes))

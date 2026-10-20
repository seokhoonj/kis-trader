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
from typing import TYPE_CHECKING, Literal

from .._internal._datetime import _KST
from ..errors import KISError, KISUsageError, OrderRejectedError, OrderTimeoutError
from ..order import mint_client_order_id
from ..report import TERMINAL_ORDER_STATUSES
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

    ``client_order_id`` 는 **결과 미확정** 슬라이스에만 실린다 -- 타임아웃(체결 불명)이거나, 접수는
    됐지만 발주 직후 재조회(reconcile)가 실패해 체결을 확정 못 한 경우다. 그 주문은 살아 있을 수 있어
    요청 ID를 유실하지 않고 실어 둔다. 저장된 유효한 증권사 주문번호가 있으면
    ``kis.orders.reconcile(client_order_id, refresh=True)`` 로 갱신할 수 있지만, 최초 타임아웃으로
    번호가 없으면 불명으로 유지하므로 재전송하지 말고 HTS/MTS나 계좌 주문·체결 조회로 확인한다.
    ``client_order_id`` 가 실리는(= pending, shortfall 에서 제외) 경우: (1) 발주 타임아웃, (2) 재조회
    읽기 실패, (3) 재조회가 모호(0건 -> ``None``), (4) 재조회가 **비종료** 리포트(NEW/부분체결 등 아직
    살아있음), (5) ``reconcile=False``, (6) 발주 호출 중 ``KeyboardInterrupt`` 로 접수 여부가 불명(살아
    있을 수 있어 재전송 금지·저장 상태 및 수동 확인 대상). 종료상태(체결/취소/거부/만료)로 확정된 슬라이스와
    거부 슬라이스는 ``None``(거부는 재조회 불필요, 확정은 ``report`` 로 집계)."""

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
    interrupted: bool = False

    @property
    def outcome(self) -> Literal["completed", "incomplete", "unknown", "interrupted"]:
        """실행 분류. 중단을 우선하며 미확정 주문은 pending 목록으로 별도 보존한다.

        미확정 물량은 shortfall 에서 제외되므로 shortfall 이 0 이어도 완료가 아니다.
        모든 슬라이스가 오류 없이 종료 확인되고 목표 수량이 체결돼야 완료다.
        """
        if self.interrupted:
            return "interrupted"
        if self.pending_reconcile_ids:
            return "unknown"
        if len(self.outcomes) != len(self.schedule.slices) or any(
            outcome.error is not None or outcome.report is None
            or outcome.report.status not in TERMINAL_ORDER_STATUSES
            for outcome in self.outcomes
        ) or self.shortfall != 0:
            return "incomplete"
        return "completed"

    @property
    def submitted_quantity(self) -> int:
        """접수된 슬라이스 수량 합(거부/미시도 제외)."""
        return sum(o.quantity for o in self.outcomes if o.report is not None)

    @property
    def filled_quantity(self) -> Decimal:
        """**확정된** 슬라이스의 체결 수량 합. 사후 재조회가 필요한(pending) 슬라이스는 체결 여부가
        불명이라(발주 직후 스냅샷의 filled 은 보통 0) 여기 넣지 않는다 -- 넣으면 미확정 물량을 '0 체결'
        로 단정해 shortfall 을 부풀린다. pending 물량은 :attr:`pending_quantity` 로 따로 센다."""
        return sum((o.report.filled_quantity for o in self.outcomes
                    if o.report is not None and o.client_order_id is None),
                   Decimal(0))

    @property
    def pending_quantity(self) -> Decimal:
        """사후 재조회가 필요한(체결 여부 불명) 슬라이스의 발주 수량 합 -- :attr:`pending_reconcile_ids`
        와 같은 모집단. shortfall 에서 제외된다. 알려진 증권사 주문번호는 상태를 갱신하고,
        번호 없는 불명 요청은 재전송하지 말고 HTS/MTS나 계좌 주문·체결 조회로 확인한다."""
        return sum((Decimal(o.quantity) for o in self.outcomes if o.client_order_id is not None),
                   Decimal(0))

    @property
    def shortfall(self) -> Decimal:
        """목표 대비 미체결 수량 -- 총 수량에서 확정 체결과 **pending(불명) 물량을 함께 뺀다**.
        pending 을 빼므로 이 값을 그대로 재주문해도 아직 살아 있을 수 있는 pending 슬라이스를
        이중매수하지 않는다. pending 이 있으면(:attr:`pending_reconcile_ids` 비지 않음) 그 물량은
        재주문하지 말고 저장 상태를 확인하라. 알려진 증권사 주문번호는 재조회할 수 있지만,
        번호 없는 불명 요청은 HTS/MTS나 계좌 주문·체결 조회로 수동 확인한다."""
        return Decimal(self.schedule.total_quantity) - self.filled_quantity - self.pending_quantity

    @property
    def pending_reconcile_ids(self) -> tuple[str, ...]:
        """사후 재조회가 필요한 슬라이스의 ``client_order_id`` 들 -- 타임아웃(체결 불명)이거나 발주 직후
        재조회가 실패해 미확정인 슬라이스. 이 목록이 비지 않으면 그 주문들이 살아 있을 수 있으니
        ``kis order reconcile <id>`` 로 저장 상태를 확인한다. 번호 없는 불명 요청은
        재전송하지 말고 HTS/MTS나 계좌 주문·체결 조회로 수동 확인한다."""
        return tuple(o.client_order_id for o in self.outcomes if o.client_order_id is not None)

    @property
    def average_price(self) -> Decimal | None:
        """체결 수량 가중 평균 단가. 단가가 보고된(``average_price`` 가 not None) 체결만으로 계산하며
        (분자·분모 동일 모집단), 그런 체결이 없으면 None. 국내 체결 리포트는 filled>0 이라도 단가가
        None 일 수 있어(와이어 평균가 공백), 그 물량은 가중평균에서 함께 제외한다."""
        priced = [
            (o.report.filled_quantity, o.report.average_price)
            for o in self.outcomes
            # filled_quantity 와 같은 모집단(확정 체결 = client_order_id 없는 비-pending 슬라이스)만 쓴다
            # -- pending(비종결) 슬라이스의 부분체결 단가가 섞이면 두 집계가 다른 체결 집합을 말하게 된다.
            if o.report is not None and o.client_order_id is None and o.report.average_price is not None
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
    발주하고, ``reconcile`` 이면 알려진 주문번호로 현재 상태를 조회해 :class:`TWAPExecutionResult` 로 모은다.

    슬라이스 발주가 :class:`~kis_trader.errors.OrderRejectedError`/:class:`~kis_trader.errors.
    OrderTimeoutError` 로 실패하면 그 슬라이스만 오류로 기록하고 **다음 슬라이스를 계속**한다(부분 실행,
    최종 ``shortfall`` 로 미달 보고). ``KeyboardInterrupt`` (Ctrl-C)면 남은 슬라이스를 멈추고 여기까지의
    부분 결과를 반환한다(이미 낸 시장가 주문은 되돌리지 않는다). 그 밖의 오류(조회전용 계좌·인증·설정
    오류나 KIS 스로틀 등 매 슬라이스 반복될 오류)는 **전파**한다 -- 단, 이미 발주된 슬라이스는 안전 코어가
    로컬 저장소에 기록해 둔다. 알려진 주문번호는 재조회하고 번호 없는 불명 요청은 수동 확인한다. 몇 개까지
    발주됐는지를 예외 노트로 덧붙인다. ``now_fn``/``sleep_fn`` 은 시각·대기 주입점(테스트 결정성)이며,
    ``sleep_fn`` 은 ``now_fn`` 을 전진시켜야 한다(멈춘 시계면 무한 대기를 막고자 오류로 올린다)."""
    stock = kis.domestic.stock(schedule.symbol)
    if schedule.side == "buy":
        place = stock.buy
    elif schedule.side == "sell":
        place = stock.sell
    else:
        raise KISUsageError(f"side 는 buy/sell 여야 한다: {schedule.side!r}")
    outcomes: list[TWAPSliceOutcome] = []
    in_flight: TWAPSliceOutcome | None = None
    interrupted = False
    try:
        for entry in schedule.slices:
            outcomes_before_slice = len(outcomes)   # 이 슬라이스가 아직 결과를 안 실었는지 판별용
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
            slice_id = mint_client_order_id()
            pending_interrupt = TWAPSliceOutcome(
                entry.at, entry.quantity, None,
                "실행 중단(KeyboardInterrupt) -- 이 슬라이스는 접수됐을 수 있다.",
                client_order_id=slice_id,
            )
            in_flight = pending_interrupt
            try:
                report = place(quantity=entry.quantity, client_order_id=slice_id)
            except OrderTimeoutError as error:
                # 타임아웃 슬라이스는 살아 있을 수 있다 -- 저장 상태 및 수동 확인에 쓸 요청 ID를 보존.
                outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, None, str(error),
                                                 client_order_id=error.client_order_id))
                in_flight = None
                continue
            except OrderRejectedError as error:
                outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, None, str(error)))
                in_flight = None
                continue
            pending_reconcile_id: str | None = None
            if reconcile:
                try:
                    # refresh=True: 접수 직후 저장된 NEW/체결 0 스냅샷을 단락 반환하지 않고 브로커
                    # 일별체결을 재스캔해 **실제** 체결을 받는다. 안 그러면 체결을 0 으로 오인해
                    # shortfall 이 전량이 되고, 사용자가 그걸 재주문하면 이중매수가 난다.
                    confirmed = kis.orders.reconcile(report.client_order_id, refresh=True)
                except KISError:
                    # 재조회(읽기) 실패(타임아웃/스로틀 등) -> 체결 불명.
                    confirmed = None
                if confirmed is not None:
                    report = confirmed      # 표시는 최신 브로커 스냅샷으로(참고용)
                # 종료 상태(FILLED/CANCELED/REJECTED)만 '확정'으로 집계한다 -- 그 경우에만 미체결 잔량이
                # 주문이 죽어 진짜 shortfall 이다. 모호(None)거나 비종료(NEW/부분체결/pending)면 주문이
                # 아직 살아 있어 체결 불명이므로 pending 으로 돌린다 -- 확정 0/부분으로 집계하면 아직
                # 체결될 잔량이 shortfall 이 돼 사용자가 재주문 시 이중매수가 난다.
                if confirmed is None or confirmed.status not in TERMINAL_ORDER_STATUSES:
                    pending_reconcile_id = report.client_order_id
            else:
                # reconcile 비활성: 접수 직후 스냅샷(NEW/체결 0)은 체결 불명이므로 pending 으로 돌린다
                # (체결 0 으로 단정해 shortfall 을 부풀리지 않는다 -- 위와 같은 이중매수 방지).
                pending_reconcile_id = report.client_order_id
            outcomes.append(TWAPSliceOutcome(entry.at, entry.quantity, report, None, client_order_id=pending_reconcile_id))
            in_flight = None
    except KeyboardInterrupt:
        interrupted = True
        # 발주 호출 중 중단이면 그 슬라이스는 접수됐을 수 있어 pending 으로 실어 둔다. 단 결과가 이미
        # 커밋됐으면(append~in_flight=None 사이의 좁은 창에서 인터럽트) 같은 슬라이스를 두 번 세지 않게
        # outcomes 길이로 확인한다(중복 집계 시 pending_quantity 과대 -> shortfall 과소 -> 미달주문).
        if in_flight is not None and len(outcomes) == outcomes_before_slice:
            outcomes.append(in_flight)
    except Exception as error:
        # 조회전용/인증/스로틀 등 전파 오류: 이미 발주된 슬라이스가 유실로 보이지 않게 몇 개까지
        # 접수됐는지 노트를 남기고 재전파한다(발주분의 저장 상태와 계좌 주문·체결 조회로 확인).
        submitted = sum(1 for o in outcomes if o.report is not None)
        if submitted:
            error.add_note(f"TWAP 부분 실행됨: {submitted}/{len(schedule.slices)} 슬라이스 접수 후 중단.")
        raise
    return TWAPExecutionResult(schedule=schedule, outcomes=tuple(outcomes), interrupted=interrupted)

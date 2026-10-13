"""지역 중립 주문 안전 코어 (내부) -- place / submit_change.

이중체결 구조적 불가·쓰기 재시도 금지·보수적 재조회의 안전 불변식은 시장과 무관하다. 그 코어를
자산군 패키지(domestic/overseas)에서 분리해 여기 둔다 -- 각 시장 엔진이 자기 와이어 빌더
(:class:`PlaceRequestBuilder`/:class:`ChangeRequestBuilder`)를 **주입**하고, 사전 리스크 점검도
콜러블로 주입한다(이 코어는 국내/해외 어느 빌더에도 의존하지 않는다). ``build_request`` 는 필수다 --
기본 국내 빌더를 두면 이 코어가 domestic 패키지에 안쪽 의존하게 되므로.

:func:`place` 흐름(안전 6단계):
1. 계좌 주문가능 여부·수량 정수·사전 리스크·와이어 변환을 **먼저** 확인(불가/미구현이면 claim 전에 중단).
2. :meth:`OrderStore.try_claim` 로 원자적 판정/확보: 완료면 리포트 replay, 같은 id 다른 지문은
   충돌 거부, in-flight 면 재조회 요구, 새로 확보되면 전송.
3. 전송. 타임아웃이면 재시도 없이 in-flight 유지 후 :class:`OrderTimeoutError`.
4. 접수 거부(rt_cd!=0)면 in-flight 해제 후 :class:`OrderRejectedError`(체결 아님).
5. 접수됐으나 ODNO 없으면 재조회 불가 -> in-flight 유지하고 raise.
6. 성공이면 리포트(NEW)를 기록하고 반환.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from ._internal._datetime import _KST
from ._internal._wire import format_wire_decimal
from .errors import (
    AccountNotOrderableError,
    KISAuthError,
    KISError,
    KISUsageError,
    OrderError,
    OrderNotAcceptedYetError,
    OrderRejectedError,
    OrderTimeoutError,
)
from .order import (
    ChangeAction,
    ChangeActionFingerprint,
    ImmediateOrderFingerprint,
    Order,
    WireRequest,
)
from .report import ExecutionReport, OrderStatus
from .store import Binding, Claimed, Completed, Conflict, InFlight, OrderStore, PriorChangeInFlight
from .transport import Environment, Transport, TransportTimeoutError

#: 시장 중립 접수-일자 키. 시장별 extract_output 파서가 접수 응답의 자기 일자 필드(예: 해외선물옵션의
#: ORD_DT)를 이 키로 정규화해 넣으면, :func:`place` 가 필드명을 모른 채 ``receipt_date`` 로 영속한다.
#: 이 키를 안 채우는 자산(국내주식/파생/채권 등)은 ``receipt_date`` 가 None 으로 남아 동작 불변이다.
_RECEIPT_DATE_KEY = "_receipt_date"


class PlaceRequestBuilder(Protocol):
    """발주 와이어 빌더의 정확한 호출 계약 -- 안전 코어(:func:`place`)는 시장 중립이라 이 형태의
    빌더를 주입받아 ``(order, cano, product_code, environment)`` 로 호출한다. 국내 현금·신용, 해외
    정규·주간 빌더가 이를 만족한다. 세 문자열이 자리로만 구분되던 것을 이름 있는 계약으로 고정한다."""

    def __call__(
        self, order: Order, *, cano: str, product_code: str, environment: Environment
    ) -> WireRequest: ...


class ChangeRequestBuilder(Protocol):
    """정정·취소 와이어 빌더의 정확한 호출 계약 -- 안전 코어(:func:`submit_change`)가 주입받아 호출한다.
    전 인자 키워드 전용이며 원주문 리포트·지문 + 변경 의도(action/수량/가격)로 조립한다. 국내·해외
    정규/주간 정정취소 빌더가 이를 만족한다."""

    def __call__(
        self, *, original_report: ExecutionReport, original_fingerprint: ImmediateOrderFingerprint,
        action: ChangeAction, quantity: Decimal, limit_price: Decimal | None,
        cano: str, product_code: str, environment: Environment,
    ) -> WireRequest: ...


def _extract_output_mapping(body: Mapping[str, Any]) -> Mapping[str, Any]:
    out = body.get("output", body)
    return out if isinstance(out, Mapping) else {}


def _extract_organization_number(output: Mapping[str, Any]) -> str | None:
    """주문 응답/체결행에서 한국거래소전송주문조직번호를 뽑는다(없으면 None).

    접수/정정 응답은 ``KRX_FWDG_ORD_ORGNO``, 일별체결 행은 ``ord_gno_brno`` 키를 쓴다. 그 키가
    없는 자산(해외 등)은 None 이 되어 동작 불변."""
    org = str(output.get("KRX_FWDG_ORD_ORGNO") or output.get("ord_gno_brno") or "").strip()
    return org or None


def _conflict_message(client_order_id: str) -> str:
    return (
        f"client_order_id {client_order_id!r} 는 이미 다른 주문에 사용됐다. 새 id를 발행하라."
    )


def _in_flight_message(client_order_id: str) -> str:
    return (
        f"주문 {client_order_id} 은 전송됐으나 결과가 확인되지 않았다. "
        f"kis.orders.reconcile({client_order_id!r}) 로 재조회한 뒤 판단하라."
    )


def place(
    transport: Transport, store: OrderStore, order: Order, *,
    cano: str, product_code: str, environment: Environment, orderable: bool = True,
    build_request: PlaceRequestBuilder,
    extract_output: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    pre_trade_risk: Callable[[Order], None] | None = None,
) -> ExecutionReport:
    """주문을 안전 규칙(모듈 docstring 6단계)에 따라 전송한다.

    ``build_request`` 는 와이어 요청을 조립하는 시장별 빌더(필수) -- 이중체결 방지·재시도 금지·재조회
    등 안전 코어는 시장과 무관하게 공유한다. ``extract_output`` 은 접수 응답에서 ODNO 를 담은 output
    매핑을 뽑는 파서(기본 :func:`_extract_output_mapping`; 파생은 top-level 폴백을 금지하는 엄격 파서를
    주입). ``pre_trade_risk`` 를 주면 와이어 전에 사전 리스크 한도를 점검하는 콜러블을 호출한다(시장별
    참조가 조회는 그 콜러블이 담당). 계좌 가드/수량 정수/리스크 게이트는 모두 :meth:`OrderStore.try_claim`
    **전**에 돈다 -- 거부되면 ``client_order_id`` 를 소비하지도 와이어에 닿지도 않는다.
    """
    build = build_request
    extract = extract_output if extract_output is not None else _extract_output_mapping
    client_order_id = order.client_order_id
    fingerprint = order.fingerprint

    # 멱등 replay 빠른길: 이미 완료/전송된 id 는 사전 리스크(라이브 시세, fail-closed)·와이어 변환
    # 같은 실패 가능 게이트를 타기 전에 저장소에서 바로 판별한다 -- 일시적 조회 실패(스로틀/장마감)가
    # 저장된 완료 리포트의 replay 를 막아 호출자를 새 id 재제출(이중발주)로 밀어내지 않도록 한다.
    # classify 는 한 락으로 try_claim 과 동일 분류를 주되 쓰기는 안 한다(찢긴 상태 안 봄). fresh
    # 주문의 경합 권한은 그대로 try_claim(원자적 check-and-claim)이 가진다.
    peek = store.classify(client_order_id, fingerprint)
    if isinstance(peek, Completed):
        return peek.report             # 완료 replay -- 같은 주문(리포트는 변형 보장, 재확인 불필요)
    if isinstance(peek, Conflict):
        raise KISUsageError(_conflict_message(client_order_id))
    if isinstance(peek, InFlight):
        raise KISUsageError(_in_flight_message(client_order_id))

    if not orderable:
        raise AccountNotOrderableError(
            "이 계좌는 API 주문이 불가하다(퇴직연금 IRP/DC 등 조회전용). 일반/연금저축 계좌를 쓰라."
        )
    # 주식 주문은 주(株) 단위 정수 수량만 -- 소수 수량은 fat-finger(예: 10.5). 와이어 전에 막는다.
    if order.quantity != order.quantity.to_integral_value():
        raise KISUsageError(f"주식 주문 수량은 정수여야 한다(주 단위): {order.quantity}")
    # 사전 리스크 한도(opt-in). 참조가가 필요하면 현재가를 조회한다 -- 조회 실패는 fail-closed
    # (한도 확인 불가 -> 주문 중단; 예외가 그대로 올라가 claim 전에 멈춘다).
    if pre_trade_risk is not None:
        pre_trade_risk(order)
    # 와이어 변환을 먼저 -- 미구현/부적합이면 claim 전에 중단(stuck in-flight 방지).
    method, path, tr_id, body = build(order, cano=cano, product_code=product_code, environment=environment)

    claim = store.try_claim(client_order_id, fingerprint)
    if isinstance(claim, Completed):
        return claim.report        # 완료 replay -- 리포트는 변형이 보장(재확인 불필요)
    if isinstance(claim, Conflict):
        raise KISUsageError(_conflict_message(client_order_id))
    if isinstance(claim, InFlight):
        raise KISUsageError(_in_flight_message(client_order_id))
    if not isinstance(claim, Claimed):  # 전송은 좁게 가드 -- Claimed 만 와이어에 닿는다
        raise OrderError(f"예상치 못한 claim 결과: {claim!r}")

    try:
        resp = transport.request(
            method=method, path=path, tr_id=tr_id, body=body, idempotent=False
        )
    except TransportTimeoutError as err:
        # 재시도 금지: 체결 여부 불명. in-flight 유지, 재조회를 요구한다.
        raise OrderTimeoutError(
            f"주문 전송 시간초과 -- 체결 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회하라.",
            client_order_id=client_order_id,
        ) from err
    except KISAuthError:
        # 401(인증 실패)은 게이트웨이 인증 단계에서 거부된 것 -- 주문 엔진에 닿지 못했으므로
        # '접수 안 됨'이 확실하다(이중주문 위험 없음). in-flight 를 해제하고 인증 오류를 그대로
        # 올려, 호출자가 토큰을 고쳐 바로 재주문하게 한다(reconcile 불필요). 429/500(스로틀)은
        # 접수 여부가 불명이라 아래 in-flight 유지 분기로 둔다.
        store.clear_in_flight(client_order_id)
        raise
    except KISError as err:
        # 401(KISAuthError) 외의 모든 KISError: 비-200 전송계층 오류(KIS 는 app-key 합산 스로틀을
        # HTTP 500 으로 낸다; 429/기타 5xx), 전송 전 확실한 요청구성 오류, 비-JSON 바디까지 전부
        # 여기로 흘러든다. 접수 여부가 불명인 경우가 섞여 있으므로 보수적으로 전부 in-flight 를 유지하고
        # 재조회를 요구한다 -- 조용히 재전송하면 (스로틀로 이미 접수됐을 때) 이중 주문이 날 수 있다.
        raise OrderTimeoutError(
            f"주문 전송 중 전송계층 오류 -- 접수 여부가 불명이다. 재전송하지 말고 "
            f"kis.orders.reconcile({client_order_id!r}) 로 재조회하라.",
            client_order_id=client_order_id,
        ) from err

    if not resp.ok:
        # 접수 거부 -- 주문이 들어가지 않았다. in-flight 해제, 명확히 raise.
        store.clear_in_flight(client_order_id)
        raise OrderRejectedError(
            f"주문 접수 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )

    output = extract(resp.body)
    order_id = output.get("ODNO")
    if not order_id:
        # 접수(rt_cd=0)인데 거래소 주문번호 없음 -> 재조회 불가. in-flight 유지하고 raise.
        raise OrderError(
            "접수 응답(rt_cd=0)에 거래소 주문번호(ODNO)가 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )

    # 시장 중립 접수-일자: 시장별 파서가 정규화 키(_RECEIPT_DATE_KEY)로 채운 값만 영속한다 -- place 는
    # 자산별 응답 필드명(예: 해외선물옵션 ORD_DT)을 모른다. 해외선물옵션은 이후 정정·취소가 원주문일자
    # (ORGN_ORD_DT)로 대상을 특정하므로 접수 시점에 잡아 둔다. 이 키를 안 채우는 자산(국내주식/파생/
    # 채권)은 None 이 되어(receipt_date 기본값과 동일) 기존 동작은 불변이다.
    receipt = output.get(_RECEIPT_DATE_KEY)
    report = ExecutionReport(
        client_order_id=client_order_id,
        order_id=str(order_id),
        symbol=order.symbol,
        side=order.side,
        status=OrderStatus.NEW,          # 접수됨 -- 체결은 이후 리포트/재조회로 확인
        filled_quantity=Decimal(0),
        average_price=None,
        recorded_at=datetime.now(_KST),
        organization_number=_extract_organization_number(output),
        receipt_date=str(receipt) if receipt else None,
        _raw=resp.body,
    )
    store.record(report, fingerprint)
    return report


def submit_change(
    transport: Transport,
    store: OrderStore,
    *,
    original_client_order_id: str,
    request_id: str,
    action: ChangeAction,
    quantity: Decimal,
    limit_price: Decimal | None,
    cano: str,
    product_code: str,
    environment: Environment,
    build_request: ChangeRequestBuilder,
) -> ExecutionReport:
    """접수된 주문을 정정하거나 취소한다. 변경 요청도 별도 멱등키(``request_id``)로 중복 전송을 막는다.

    정정(``action="modify"``)이 성공하면 KIS 가 원주문에 **새 ODNO** 를 부여하므로, 원
    ``original_client_order_id`` 의 표준 리포트와 지문을 그 정정된 주문(새 ODNO/조직번호, 정정
    수량/가격, ``PENDING_REPLACE``)으로 **재바인딩**한다 -- 이후 같은 id 의 cancel/modify 가
    정정된 주문을 지목하게 하려는 것이다. 정정 접수(rt_cd=0)인데 새 ODNO 가 없으면 재조회
    불가라 :class:`OrderError` 로 fail-closed(낡은 ODNO 에 재바인딩하지 않는다).

    재바인딩은 응답의 ``ODNO`` 를 취소 대상으로 삼으므로, 정정이 제자리(구 ODNO 유효)든 새
    ODNO 든 그 응답값이 올바른 대상이 된다. 국내는 새 ODNO 부여를 실서버로 실증했다. 해외도
    같은 안전 코어를 공유하며(``build_request`` 만 다름), KIS 명세('해외주식 정정취소주문' output)으로
    검증됨 -- 응답 output 에 ``ODNO``("채번된 주문번호" = 정정 시 새 주문번호)와 ``KRX_FWDG_ORD_ORGNO``
    가 있어 국내와 동일 구조이며, 재바인딩 대상(새 ODNO)이 정확하다.

    원주문에 결과 미확인(in-flight) 변경이 이미 있으면 새(다른 ``request_id``) 변경은 :class:`KISUsageError`
    로 거부한다(같은 원주문을 겨눈 두 변경의 경합 방지) -- 같은 ``request_id`` 재전송은 예외로,
    아래 멱등 in-flight 경로가 처리한다.

    Raises: 미확정/종료/수량·가격 위반·경합 변경은 :class:`KISUsageError`, 접수 거부(rt_cd!=0)는
    :class:`OrderRejectedError` -- 그중 '거래소 미접수'(원주문이 아직 거래소에 접수 전이라 정정취소가
    이른 경우)는 재시도 가능한 하위 타입 :class:`OrderNotAcceptedYetError`, 전송 타임아웃(처리 불명)은
    :class:`OrderTimeoutError`, 정정 접수인데 ODNO 없음/예상외 outcome 은 :class:`OrderError`."""
    # (리포트, 지문)을 한 락으로 원자 스냅샷 -- 아래 빌더가 둘을 함께 읽어 잔량/와이어를 만든다.
    # 따로 읽으면 그 사이 동시 정정/취소의 원자 리바인드가 끼어들어 세대가 어긋난 쌍(옛 지문 + 새
    # 리포트)을 볼 수 있고, 그러면 잘못된 잔량이 와이어로 나간다.
    binding = store.binding_for(original_client_order_id)
    if binding is None:
        raise KISUsageError(f"확정된 원주문을 찾을 수 없다: {original_client_order_id!r}")
    original_report, original_fingerprint = binding
    if not isinstance(original_fingerprint, ImmediateOrderFingerprint):
        # 정정·취소(order-rvsecncl)는 즉시체결 주문만 -- 예약주문은 예약 전용 정정취소(순번 지목)를 쓴다.
        raise KISUsageError(
            "이 주문은 즉시체결 정정·취소를 지원하지 않는다(예약주문은 예약 정정·취소를 쓰라)."
        )
    if not original_report.order_id:
        raise KISUsageError("원주문에 거래소 주문번호가 없어 정정·취소할 수 없다.")
    if original_report.is_terminal:
        raise KISUsageError(
            f"종료 상태 주문은 정정·취소할 수 없다: {original_report.status.value}"
        )
    if action not in {"cancel", "modify"}:
        raise KISUsageError(f"지원하지 않는 주문 변경: {action!r}")
    if quantity <= 0 or quantity != quantity.to_integral_value():
        raise KISUsageError(f"정정·취소 수량은 양의 정수여야 한다: {quantity}")
    # 가격형상 검증(정정=양의 limit_price, 취소=가격 없음)은 와이어 규칙이 자산마다 달라 각 자산
    # 빌더가 수행한다. "원주문에 다른 미확인 변경이 있는가" 검사와 request_id 확보는 아래
    # store.try_claim_change 가 **한 락** 안에서 원자적으로 처리한다(과거엔 in_flight_change_for 후
    # try_claim 으로 쪼개져, 같은 원주문 두 변경이 둘 다 검사를 통과해 둘 다 전송되는 TOCTOU 경합이 났다).
    action_fingerprint = ChangeActionFingerprint(
        # 원 client_order_id 로만 식별한다(그 order_id 로 파생하지 않는다) -- 정정은 원 id 를
        # 새 ODNO 로 재바인딩하므로, order_id 를 지문에 넣으면 같은 request_id 재시도 시 재계산
        # 값이 달라져 replay 가 CONFLICT 로 깨진다. request_id 가 1차 멱등키, action/수량/가격이
        # 변경 의도를 식별하며, exchange 는 원주문 거래소(온-디스크엔 "action:" 접두로 네임스페이스 분리).
        original_client_order_id=original_client_order_id,
        side=original_fingerprint.side,
        order_type=original_fingerprint.order_type,
        quantity=format_wire_decimal(quantity),
        limit_price="" if limit_price is None else format_wire_decimal(limit_price),
        action=action,
        time_in_force=original_fingerprint.time_in_force,
        region=original_fingerprint.region,
        asset_class=original_fingerprint.asset_class,
        exchange=original_fingerprint.exchange,
    )
    builder = build_request
    request = builder(
        original_report=original_report,
        original_fingerprint=original_fingerprint,
        action=action,
        quantity=quantity,
        limit_price=limit_price,
        cano=cano,
        product_code=product_code,
        environment=environment,
    )
    claim = store.try_claim_change(
        request_id, action_fingerprint, original_client_order_id=original_client_order_id
    )
    if isinstance(claim, PriorChangeInFlight):
        raise KISUsageError(
            f"원주문 {original_client_order_id!r} 에 결과 미확인 변경 {claim.other_request_id!r} 이 있다 -- "
            f"kis.orders.reconcile({claim.other_request_id!r}) 로 해소한 뒤 재시도하라."
        )
    if isinstance(claim, Completed):
        return claim.report
    if isinstance(claim, Conflict):
        raise KISUsageError(f"request_id {request_id!r}는 이미 다른 요청에 사용됐다.")
    if isinstance(claim, InFlight):
        raise KISUsageError(
            f"변경 요청 {request_id}의 결과가 아직 확인되지 않았다. 재전송하지 말라."
        )
    if not isinstance(claim, Claimed):
        raise OrderError(f"예상치 못한 claim 결과: {claim!r}")
    try:
        resp = transport.request(
            method=request.method,
            path=request.path,
            tr_id=request.tr_id,
            body=request.body,
            idempotent=False,
        )
    except TransportTimeoutError as err:
        raise OrderTimeoutError(
            f"주문 {action} 요청 시간초과 -- 처리 여부가 불명이다. 재전송하지 말라.",
            client_order_id=request_id,
        ) from err
    except KISAuthError:
        # 401(인증 실패)은 인증 단계 거부 -- 변경 요청이 처리되지 않았음이 확실하다. in-flight 를
        # 해제하고 인증 오류를 그대로 올린다(토큰 교정 후 바로 재시도 가능). 429/500 은 처리 여부가
        # 불명이라 아래 in-flight 유지 분기로 둔다.
        store.clear_in_flight(request_id)
        raise
    except KISError as err:
        # 401(KISAuthError) 외의 모든 KISError: 비-200(HTTP 500 스로틀·429·기타 5xx), 전송 전 확실한
        # 요청구성 오류, 비-JSON 바디까지 전부 여기로 온다. 처리 여부 불명이 섞여 있어 보수적으로
        # in-flight 유지 + 재조회 요구(조용한 재전송 = 이중 변경 위험).
        raise OrderTimeoutError(
            f"주문 {action} 요청 중 전송계층 오류 -- 처리 여부가 불명이다. 재전송하지 말라.",
            client_order_id=request_id,
        ) from err
    if not resp.ok:
        store.clear_in_flight(request_id)
        # '거래소 미접수' 거부는 재시도 가능 -- 브로커는 ODNO 를 냈으나 거래소가 아직 원주문을
        # 접수하지 않아 정정취소가 너무 이르다. 실거부와 구별되게 하위 타입으로 올려 호출자가 잠시
        # 뒤 재시도하게 한다(발주 아닌 정정·취소 경로에서만 매핑). msg_cd 정본이 확인되면 msg1
        # 부분일치 대신 그 코드로 판별하는 게 더 견고하다.
        rejection = (
            OrderNotAcceptedYetError if "미접수" in (resp.msg1 or "") else OrderRejectedError
        )
        raise rejection(
            f"주문 {action} 요청 거부: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    output = _extract_output_mapping(resp.body)
    new_order_id = output.get("ODNO")
    if action == "modify" and not new_order_id:
        # 정정 접수(rt_cd=0)인데 새 ODNO 가 없다 -> 재조회 불가. place 와 같이 fail-closed:
        # in-flight 유지(재조회 요구)하고, 원 id 를 낡은 ODNO 에 재바인딩하지 않는다. 원 ODNO 는
        # 정정으로 무효화됐을 수 있어, 낡은 값에 재바인딩하면 이후 취소가 조용히 거부된다(은폐 금지).
        raise OrderError(
            "정정 접수(rt_cd=0)에 새 거래소 주문번호(ODNO)가 없다 -- 재조회 불가.",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )
    report = ExecutionReport(
        client_order_id=request_id,
        order_id=str(new_order_id or original_report.order_id),
        symbol=original_report.symbol,
        side=original_report.side,
        status=OrderStatus.PENDING_CANCEL if action == "cancel" else OrderStatus.PENDING_REPLACE,
        filled_quantity=original_report.filled_quantity,
        average_price=original_report.average_price,
        recorded_at=datetime.now(_KST),
        # 정정 응답의 새 조직번호(없으면 원주문 것 유지) -- 정정 시 새 ODNO 와 짝이 되어 영속된다.
        organization_number=_extract_organization_number(output) or original_report.organization_number,
        _raw=resp.body,
    )
    rebind: Binding | None = None
    if action == "modify":
        # 정정은 KIS 가 원주문에 **새 ODNO** 를 부여한다(원 ODNO 는 정정취소 가능수량 소멸). 원
        # client_order_id 가 이후에도 그 살아있는 주문을 가리키도록 표준 리포트+지문을 재바인딩한다:
        # order_id/조직번호(report._raw)는 정정 응답값, 지문 수량/가격은 정정값(이후 잔량 계산 정확),
        # 리포트 filled=0 (새 ODNO 는 정정 수량만큼의 신규 대기주문 -- 기체결분은 이전 실행에 남는다).
        # 변경요청 기록과 이 재바인딩은 record_change 로 한 번에 커밋해 크래시 창을 없앤다.
        resting_fingerprint = replace(
            original_fingerprint,
            quantity=format_wire_decimal(quantity),
            limit_price="" if limit_price is None else format_wire_decimal(limit_price),
        )
        rebound_report = replace(
            report, client_order_id=original_client_order_id,
            filled_quantity=Decimal(0), average_price=None,   # filled==0 => 평균가 없음(규약)
            # 원주문일자(접수 응답의 자기 일자)는 같은 날 정정 전후로 불변이다 -- 정정 응답엔
            # 이 값이 없어 새로 만든 리포트에선 None 이 되므로 원리포트 값을 이어붙인다. 안 그러면
            # 해외선물옵션(OSFO) 재바인딩 주문이 receipt_date=None 이 되어 이후 취소가 원주문일자
            # (ORGN_ORD_DT) 부재로 fail-closed 되어 영영 취소 불가가 된다. 이 키를 안 쓰는 자산은
            # original_report.receipt_date 가 None 이라 재바인딩 리포트가 종전과 동일하다.
            receipt_date=original_report.receipt_date,
        )
        rebind = Binding(rebound_report, resting_fingerprint)
    store.record_change(report, action_fingerprint, rebind=rebind)
    return report

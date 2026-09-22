"""KIS 응답 봉투(envelope) 해석 -- 성공/실패 판정과 누락 블록 오류.

KIS 표준 응답은 ``rt_cd``/``msg_cd``/``msg1`` 봉투에 ``output`` 블록(들)을 싣는다. 이
모듈은 자산군(국내/해외)·엔드포인트와 무관하게 그 봉투만 해석한다: 실패면
:class:`KISError` 로 올리고(조기 종료), 성공인데 기대한 출력 블록이 없으면 부분 결과
대신 fail-closed 오류를 만든다. 시장별 엔진이 공통으로 import 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..errors import KISError
from ..transport import RawResponse, Transport

#: KIS 연속조회 종료 센티널. 일부 조회는 tr_cont 를 F/M 로 유지한 채 연속키를 이 값으로 돌려
#: "더 없음"을 알린다 -- 이 키로는 재요청하지 않는다(같은 페이지 재조회/이중집계 방지).
_CONTINUATION_END = "^^"


def advance_cursor(
    body: Mapping[str, Any], *, ctx_width: int, prev_nk: str
) -> tuple[str, str] | None:
    """연속조회 다음 페이지 커서 ``(fk, nk)`` 를 돌려준다. ``tr_cont`` 가 F/M(더 있음)인데 연속키가
    진전하지 않으면(빈 키 / 직전과 같은 키 반복 / 종료 센티널 ``"^^"``) ``None`` 을 돌려 재요청을
    멈춘다 -- 같은 페이지를 다시 받아 행이 이중집계되는 것을 막는다. 공유 페이지네이터와 각 엔진의
    hand-rolled 연속조회 루프가 공용으로 쓴다(가드 로직 단일 출처)."""
    next_nk = str(body.get(f"ctx_area_nk{ctx_width}") or "").strip()
    if not next_nk or next_nk == prev_nk or next_nk == _CONTINUATION_END:
        return None
    next_fk = str(body.get(f"ctx_area_fk{ctx_width}") or "").strip()
    return next_fk, next_nk


def _missing_block_error(block: str, resp: RawResponse) -> KISError:
    return KISError(
        f"응답에 {block} 블록이 없다.",
        rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
    )


def _require_mapping_rows(block: str, resp: RawResponse) -> list[Mapping[str, Any]]:
    """성공 응답에서 행 배열 블록을 꺼내 검증한다. 블록이 없거나 리스트가 아니면
    :func:`_missing_block_error`, 원소 중 매핑이 아닌 것이 있으면(예: output=[None]/[1])
    fail-closed(:class:`KISError`). 정상 페이로드는 그대로 통과한다."""
    rows = resp.body.get(block)
    if not isinstance(rows, list):
        raise _missing_block_error(block, resp)
    for row in rows:
        if not isinstance(row, Mapping):
            raise KISError(
                f"{block} 블록에 매핑이 아닌 행이 있다: {row!r}",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
    return rows


def _raise_if_error(resp: RawResponse) -> None:
    if not resp.ok:
        raise KISError(
            f"조회 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )


def _fetch_paginated_rows(
    transport: Transport,
    *,
    path: str,
    tr_id: str,
    base_params: Mapping[str, str],
    output_key: str,
    max_pages: int,
    cap_message: str,
    ctx_width: int = 100,
    idempotent: bool = True,
) -> list[Mapping[str, Any]]:
    """GET 연속조회(pagination) 상태기계 공용 구현 -- 행 배열만 모은다.

    KIS의 다음페이지 조회는 응답헤더 ``tr_cont`` 가 F/M(더 있음)이면 응답 바디의 연속 커서
    (``ctx_area_fk*``/``ctx_area_nk*``)를 다음 요청의 ``CTX_AREA_FK*``/``CTX_AREA_NK*`` 로
    되먹이고 요청헤더 ``tr_cont`` 를 "N" 으로 보내는 방식이다. 이 순회를 한 곳에 가둔다.

    ``base_params`` 는 엔드포인트 고정 파라미터 + 연속 커서 키(``CTX_AREA_FK{ctx_width}``/
    ``NK{ctx_width}``, 최초 값 "")를 **와이어 순서 그대로** 담아야 한다 -- 매 페이지 이 사본의
    커서 키만 제자리에서 갱신하므로 쿼리스트링 바이트가 원본과 동일하게 유지된다. ``ctx_width``
    는 커서 키 폭(국내·해외 대부분 100, 해외 잔고/미체결/알고 등은 200)이다.

    ``output_key`` 블록(예: ``output``/``output1``)을 :func:`_require_mapping_rows` 로 검증해
    페이지마다 이어붙이고, 봉투 오류는 :func:`_raise_if_error` 로 조기 종료한다. ``max_pages``
    페이지 상한에 닿았는데 연속조회가 남아있으면 부분 결과로 자르지 않고 ``cap_message`` 로
    fail-closed 한다. 요약(output2/output3 등)을 페이지에 걸쳐 함께 모아야 하는 조회는
    :func:`_fetch_paginated_rows_with_summary` 를 쓴다 -- 이 함수는 그 위임 래퍼다(상태기계 단일 출처).
    """
    rows, _summary = _fetch_paginated_rows_with_summary(
        transport, path=path, tr_id=tr_id, base_params=base_params, output_key=output_key,
        max_pages=max_pages, cap_message=cap_message, summary_from=lambda _body: None,
        ctx_width=ctx_width, idempotent=idempotent,
    )
    return rows


def _fetch_paginated_rows_with_summary(
    transport: Transport,
    *,
    path: str,
    tr_id: str,
    base_params: Mapping[str, str],
    output_key: str,
    max_pages: int,
    cap_message: str,
    summary_from: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    ctx_width: int = 100,
    idempotent: bool = True,
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    """:func:`_fetch_paginated_rows` 와 같은 연속조회 상태기계에, 계좌 요약(output2/output3 등)을
    **첫 페이지에서 한 번** 함께 잡아 돌려주는 변형.

    ``summary_from`` 은 응답 바디에서 요약 객체를 뽑는 순수 함수(첫 페이지 한정, 계좌 단위라 페이지
    불변). 행 배열(``output_key``)은 :func:`_require_mapping_rows` 로 페이지마다 검증·누적하고, 상한
    도달 시 부분 결과로 자르지 않고 ``cap_message`` 로 fail-closed 한다. 반환은 ``(rows, summary)``
    -- ``summary`` 는 첫 페이지에서 못 뽑았으면 ``None`` (호출자가 필요하면 fail-closed 판단)."""
    fk_key = f"CTX_AREA_FK{ctx_width}"
    nk_key = f"CTX_AREA_NK{ctx_width}"
    rows: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] | None = None
    ctx_fk, ctx_nk, tr_cont = "", "", ""
    for _page in range(max_pages):
        params = dict(base_params)
        params[fk_key] = ctx_fk
        params[nk_key] = ctx_nk
        resp = transport.request(
            method="GET", path=path, tr_id=tr_id,
            params=params, idempotent=idempotent, tr_cont=tr_cont,
        )
        _raise_if_error(resp)
        if summary is None:  # 요약은 첫 페이지에서(계좌 단위라 페이지 불변)
            summary = summary_from(resp.body)
        rows.extend(_require_mapping_rows(output_key, resp))
        if resp.tr_cont not in ("F", "M"):
            break
        nxt = advance_cursor(resp.body, ctx_width=ctx_width, prev_nk=ctx_nk)
        if nxt is None:  # 비진전 커서(빈 키/반복/종료 센티널) -> 재요청 중단(이중집계 방지)
            break
        ctx_fk, ctx_nk = nxt
        tr_cont = "N"
    else:
        raise KISError(cap_message)
    return rows, summary

"""KIS 응답 봉투(envelope) 해석 -- 성공/실패 판정과 누락 블록 오류.

KIS 표준 응답은 ``rt_cd``/``msg_cd``/``msg1`` 봉투에 ``output`` 블록(들)을 싣는다. 이
모듈은 자산군(국내/해외)·엔드포인트와 무관하게 그 봉투만 해석한다: 실패면
:class:`KISError` 로 올리고(조기 종료), 성공인데 기대한 출력 블록이 없으면 부분 결과
대신 fail-closed 오류를 만든다. 시장별 엔진이 공통으로 import 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import KISError
from .transport import RawResponse


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

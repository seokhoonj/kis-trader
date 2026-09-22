"""공유 연속조회 상태기계(_internal._response) 직접 테스트 -- 비진전 커서 방어.

국내/해외 계좌·잔고·주문 조회가 모두 이 헬퍼를 쓴다. KIS 가 tr_cont 를 F/M 로 유지한 채 연속키를
진전시키지 않으면(빈 키/같은 키 반복/종료 센티널 "^^") 같은 페이지를 재요청해 행이 이중집계되거나
페이지 상한까지 무한 재요청하므로, 헬퍼가 그 커서에서 종료해야 한다.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from kis_trader._internal._response import (
    _fetch_paginated_rows,
    _fetch_paginated_rows_with_summary,
)
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


class ScriptedTransport:
    """페이지별 RawResponse 를 순서대로 돌려주고, 다 쓰면 마지막을 반복한다."""

    def __init__(self, pages: list[RawResponse]) -> None:
        self._pages = pages
        self.calls = 0
        self._lock = threading.Lock()

    def request(self, *, method: str, path: str, tr_id: str, params: Any = None,
                body: Any = None, idempotent: bool = True, tr_cont: str = "") -> RawResponse:
        with self._lock:
            i = min(self.calls, len(self._pages) - 1)
            self.calls += 1
        return self._pages[i]


def _page(nk: str, rows: list[dict], *, tr_cont: str = "F") -> RawResponse:
    return RawResponse(rt_cd="0", msg_cd="M", msg1="",
                       body={"output": rows, "ctx_area_nk100": nk, "ctx_area_fk100": ""},
                       tr_cont=tr_cont)


def _collect(transport: ScriptedTransport) -> list[dict]:
    return _fetch_paginated_rows(
        transport, path="/x", tr_id="T", base_params={"CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
        output_key="output", max_pages=100, cap_message="페이지 상한 도달",
    )


def test_repeated_cursor_terminates_without_cap_error():
    # 커서가 진전 안 하고 반복되면(K1 -> K1) 무한 재요청/이중집계 대신 그 지점에서 종료한다.
    # 가드가 없으면 max_pages 까지 돌아 cap 에러가 났다.
    t = ScriptedTransport([_page("K1", [{"a": "1"}]), _page("K1", [{"a": "2"}])])
    rows = _collect(t)
    assert rows == [{"a": "1"}, {"a": "2"}]      # 유한 종료, cap 에러 없음
    assert t.calls == 2                          # 반복 커서 감지 후 멈춤(100회 아님)


def test_continuation_end_sentinel_terminates():
    # 종료 센티널("^^")을 연속키로 받으면 그 키로 재요청하지 않는다.
    t = ScriptedTransport([_page("^^", [{"a": "1"}]), _page("^^", [{"a": "2"}])])
    assert _collect(t) == [{"a": "1"}]           # 첫 페이지만, 센티널로 종료
    assert t.calls == 1


def test_empty_cursor_terminates():
    # 빈 연속키(F/M 인데 nk="")도 재요청하지 않는다(같은 처음 페이지 재조회 방지).
    t = ScriptedTransport([_page("", [{"a": "1"}])])
    assert _collect(t) == [{"a": "1"}]
    assert t.calls == 1


def test_advancing_cursor_still_paginates_normally():
    # 정상적으로 진전하는 커서는 그대로 다음 페이지를 받는다(가드가 정상 조회를 막지 않는다).
    t = ScriptedTransport([
        _page("K1", [{"a": "1"}]), _page("K2", [{"a": "2"}]),
        _page("", [{"a": "3"}], tr_cont=""),     # 마지막 페이지(더 없음)
    ])
    assert _collect(t) == [{"a": "1"}, {"a": "2"}, {"a": "3"}]
    assert t.calls == 3


def test_with_summary_variant_also_guards_repeated_cursor():
    def _sum(body): return body.get("output2")
    pages = [
        RawResponse(rt_cd="0", msg_cd="M", msg1="",
                    body={"output": [{"a": "1"}], "output2": {"s": "x"},
                          "ctx_area_nk100": "K1", "ctx_area_fk100": ""}, tr_cont="F"),
        RawResponse(rt_cd="0", msg_cd="M", msg1="",
                    body={"output": [{"a": "2"}], "ctx_area_nk100": "K1", "ctx_area_fk100": ""},
                    tr_cont="F"),
    ]
    t = ScriptedTransport(pages)
    rows, summary = _fetch_paginated_rows_with_summary(
        t, path="/x", tr_id="T", base_params={"CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
        output_key="output", max_pages=100, cap_message="페이지 상한 도달",
        summary_from=_sum,
    )
    assert rows == [{"a": "1"}, {"a": "2"}]       # 유한 종료
    assert summary == {"s": "x"}                  # 요약은 첫 페이지에서
    assert t.calls == 2


def test_genuine_cap_still_fails_closed():
    # 커서가 매번 진짜로 바뀌면(무한히 다음 페이지가 있음) 상한에서 fail-closed 한다(부분 결과 금지).
    pages = [_page(f"K{i}", [{"a": str(i)}]) for i in range(200)]
    t = ScriptedTransport(pages)
    with pytest.raises(KISError, match="페이지 상한"):
        _fetch_paginated_rows(
            t, path="/x", tr_id="T", base_params={"CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
            output_key="output", max_pages=5, cap_message="페이지 상한 도달",
        )

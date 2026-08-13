"""기간봉 페이지네이터의 fail-closed 계약 -- collect_period_bars.

``max_bars`` 가 0 이하이면 조회가 무의미하므로 I/O(첫 요청) 전에 거부한다. ``None``(무제한)과
양의 정수는 그대로 동작한다. 종목/지수 공용 콜렉터라 여기서 한 번 막으면 모든 호출자가 보호된다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kis_openapi._bars import collect_period_bars
from kis_openapi._datetime import _KST
from kis_openapi.bar import Bar
from kis_openapi.errors import KISUsageError
from kis_openapi.transport import RawResponse


class _RecordingTransport:
    def __init__(self, *, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        self.calls.append({"path": path, "params": params})
        return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": self.rows})


class _ExplodingTransport:
    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        raise AssertionError("max_bars<=0 인데 와이어에 요청이 나갔다")


def _parse_rows(rows):
    return [
        Bar(
            symbol="005930",
            timestamp=datetime.strptime(row["d"], "%Y%m%d").replace(tzinfo=_KST),
            open=Decimal(row["o"]), high=Decimal(row["h"]),
            low=Decimal(row["l"]), close=Decimal(row["c"]), volume=int(row["v"]),
        )
        for row in rows
    ]


def _collect(transport, *, max_bars):
    return collect_period_bars(
        transport, path="/p", tr="TR", base_params={"FID": "x"},
        start_date="20240101", end_date="20240131", max_bars=max_bars,
        parse_rows=_parse_rows,
    )


@pytest.mark.parametrize("max_bars", [0, -1, -50])
def test_collect_period_bars_rejects_nonpositive_max_bars_before_io(max_bars):
    with pytest.raises(KISUsageError):
        _collect(_ExplodingTransport(), max_bars=max_bars)


def test_collect_period_bars_positive_and_none_still_work():
    # 시작일에 걸린 봉이면 한 페이지로 페이지네이션이 종료된다(oldest <= start).
    row = {"d": "20240101", "o": "100", "h": "110", "l": "95", "c": "105", "v": "1000"}
    for max_bars in (None, 5, 1):
        transport = _RecordingTransport(rows=[row])
        bars = _collect(transport, max_bars=max_bars)
        assert len(bars) == 1
        assert bars[0].close == Decimal(105)
        assert transport.calls                        # 유효 입력은 정상적으로 와이어에 도달

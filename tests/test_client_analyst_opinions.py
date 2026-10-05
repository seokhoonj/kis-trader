"""애널리스트 투자의견 -- kis.domestic.stock(code).analyst_opinions()."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from kis_trader import AnalystOpinion, KISClient
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


class SequencedTransport:
    """호출마다 큐에서 다음 응답을 돌려준다(구간 분할 백필 검증용)."""

    def __init__(self, *, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
            return self.responses.pop(0)


def _client(t):
    return KISClient(app_key="k", app_secret="s", transport=t)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output": rows})


def _day_before(ymd):
    """``YYYYMMDD`` 하루 전 -- 다음 창의 끝(직전 창 최오래된 날짜의 전날)을 계산할 때 쓴다."""
    return f"{datetime.strptime(ymd, '%Y%m%d') - timedelta(days=1):%Y%m%d}"  # noqa: DTZ007


def _rows_desc(count, *, newest_date, broker="유안타"):
    """최신->과거 ``count`` 행(``newest_date`` 부터 하루씩 과거로)."""
    base = datetime.strptime(newest_date, "%Y%m%d")  # noqa: DTZ007
    return [
        {"stck_bsop_date": f"{base - timedelta(days=i):%Y%m%d}", "invt_opnn": "BUY",
         "rgbf_invt_opnn": "BUY", "mbcr_name": broker, "hts_goal_prc": "90000",
         "stck_prdy_clpr": "75000", "dprt": "20.0"}
        for i in range(count)
    ]


def test_analyst_opinions_maps():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "중립",
             "mbcr_name": "유안타", "hts_goal_prc": "90000", "stck_prdy_clpr": "75000",
             "dprt": "20.0"}]
    fake = FakeTransport(response=_resp(rows))
    ops = _client(fake).domestic.stock("005930").analyst_opinions(start="20240101", end="20240513")
    assert isinstance(ops[0], AnalystOpinion)
    assert ops[0].broker == "유안타"
    assert ops[0].opinion == "매수"
    assert ops[0].previous_opinion == "중립"
    assert ops[0].target_price == Decimal(90000)
    assert ops[0].disparity_rate == Decimal("20.0")
    assert f"{ops[0].timestamp:%Y%m%d}" == "20240510"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/invest-opinion"
    assert call["tr_id"] == "FHKST663300C0"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "16633"
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"


def test_analyst_opinions_broker_defaults_empty_when_absent():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "중립"}]
    fake = FakeTransport(response=_resp(rows))
    op = _client(fake).domestic.stock("005930").analyst_opinions()[0]
    assert op.broker == ""


def test_analyst_opinions_collects_older_rows_when_first_page_truncated():
    # 첫 창이 상한(100)에 닿아 과거가 잘림 -> 창을 과거로 당겨 더 오래된 행까지 전량 모은다.
    first_window_rows = _rows_desc(100, newest_date="20240510")
    oldest_first = min(r["stck_bsop_date"] for r in first_window_rows)
    # 두 번째 창의 끝 = 첫 창 최오래된 날짜의 하루 전
    expected_end = _day_before(oldest_first)
    second_window_rows = _rows_desc(10, newest_date=expected_end)
    transport = SequencedTransport(responses=[_resp(first_window_rows), _resp(second_window_rows)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20240101", end="20240531")
    assert len(ops) == 110                         # 두 창 전량 수집
    assert len(transport.calls) == 2                # 상한 도달 -> 한 번 더 조회
    assert transport.calls[0]["params"]["FID_INPUT_DATE_2"] == "20240531"
    assert transport.calls[1]["params"]["FID_INPUT_DATE_2"] == expected_end
    assert transport.calls[1]["params"]["FID_INPUT_DATE_1"] == "20240101"  # start 는 고정


def test_analyst_opinions_stops_at_start_even_when_cap_hit():
    # 상한에 닿았지만 최오래된 날짜가 이미 start <= -> 더 조회하지 않는다(무한루프 방지).
    rows = _rows_desc(100, newest_date="20240410")  # 100일 과거 -> 20240101 근방까지
    oldest = min(r["stck_bsop_date"] for r in rows)
    transport = SequencedTransport(responses=[_resp(rows)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(start=oldest, end="20240410")
    assert len(ops) == 100
    assert len(transport.calls) == 1                 # oldest <= start -> 추가 조회 없음


def test_analyst_opinions_single_call_when_under_cap():
    transport = SequencedTransport(responses=[_resp(_rows_desc(5, newest_date="20240510"))])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20200101", end="20240531")
    assert len(ops) == 5
    assert len(transport.calls) == 1                 # 상한 미만 -> 넓은 구간이어도 한 번


def test_analyst_opinions_all_empty_date_page_stops_without_crash():
    # 상한을 채웠지만 모든 행의 날짜가 비면 opinion_dates 가 비어 min([]) 크래시 위험 ->
    # `not opinion_dates` 가드가 1콜로 종료시킨다(ValueError 없음).
    rows = [{"stck_bsop_date": "", "invt_opnn": "BUY", "mbcr_name": "유안타"} for _ in range(100)]
    transport = SequencedTransport(responses=[_resp(rows)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20200101", end="20240531")
    assert ops == []
    assert len(transport.calls) == 1


def test_analyst_opinions_advances_past_empty_date_rows_in_capped_page():
    # 상한 페이지에 날짜 없는 행이 섞여도(건너뜀) 유효 최오래된 날짜 기준으로 다음 창을 연다.
    first_window_rows = (
        _rows_desc(99, newest_date="20240510") + [{"stck_bsop_date": "", "invt_opnn": "BUY"}]
    )
    oldest_valid = min(r["stck_bsop_date"] for r in first_window_rows if r["stck_bsop_date"])
    expected_end = _day_before(oldest_valid)
    second_window_rows = _rows_desc(3, newest_date=expected_end)
    transport = SequencedTransport(responses=[_resp(first_window_rows), _resp(second_window_rows)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20240101", end="20240531")
    assert len(ops) == 102                           # 99 유효 + 3 (빈 날짜 1행 제외)
    assert len(transport.calls) == 2
    assert transport.calls[1]["params"]["FID_INPUT_DATE_2"] == expected_end


def test_analyst_opinions_backfills_across_three_windows():
    # 두 번 연속 상한 -> 두 번 전진하고 세 번째(상한 미만)에서 종료. window_end 가 홉마다
    # 갱신값으로 체인되는지(2번째 전진이 end_date 가 아닌 직전 창의 oldest-1 에서 출발).
    first_window_rows = _rows_desc(100, newest_date="20240601")
    end2 = _day_before(min(r["stck_bsop_date"] for r in first_window_rows))
    second_window_rows = _rows_desc(100, newest_date=end2)
    end3 = _day_before(min(r["stck_bsop_date"] for r in second_window_rows))
    third_window_rows = _rows_desc(4, newest_date=end3)
    transport = SequencedTransport(
        responses=[_resp(first_window_rows), _resp(second_window_rows), _resp(third_window_rows)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20200101", end="20240601")
    assert len(ops) == 204
    assert len(transport.calls) == 3
    assert transport.calls[1]["params"]["FID_INPUT_DATE_2"] == end2
    assert transport.calls[2]["params"]["FID_INPUT_DATE_2"] == end3  # 2번째 홉이 갱신값에서 체인


def test_analyst_opinions_stops_when_server_ignores_date_window():
    # 서버가 FID_INPUT_DATE_2 를 무시하고 같은 최신 100행을 재반환하면 oldest 가 고정돼 창이
    # 전진하지 못한다 -> next_window_end >= window_end 가드가 spin 을 막는다(3콜째 없음).
    page = _rows_desc(100, newest_date="20240601")
    transport = SequencedTransport(responses=[_resp(page), _resp(page)])
    ops = _client(transport).domestic.stock("005930").analyst_opinions(
        start="20200101", end="20241231")
    assert len(transport.calls) == 2                 # 가드가 3콜째를 막음(없으면 IndexError/무한)
    assert len(ops) == 200                           # 두 콜의 중복 포함(종료가 핵심)


def test_analyst_opinions_default_window():
    fake = FakeTransport(response=_resp([]))
    _client(fake).domestic.stock("005930").analyst_opinions(end="20240131")
    call = fake.calls[0]
    assert call["params"]["FID_INPUT_DATE_1"] == "20240101"       # 30일 전
    assert call["params"]["FID_INPUT_DATE_2"] == "20240131"


def test_analyst_opinions_optional_target_none():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "",
             "hts_goal_prc": "", "stck_prdy_clpr": "75000", "dprt": ""}]
    fake = FakeTransport(response=_resp(rows))
    op = _client(fake).domestic.stock("005930").analyst_opinions()[0]
    assert op.target_price is None
    assert op.disparity_rate is None


def test_analyst_opinions_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").analyst_opinions()


def test_analyst_opinions_bad_value_fails_closed():
    rows = [{"stck_bsop_date": "20240510", "invt_opnn": "매수", "rgbf_invt_opnn": "중립",
             "hts_goal_prc": "n/a"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.stock("005930").analyst_opinions()

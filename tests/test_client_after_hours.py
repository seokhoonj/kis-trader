"""시간외 단일가 -- kis.ticker(...).after_hours_quote().

예상체결가·최우선호가 매핑, 전일대비 부호 복원, 세션 밖 빈 값->None(optional), fail-closed 를
가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import AfterHoursQuote, KISClient
from kis_openapi.errors import KISError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/domestic-stock/v1/quotations/inquire-overtime-price"


def _output(**over):
    base = {"bidp": "71400", "askp": "71500", "ovtm_untp_antc_cnpr": "71450",
            "ovtm_untp_antc_cnqn": "1200", "ovtm_untp_antc_cntg_vrss": "50",
            "ovtm_untp_antc_cntg_ctrt": "0.07", "ovtm_untp_antc_cntg_vrss_sign": "2"}
    base |= over
    return base


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(output):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": output})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_after_hours_quote_maps_fields():
    fake = FakeTransport(response=_resp(_output()))
    quote = _client(fake).ticker("005930").after_hours_quote()
    assert isinstance(quote, AfterHoursQuote)
    assert quote.symbol == "005930"
    assert quote.bid == Decimal(71400)
    assert quote.ask == Decimal(71500)
    assert quote.expected_price == Decimal(71450)
    assert quote.expected_quantity == 1200
    assert quote.change == Decimal(50)
    assert quote.change_percent == Decimal("0.07")
    assert fake.calls[0]["path"] == _PATH
    assert fake.calls[0]["tr_id"] == "FHPST02300000"
    assert fake.calls[0]["params"] == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


def test_after_hours_quote_restores_down_sign():
    fake = FakeTransport(response=_resp(_output(ovtm_untp_antc_cntg_vrss_sign="5")))
    quote = _client(fake).ticker("005930").after_hours_quote()
    assert quote.change == Decimal(-50)                    # 하락 -> 음수
    assert quote.change_percent == Decimal("-0.07")


def test_after_hours_quote_empty_fields_become_none():
    empty = {k: "" for k in _output()}
    quote = _client(FakeTransport(response=_resp(empty))).ticker("005930").after_hours_quote()
    assert quote.bid is None
    assert quote.ask is None
    assert quote.expected_price is None
    assert quote.expected_quantity is None
    assert quote.change is None                            # 세션 밖 -> None
    assert quote.change_percent is None


def test_after_hours_quote_maps_partial_payload():
    # 일부만 채워진 경우 필드별로 독립 파싱되는지: bid/수량/change 만 존재.
    partial = _output(askp="", ovtm_untp_antc_cnpr="", ovtm_untp_antc_cntg_ctrt="")
    quote = _client(FakeTransport(response=_resp(partial))).ticker("005930").after_hours_quote()
    assert quote.bid == Decimal(71400)
    assert quote.ask is None
    assert quote.expected_price is None
    assert quote.expected_quantity == 1200
    assert quote.change == Decimal(50)
    assert quote.change_percent is None


def test_after_hours_quote_missing_output_block_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").after_hours_quote()


def test_after_hours_quote_bad_value_fails_closed():
    fake = FakeTransport(response=_resp(_output(ovtm_untp_antc_cnpr="oops")))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").after_hours_quote()


def test_after_hours_quote_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").after_hours_quote()


def _resp2(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상",
                       body={"output1": {}, "output2": rows})


def test_after_hours_conclusions_maps_output2():
    # 원장 응답 예시값(180025, 하락 sign=5).
    rows = [{"stck_cntg_hour": "180025", "stck_prpr": "2835", "prdy_vrss": "-70",
             "prdy_vrss_sign": "5", "prdy_ctrt": "-2.41", "askp": "2840", "bidp": "2835",
             "acml_vol": "68086", "cntg_vol": "12865"}]
    fake = FakeTransport(response=_resp2(rows))
    from kis_openapi import AfterHoursConclusion
    pts = _client(fake).ticker("005930").after_hours_conclusions()
    assert isinstance(pts[0], AfterHoursConclusion)
    assert pts[0].price == Decimal(2835)
    assert pts[0].change == Decimal(-70)                 # sign 5 -> 음수
    assert pts[0].change_percent == Decimal("-2.41")
    assert pts[0].ask == Decimal(2840)
    assert pts[0].cumulative_volume == 68086
    assert pts[0].tick_volume == 12865
    assert pts[0].timestamp.strftime("%H%M%S") == "180025"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion"
    assert call["tr_id"] == "FHPST02310000"
    assert call["params"]["FID_HOUR_CLS_CODE"] == "1"


def test_after_hours_daily_maps_output2():
    # 원장 응답 예시값(시간외가 상승 sign=2).
    rows = [{"stck_bsop_date": "20240223", "ovtm_untp_prpr": "106000",
             "ovtm_untp_prdy_vrss": "500", "ovtm_untp_prdy_vrss_sign": "2",
             "ovtm_untp_prdy_ctrt": "0.47", "ovtm_untp_vol": "12740",
             "ovtm_untp_tr_pbmn": "1348318000"}]
    fake = FakeTransport(response=_resp2(rows))
    from kis_openapi import AfterHoursDailyPrice
    pts = _client(fake).ticker("005930").after_hours_daily()
    assert isinstance(pts[0], AfterHoursDailyPrice)
    assert pts[0].price == Decimal(106000)
    assert pts[0].change == Decimal(500)                 # sign 2 -> 양수
    assert pts[0].change_percent == Decimal("0.47")
    assert pts[0].volume == 12740
    assert pts[0].amount == Decimal(1348318000)
    assert pts[0].timestamp.strftime("%Y%m%d") == "20240223"
    call = fake.calls[0]
    assert call["path"] == "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice"
    assert call["tr_id"] == "FHPST02320000"


def test_after_hours_history_missing_output2_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").after_hours_conclusions()

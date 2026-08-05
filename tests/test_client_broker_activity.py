"""회원사 매매 -- kis.ticker(...).broker_activity().

평평한 상위 5개(매도/매수) 필드를 중첩 리스트로 매핑, 외국계 여부(Y/N)->bool, 빈 순위 skip,
fail-closed 파싱을 가짜 전송으로 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import BrokerActivitySummary, KISClient
from kis_openapi.errors import KISError
from kis_openapi.transport import RawResponse

_MEMBER_PATH = "/uapi/domestic-stock/v1/quotations/inquire-member"


def _member_output():
    out = {}
    sellers = [("미래에셋", "0034", "12.5", "-1000", "N"), ("모간스탠리", "0044", "8.1", "500", "Y")]
    buyers = [("키움증권", "0050", "15.0", "3000", "N")]
    for i, (name, no, rlim, icdc, glob) in enumerate(sellers, start=1):
        out |= {f"seln_mbcr_name{i}": name, f"seln_mbcr_no{i}": no, f"seln_mbcr_rlim{i}": rlim,
                f"seln_qty_icdc{i}": icdc, f"seln_mbcr_glob_yn_{i}": glob}
    for i, (name, no, rlim, icdc, glob) in enumerate(buyers, start=1):
        out |= {f"shnu_mbcr_name{i}": name, f"shnu_mbcr_no{i}": no, f"shnu_mbcr_rlim{i}": rlim,
                f"shnu_qty_icdc{i}": icdc, f"shnu_mbcr_glob_yn_{i}": glob}
    return out


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


def test_broker_activity_maps_sellers_and_buyers():
    fake = FakeTransport(response=_resp(_member_output()))
    summary = _client(fake).ticker("005930").broker_activity()
    assert isinstance(summary, BrokerActivitySummary)
    assert summary.symbol == "005930"
    assert len(summary.sellers) == 2                      # 빈 3~5위는 제외
    assert len(summary.buyers) == 1
    top_seller = summary.sellers[0]
    assert top_seller.member_name == "미래에셋"
    assert top_seller.member_number == "0034"
    assert top_seller.volume_share_percent == Decimal("12.5")
    assert top_seller.quantity_change == -1000            # 증감 음수
    assert top_seller.is_foreign is False
    assert summary.sellers[1].is_foreign is True          # 외국계(Y)
    assert fake.calls[0]["path"] == _MEMBER_PATH
    assert fake.calls[0]["tr_id"] == "FHKST01010600"
    assert fake.calls[0]["params"] == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


def test_broker_activity_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").broker_activity()


def test_broker_activity_error_response_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="1", msg_cd="X", msg1="실패", body={}))
    with pytest.raises(KISError):
        _client(fake).ticker("005930").broker_activity()


def test_broker_activity_bad_share_percent_fails_closed():
    bad = _member_output() | {"seln_mbcr_rlim1": "n/a"}
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(bad))).ticker("005930").broker_activity()

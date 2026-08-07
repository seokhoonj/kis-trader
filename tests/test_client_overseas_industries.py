"""해외 거래소 업종 코드 목록 조회의 라우팅·파싱·실패 경계를 검증한다."""

from __future__ import annotations

import threading

import pytest

from kis_openapi import KISClient, OverseasIndustry
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse

_PATH = "/uapi/overseas-price/v1/quotations/industry-price"


class FakeTransport:
    def __init__(self, *, responses):
        self.responses = iter(responses)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append(
                {"method": method, "path": path, "tr_id": tr_id, "params": params}
            )
        return next(self.responses)


def _client(transport, *, environment="real"):
    return KISClient(
        app_key="k", app_secret="s", environment=environment, transport=transport
    )


def _response(output2):
    return RawResponse(
        rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output2": output2}
    )


def test_overseas_industries_routes_and_parses_single_call():
    fake = FakeTransport(
        responses=[
            _response(
                [
                    {"icod": "000", "name": "전체"},
                    {"icod": "010", "name": "에너지 및 관련 서비스"},
                ]
            )
        ]
    )
    industries = _client(fake).overseas_industries("NAS")

    assert [(item.code, item.name) for item in industries] == [
        ("000", "전체"),
        ("010", "에너지 및 관련 서비스"),
    ]
    assert all(isinstance(item, OverseasIndustry) for item in industries)
    assert fake.calls == [
        {
            "method": "GET",
            "path": _PATH,
            "tr_id": "HHDFS76370100",
            "params": {"AUTH": "", "EXCD": "NAS"},
        }
    ]


def test_overseas_industries_rejects_demo_environment():
    fake = FakeTransport(responses=[])
    with pytest.raises(KISUsageError):
        _client(fake, environment="demo").overseas_industries("NAS")
    assert fake.calls == []


@pytest.mark.parametrize("output2", [None, {}, "not-an-array"])
def test_overseas_industries_missing_or_non_list_output2_fails_closed(output2):
    body = {} if output2 is None else {"output2": output2}
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)
    fake = FakeTransport(responses=[response])
    with pytest.raises(KISError):
        _client(fake).overseas_industries("NAS")


def test_overseas_industries_non_mapping_item_fails_closed():
    fake = FakeTransport(responses=[_response([{"icod": "000", "name": "전체"}, "bad"])])
    with pytest.raises(KISError):
        _client(fake).overseas_industries("NAS")

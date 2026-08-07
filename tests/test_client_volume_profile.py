"""per-ticker 매물대/거래비중 -- volume_profile."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import KISClient, VolumeAtPrice, VolumeProfile
from kis_openapi.errors import KISError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append(
                {"method": method, "path": path, "tr_id": tr_id, "params": params,
                 "idempotent": idempotent}
            )
        return self.response


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def _summary(**overrides):
    summary = {
        "rprs_mrkt_kor_name": "KOSDAQ",
        "stck_prpr": "3240",
        "prdy_vrss_sign": "5",
        "prdy_vrss": "25",
        "prdy_ctrt": "0.77",
        "acml_vol": "847563",
        "prdy_vol": "913274",
        "wghn_avrg_stck_prc": "3256.34",
        "lstn_stcn": "106209702",
        "hts_kor_isnm": "테스트종목",
        "stck_shrn_iscd": "123456",
    }
    summary.update(overrides)
    return summary


def _bands():
    return [
        {"data_rank": "1", "stck_prpr": "3255", "cntg_vol": "124515",
         "acml_vol_rlim": "14.69"},
        {"data_rank": "2", "stck_prpr": "3260", "cntg_vol": "123909",
         "acml_vol_rlim": "14.62"},
    ]


def _response(*, output1=None, output2=None):
    return RawResponse(
        rt_cd="0",
        msg_cd="MCA00000",
        msg1="정상",
        body={
            "output1": _summary() if output1 is None else output1,
            "output2": _bands() if output2 is None else output2,
        },
    )


def test_volume_profile_routes_and_maps_ledger_values():
    fake = FakeTransport(response=_response())
    profile = _client(fake).ticker("123456").volume_profile()

    assert isinstance(profile, VolumeProfile)
    assert profile.symbol == "123456"
    assert profile.market == "KOSDAQ"
    assert profile.name == "테스트종목"
    assert profile.price == Decimal(3240)
    assert profile.change == Decimal(-25)
    assert profile.change_percent == Decimal("-0.77")
    assert profile.volume == 847563
    assert profile.weighted_average_price == Decimal("3256.34")
    assert profile.listed_shares == 106209702
    assert isinstance(profile.bands[0], VolumeAtPrice)
    assert [(band.rank, band.price, band.volume, band.volume_share_percent)
            for band in profile.bands] == [
        (1, Decimal(3255), 124515, Decimal("14.69")),
        (2, Decimal(3260), 123909, Decimal("14.62")),
    ]
    assert len(fake.calls) == 1
    assert fake.calls[0] == {
        "method": "GET",
        "path": "/uapi/domestic-stock/v1/quotations/pbar-tratio",
        "tr_id": "FHPST01130000",
        "params": {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": "123456",
            "FID_COND_SCR_DIV_CODE": "20113",
            "FID_INPUT_HOUR_1": "",
        },
        "idempotent": True,
    }


@pytest.mark.parametrize(
    ("output1", "output2"),
    [([], _bands()), (_summary(), None), (_summary(), {})],
)
def test_volume_profile_missing_blocks_fail_closed(output1, output2):
    body = {"output1": output1}
    if output2 is not None:
        body["output2"] = output2
    response = RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body=body)

    with pytest.raises(KISError):
        _client(FakeTransport(response=response)).ticker("123456").volume_profile()


def test_volume_profile_bad_required_numeric_fails_closed():
    response = _response(output1=_summary(wghn_avrg_stck_prc="not-a-number"))

    with pytest.raises(KISError):
        _client(FakeTransport(response=response)).ticker("123456").volume_profile()


def test_volume_profile_entities_are_hashable_and_raw_does_not_affect_equality():
    first = _client(FakeTransport(response=_response())).ticker("123456").volume_profile()
    changed_raw = _summary(extra_vendor_field="ignored")
    second = _client(
        FakeTransport(response=_response(output1=changed_raw))
    ).ticker("123456").volume_profile()

    assert first == second
    assert hash(first) == hash(second)

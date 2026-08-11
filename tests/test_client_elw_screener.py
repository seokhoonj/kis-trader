"""ELW 스크리닝 -- kis.domestic.elw_screener.*.

기초자산 목록/별 시세, 비교종목, 신규상장, 만기예정 각각의 TR·URL·시장구분 W·필터 파라미터
(콜풋 코드가 엔드포인트마다 다름), 공통 행 파싱(코드/이름 폴백·optional 필드·부호 복원), fail-closed
를 검증한다.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_openapi import ELWListing, ELWScreenerQueries, ELWUnderlying, KISClient
from kis_openapi.errors import KISError, KISUsageError
from kis_openapi.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="MCA00000", msg1="정상", body={"output": rows})


def _client(transport):
    return KISClient(app_key="k", app_secret="s", transport=transport)


def test_screener_accessor():
    assert isinstance(_client(FakeTransport(response=_resp([]))).domestic.elw_screener, ELWScreenerQueries)


# --- underlyings (기초자산 목록) --------------------------------------------
def test_underlyings_maps_and_params():
    rows = [{"unas_shrn_iscd": "2001", "unas_isnm": "KOSPI200", "unas_prpr": "371.33",
             "unas_prdy_vrss": "0.17", "unas_prdy_vrss_sign": "2", "unas_prdy_ctrt": "0.05"},
            {"unas_shrn_iscd": "005930", "unas_isnm": "삼성전자", "unas_prpr": "40850",
             "unas_prdy_vrss": "300", "unas_prdy_vrss_sign": "5", "unas_prdy_ctrt": "0.73"}]
    fake = FakeTransport(response=_resp(rows))
    unders = _client(fake).domestic.elw_screener.underlyings(sort="gainers")
    assert all(isinstance(u, ELWUnderlying) for u in unders)
    assert unders[0].symbol == "2001"
    assert unders[0].name == "KOSPI200"
    assert unders[0].price == Decimal("371.33")
    assert unders[0].change == Decimal("0.17")            # sign 2 -> 상승
    assert unders[1].change == Decimal(-300)              # sign 5 -> 하락
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/udrl-asset-list"
    assert call["tr_id"] == "FHKEW154100C0"
    assert call["params"]["FID_RANK_SORT_CLS_CODE"] == "3"        # gainers
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11541"


# --- by_underlying (기초자산별 ELW) -----------------------------------------
def test_by_underlying_maps_listing_and_params():
    rows = [{"elw_shrn_iscd": "57JAAQ", "hts_kor_isnm": "한국JAAQ삼성전자풋", "elw_prpr": "10",
             "prdy_vrss": "0", "prdy_vrss_sign": "3", "prdy_ctrt": "0.00", "acml_vol": "0",
             "acpr": "63300.00", "unas_isnm": "삼성전자", "stck_cnvr_rate": "0.01"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.by_underlying("005930")
    assert all(isinstance(x, ELWListing) for x in listings)
    row = listings[0]
    assert row.symbol == "57JAAQ"
    assert row.name == "한국JAAQ삼성전자풋"                  # hts_kor_isnm 폴백
    assert row.underlying_name == "삼성전자"
    assert row.price == Decimal(10)
    assert row.strike == Decimal("63300.00")
    assert row.volume == 0
    assert row._raw["stck_cnvr_rate"] == "0.01"           # 전환비율은 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/udrl-asset-price"
    assert call["tr_id"] == "FHKEW154101C0"
    assert call["params"]["FID_UNAS_INPUT_ISCD"] == "005930"
    assert call["params"]["FID_MRKT_CLS_CODE"] == "A"


# --- comparables (비교대상, 코드+이름만) ------------------------------------
def test_comparables_minimal_rows():
    rows = [{"elw_shrn_iscd": "58J782", "elw_kor_isnm": "KBJ782삼성전자풋"},
            {"elw_shrn_iscd": "58JC71", "elw_kor_isnm": "KBJC71삼성전자콜"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.comparables("005930")
    assert [x.symbol for x in listings] == ["58J782", "58JC71"]
    assert listings[0].price is None                      # 시세 없음
    assert listings[0].change is None
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/compare-stocks"
    assert call["tr_id"] == "FHKEW151701C0"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"


# --- newly_listed (콜풋 코드 02/00/01) --------------------------------------
def test_newly_listed_right_code_and_dates():
    rows = [{"elw_shrn_iscd": "57K924", "elw_kor_isnm": "한국K924HLB콜", "unas_isnm": "HLB",
             "stck_lstn_date": "20240320", "stck_last_tr_date": "20240613", "acpr": "78000.00"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.newly_listed(date="20240410", right="call")
    row = listings[0]
    assert row.symbol == "57K924"
    assert row.listing_date is not None
    assert f"{row.listing_date:%Y%m%d}" == "20240320"
    assert f"{row.last_trade_date:%Y%m%d}" == "20240613"
    assert row.price is None                              # 신규상장은 시세 없음
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/newly-listed"
    assert call["tr_id"] == "FHKEW154800C0"
    assert call["params"]["FID_DIV_CLS_CODE"] == "00"            # 신규상장 call=00
    assert call["params"]["FID_INPUT_DATE_1"] == "20240410"
    assert call["params"]["FID_INPUT_ISCD_2"] == "00000"         # 기본 발행사=전체(형제 조회와 동일 코드표)


# --- expiring (콜풋 코드 2/0/1) ---------------------------------------------
def test_expiring_right_code_and_range():
    rows = [{"elw_shrn_iscd": "58K374", "elw_kor_isnm": "KBK374KOSPI200풋",
             "unas_isnm": "KOSPI200", "elw_prpr": "515", "acpr": "372.50",
             "stck_last_tr_date": "20240613"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.expiring(start="20240611", end="20240618", right="put")
    assert listings[0].symbol == "58K374"
    assert listings[0].price == Decimal(515)
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/expiration-stocks"
    assert call["tr_id"] == "FHKEW154700C0"
    assert call["params"]["FID_DIV_CLS_CODE"] == "1"            # 만기 put=1
    assert call["params"]["FID_INPUT_DATE_1"] == "20240611"
    assert call["params"]["FID_INPUT_DATE_2"] == "20240618"


# --- 공통 (빈 행 skip, 코드 폴백, 검증, fail-closed) ------------------------
def test_bond_shrn_iscd_fallback():
    rows = [{"bond_shrn_iscd": "57JAES", "hts_kor_isnm": "한국JAESKOSPI200콜", "elw_prpr": "1560"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.by_underlying("2001")
    assert listings[0].symbol == "57JAES"                 # bond_shrn_iscd 폴백


def test_listings_skip_empty_rows():
    rows = [{"elw_shrn_iscd": "57JAAQ", "hts_kor_isnm": "x"}, {"elw_shrn_iscd": ""}]
    fake = FakeTransport(response=_resp(rows))
    assert len(_client(fake).domestic.elw_screener.by_underlying("005930")) == 1


def test_screener_rejects_bad_right():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.elw_screener.newly_listed(date="20240410", right="both")


def test_underlyings_rejects_bad_sort():
    fake = FakeTransport(response=_resp([]))
    with pytest.raises(KISUsageError):
        _client(fake).domestic.elw_screener.underlyings(sort="nope")


def test_screener_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.elw_screener.underlyings()


def test_by_underlying_bad_value_fails_closed():
    rows = [{"elw_shrn_iscd": "57JAAQ", "hts_kor_isnm": "x", "elw_prpr": "n/a"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).domestic.elw_screener.by_underlying("005930")


# --- search (조건검색, 60파라미터·풍부한 _raw) ------------------------------
def test_search_maps_rich_row_and_sends_all_params():
    rows = [{"bond_shrn_iscd": "57JAES", "hts_kor_isnm": "한국JAESKOSPI200콜",
             "rght_type_name": "CALL", "elw_prpr": "1560", "prdy_vrss": "0",
             "prdy_vrss_sign": "3", "prdy_ctrt": "0.00", "acml_vol": "0", "acpr": "325.00",
             "unas_isnm": "KOSPI200", "stck_lstn_date": "20231018",
             "stck_last_tr_date": "20240613", "delta_val": "1.000000", "lvrg_val": "24.22"}]
    fake = FakeTransport(response=_resp(rows))
    listings = _client(fake).domestic.elw_screener.search(underlying="2001")
    assert all(isinstance(x, ELWListing) for x in listings)
    row = listings[0]
    assert row.symbol == "57JAES"                         # bond_shrn_iscd 폴백
    assert row.name == "한국JAESKOSPI200콜"
    assert row.price == Decimal(1560)
    assert row.strike == Decimal("325.00")
    assert row._raw["delta_val"] == "1.000000"            # 그릭스는 _raw
    assert row._raw["lvrg_val"] == "24.22"
    call = fake.calls[0]
    assert call["path"] == "/uapi/elw/v1/quotations/cond-search"
    assert call["tr_id"] == "FHKEW15100000"
    assert call["params"]["FID_COND_SCR_DIV_CODE"] == "11510"
    assert call["params"]["FID_UNAS_INPUT_ISCD"] == "2001"
    # 원장 요청 예시대로 세부 필터 60여 개를 공백으로 전부 전송
    assert call["params"]["FID_DELTA1"] == ""
    assert call["params"]["FID_THETA2"] == ""
    assert len(call["params"]) == 57                      # 전체 파라미터 수


def test_search_missing_output_fails_closed():
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={}))
    with pytest.raises(KISError):
        _client(fake).domestic.elw_screener.search()

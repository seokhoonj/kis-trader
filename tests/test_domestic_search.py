"""국내 종목 이름검색 -- kis.domestic.search + 마스터 파서/인덱스.

이름은 모호("삼성전자"⊃"삼성전자우")하므로 검색은 후보를 하나로 좁히지 않고 모두 돌려준다
(조용한 오확정 방지). 실 서버 없이 합성 .mst/주입 인덱스로 검증한다.
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from kis_trader import DomesticListing, KISClient
from kis_trader._internal._masters import (
    _DOMESTIC_PART2_WIDTH,
    DomesticListingIndex,
    download_domestic_master,
    load_domestic_index,
    load_domestic_master,
    parse_domestic_master,
)
from kis_trader.errors import KISError, KISUsageError

_LISTINGS = [
    DomesticListing("005930", "삼성전자", "KOSPI"),
    DomesticListing("005935", "삼성전자우", "KOSPI"),
    DomesticListing("006400", "삼성SDI", "KOSPI"),
    DomesticListing("035720", "카카오", "KOSPI"),
    DomesticListing("247540", "에코프로비엠", "KOSDAQ"),
]


def _index():
    return DomesticListingIndex(_LISTINGS)


# --- 인덱스 검색 -----------------------------------------------------------
def test_search_returns_all_name_matches_with_exact_first():
    hits = _index().search("삼성전자")
    assert hits[0].symbol == "005930"                    # 정확일치가 먼저
    assert {h.symbol for h in hits} == {"005930", "005935"}  # 우선주도 포함(안 삼킴)


def test_search_substring_matches_are_all_returned():
    hits = _index().search("삼성")
    assert {h.symbol for h in hits} == {"005930", "005935", "006400"}


def test_search_filters_by_market():
    hits = _index().search("에코", market="KOSDAQ")
    assert [h.symbol for h in hits] == ["247540"]
    assert _index().search("에코", market="KOSPI") == []


def test_search_by_six_digit_code_reverse_lookup():
    hits = _index().search("005930")
    assert [h.name for h in hits] == ["삼성전자"]


def test_search_short_numeric_query_matches_code_prefix():
    # 6자리 미만 숫자는 코드 접두 검색.
    assert {h.symbol for h in _index().search("0059")} == {"005930", "005935"}


def test_search_orders_exact_then_prefix_then_substring():
    idx = DomesticListingIndex([
        DomesticListing("100000", "KODEX 삼성전자레버리지", "KOSPI"),  # 부분(이름 중간에 포함)
        DomesticListing("005935", "삼성전자우", "KOSPI"),               # 접두
        DomesticListing("005930", "삼성전자", "KOSPI"),                 # 정확
    ])
    assert [h.symbol for h in idx.search("삼성전자")] == ["005930", "005935", "100000"]


def test_search_no_match_is_empty_not_error():
    assert _index().search("존재하지않는종목") == []


def test_search_rejects_empty_query():
    with pytest.raises(KISUsageError):
        _index().search("   ")


def test_search_rejects_invalid_market():
    with pytest.raises(KISUsageError):
        _index().search("삼성", market="NASDAQ")


# --- 마스터 파서(고정폭 cp949) ---------------------------------------------
def _mst_line(symbol: str, standard: str, name: str, *, market: str) -> str:
    # part1 = 단축코드(폭9, 우측패딩) + 표준코드(12) + 한글명, 그 뒤 고정 tail.
    return symbol.ljust(9) + standard + name + ("X" * _DOMESTIC_PART2_WIDTH[market])


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_parse_domestic_master_slices_code_and_name(market):
    lines = [
        _mst_line("005930", "KR7005930003", "삼성전자", market=market),
        _mst_line("035720", "KR7035720002", "카카오", market=market),
    ]
    master_bytes = ("\n".join(lines) + "\n").encode("cp949")
    parsed = parse_domestic_master(master_bytes, market=market)
    assert parsed == [
        DomesticListing("005930", "삼성전자", market),
        DomesticListing("035720", "카카오", market),
    ]


def test_parse_domestic_master_skips_short_or_blank_lines():
    good = _mst_line("005930", "KR7005930003", "삼성전자", market="KOSPI")
    master_bytes = f"\ntoo-short\n{good}\n".encode("cp949")
    assert parse_domestic_master(master_bytes, market="KOSPI") == [
        DomesticListing("005930", "삼성전자", "KOSPI")
    ]


def test_parse_domestic_master_skips_line_with_code_but_no_name():
    # 단축코드는 6자리지만 이름이 빈 손상 레코드는 건너뛴다.
    no_name = "005930".ljust(9) + "KR7005930003" + "" + ("X" * _DOMESTIC_PART2_WIDTH["KOSPI"])
    good = _mst_line("035720", "KR7035720002", "카카오", market="KOSPI")
    master_bytes = f"{no_name}\n{good}\n".encode("cp949")
    assert parse_domestic_master(master_bytes, market="KOSPI") == [
        DomesticListing("035720", "카카오", "KOSPI")
    ]


def test_parse_domestic_master_fails_closed_on_format_drift():
    # 비지 않은 입력인데 유효 종목 0건(part2 폭 변경 등) -> 조용히 [] 가 아니라 raise(해외 파서와 대칭).
    drifted = ("x" * 50 + "\n").encode("cp949")   # 어떤 줄도 6자리 코드+이름을 못 낸다
    with pytest.raises(KISError):
        parse_domestic_master(drifted, market="KOSPI")


def test_parse_domestic_master_rejects_unknown_market():
    with pytest.raises(KISUsageError):
        parse_domestic_master(b"", market="NYSE")


# --- 다운로드 배관(가짜 fetch, 네트워크 없음) ------------------------------
def _fake_zip(member: str, body: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, body)
    return buffer.getvalue()


def test_download_domestic_master_selects_named_member_and_parses():
    body = (_mst_line("005930", "KR7005930003", "삼성전자", market="KOSPI") + "\n").encode("cp949")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _fake_zip("kospi_code.mst", body)

    listings = download_domestic_master("KOSPI", fetch=fetch)
    assert listings == [DomesticListing("005930", "삼성전자", "KOSPI")]
    assert calls == ["https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"]


def test_download_domestic_master_fails_closed_on_missing_member():
    def fetch(url: str) -> bytes:
        return _fake_zip("wrong_name.mst", b"")

    with pytest.raises(KISError):
        download_domestic_master("KOSPI", fetch=fetch)


def test_download_domestic_master_selects_named_member_not_first():
    # zip 에 오답 멤버가 먼저 있어도 이름으로 kospi_code.mst 를 골라야 한다(첫 엔트리 가정 금지).
    body = (_mst_line("005930", "KR7005930003", "삼성전자", market="KOSPI") + "\n").encode("cp949")

    def fetch(url: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("_decoy_first.mst", b"garbage")
            archive.writestr("kospi_code.mst", body)
        return buffer.getvalue()

    assert download_domestic_master("KOSPI", fetch=fetch) == [
        DomesticListing("005930", "삼성전자", "KOSPI")
    ]


def test_load_domestic_master_reads_fresh_cache_without_fetching(tmp_path):
    # 캐시가 max_age 안이면 fetch 없이 캐시를 읽는다(호출되면 실패하는 fetch 로 확인).
    body = (_mst_line("005930", "KR7005930003", "삼성전자", market="KOSPI") + "\n").encode("cp949")
    cache = tmp_path / "kospi_code.mst"
    cache.write_bytes(body)
    os.utime(cache, (1000.0, 1000.0))     # mtime 고정

    def fetch(url: str) -> bytes:
        raise AssertionError("신선한 캐시는 다운로드하지 않아야 한다")

    listings = load_domestic_master("KOSPI", cache_dir=str(tmp_path), fetch=fetch, now=1000.0)
    assert listings == [DomesticListing("005930", "삼성전자", "KOSPI")]


def test_load_domestic_index_merges_both_markets_via_injected_fetch(tmp_path):
    bodies = {
        "kospi_code": _mst_line("005930", "KR7005930003", "삼성전자", market="KOSPI"),
        "kosdaq_code": _mst_line("247540", "KR7247540003", "에코프로비엠", market="KOSDAQ"),
    }

    def fetch(url: str) -> bytes:
        name = url.rsplit("/", 1)[1].removesuffix(".mst.zip")
        return _fake_zip(f"{name}.mst", (bodies[name] + "\n").encode("cp949"))

    index = load_domestic_index(cache_dir=str(tmp_path), fetch=fetch)
    assert [h.symbol for h in index.search("삼성전자")] == ["005930"]
    assert [h.symbol for h in index.search("에코", market="KOSDAQ")] == ["247540"]


# --- 파사드 + stock() 은 이름을 받지 않음(안전) ----------------------------
def test_domestic_search_facade_uses_injected_index():
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01",
                    transport=object(), domestic_index=_index())
    hits = kis.domestic.search("카카오")
    assert [h.symbol for h in hits] == ["035720"]


def test_stock_still_rejects_a_korean_name():
    # 검색은 이름을 받지만 stock()/주문 경로는 코드 전용(암묵 resolve 없음).
    kis = KISClient(app_key="k", app_secret="s", account="12345678-01", transport=object())
    with pytest.raises(KISUsageError):
        kis.domestic.stock("삼성전자").quote()

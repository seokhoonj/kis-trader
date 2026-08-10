"""해외 종목 마스터 파서 -- parse_overseas_master.

탭 구분 cp949 24컬럼 레이아웃(공식 헤더)을 심볼/거래소/통화/유형/이름으로 정확히 파싱하는지,
빈 줄 skip, 컬럼 부족 시 fail-closed 를 검증한다. 실 다운로드 없이 합성 fixture 로.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from kis_openapi._masters import (
    InstrumentRecord,
    MasterIndex,
    download_overseas_master,
    load_overseas_index,
    load_overseas_master,
    parse_overseas_master,
)
from kis_openapi.errors import KISUsageError


def _row(*, exchange, symbol, rsym, korean, english, stis, currency):
    """공식 24컬럼 레이아웃에 값을 배치한 한 줄(탭 구분). 인덱스는 헤더 순서."""
    cols = [""] * 24
    cols[0] = "US"
    cols[1] = "23"
    cols[2] = exchange
    cols[3] = "거래소명"
    cols[4] = symbol
    cols[5] = rsym
    cols[6] = korean
    cols[7] = english
    cols[8] = stis
    cols[9] = currency
    cols[22] = "001"
    return "\t".join(cols)


def _master_bytes(rows):
    return ("\n".join(rows) + "\n").encode("cp949")


def test_parse_maps_columns():
    data = _master_bytes([
        _row(exchange="NAS", symbol="AAPL", rsym="NASAAPL", korean="애플", english="APPLE INC",
             stis="2", currency="USD"),
        _row(exchange="AMS", symbol="SPY", rsym="AMSSPY", korean="SPDR S&P500",
             english="SPDR S&P 500 ETF", stis="3", currency="USD"),
    ])
    records = parse_overseas_master(data)
    assert len(records) == 2
    first = records[0]
    assert isinstance(first, InstrumentRecord)
    assert first.symbol == "AAPL"
    assert first.exchange == "NAS"
    assert first.currency == "USD"
    assert first.security_type == "stock"        # stis 2 -> stock
    assert first.korean_name == "애플"           # cp949 한글 왕복
    assert first.english_name == "APPLE INC"
    assert first.realtime_symbol == "NASAAPL"
    assert records[1].security_type == "etf"      # stis 3 -> etf


def test_parse_skips_blank_lines():
    data = _master_bytes([
        _row(exchange="TSE", symbol="7203", rsym="TSE7203", korean="도요타", english="TOYOTA",
             stis="2", currency="JPY"),
        "",
        "   ",
    ])
    records = parse_overseas_master(data)
    assert len(records) == 1
    assert records[0].symbol == "7203"
    assert records[0].currency == "JPY"


def test_parse_unknown_security_type_keeps_raw_code():
    data = _master_bytes([
        _row(exchange="HKS", symbol="0700", rsym="HKS0700", korean="텐센트", english="TENCENT",
             stis="9", currency="HKD"),
    ])
    assert parse_overseas_master(data)[0].security_type == "9"   # 미지원 코드는 원값 보존


def test_parse_short_row_fails_closed():
    bad = "US\t23\tNAS\t나스닥"                       # 컬럼 4개뿐 -> 포맷 변경
    with pytest.raises(ValueError, match="컬럼 수"):
        parse_overseas_master((bad + "\n").encode("cp949"))


def _fake_fetch_for(rows):
    """rows(마스터 텍스트 줄들)을 담은 zip 바이트를 돌려주는 fake fetch 를 만든다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("NASMST.COD", _master_bytes(rows))
    payload = buffer.getvalue()

    def fetch(url):
        assert url.endswith("nasmst.cod.zip")
        return payload

    return fetch


def test_download_unzips_and_parses():
    fetch = _fake_fetch_for([
        _row(exchange="NAS", symbol="AAPL", rsym="NASAAPL", korean="애플", english="APPLE",
             stis="2", currency="USD"),
    ])
    records = download_overseas_master("nas", fetch=fetch)
    assert [r.symbol for r in records] == ["AAPL"]
    assert records[0].exchange == "NAS"


def test_download_rejects_unknown_market():
    with pytest.raises(KISUsageError):
        download_overseas_master("xxx", fetch=lambda url: b"")


def test_index_resolve_single_match():
    index = MasterIndex([
        InstrumentRecord("AAPL", "NAS", "USD", "stock", "애플", "APPLE", "NASAAPL"),
        InstrumentRecord("7203", "TSE", "JPY", "stock", "도요타", "TOYOTA", "TSE7203"),
    ])
    record = index.resolve("AAPL")
    assert record.exchange == "NAS"
    assert record.currency == "USD"


def test_index_resolve_ambiguous_requires_exchange():
    index = MasterIndex([
        InstrumentRecord("XYZ", "NAS", "USD", "stock", "", "XYZ NAS", "NASXYZ"),
        InstrumentRecord("XYZ", "HKS", "HKD", "stock", "", "XYZ HK", "HKSXYZ"),
    ])
    with pytest.raises(KISUsageError, match="여러 거래소"):
        index.resolve("XYZ")
    picked = index.resolve("XYZ", exchange="HKS")     # 명시하면 좁혀짐
    assert picked.currency == "HKD"


def test_index_resolve_not_found():
    index = MasterIndex([InstrumentRecord("AAPL", "NAS", "USD", "stock", "", "APPLE", "NASAAPL")])
    with pytest.raises(KISUsageError, match="찾지 못"):
        index.resolve("MSFT")


class _CountingFetch:
    """호출 횟수를 세는 fake fetch. 시장코드별 zip 페이로드를 돌려준다."""

    def __init__(self, rows_by_code):
        self.calls = 0
        self._payloads = {}
        for code, rows in rows_by_code.items():
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(f"{code.upper()}MST.COD", _master_bytes(rows))
            self._payloads[code] = buffer.getvalue()

    def __call__(self, url):
        self.calls += 1
        for code, payload in self._payloads.items():
            if url.endswith(f"{code}mst.cod.zip"):
                return payload
        raise AssertionError(url)


def test_load_master_downloads_then_uses_cache(tmp_path):
    fetch = _CountingFetch({"nas": [
        _row(exchange="NAS", symbol="AAPL", rsym="NASAAPL", korean="애플", english="APPLE",
             stis="2", currency="USD"),
    ]})
    cache = str(tmp_path)
    first = load_overseas_master("nas", cache_dir=cache, fetch=fetch, now=1000.0)
    assert [r.symbol for r in first] == ["AAPL"]
    assert fetch.calls == 1
    # 캐시가 신선하면(now 가 max_age 안) 다시 다운로드하지 않는다.
    again = load_overseas_master("nas", cache_dir=cache, fetch=fetch, now=1000.0 + 3600)
    assert [r.symbol for r in again] == ["AAPL"]
    assert fetch.calls == 1                            # 재다운로드 없음


def test_load_master_refetches_when_stale(tmp_path):
    fetch = _CountingFetch({"nas": [
        _row(exchange="NAS", symbol="AAPL", rsym="NASAAPL", korean="애플", english="APPLE",
             stis="2", currency="USD"),
    ]})
    cache = str(tmp_path)
    load_overseas_master("nas", cache_dir=cache, fetch=fetch, now=1000.0)
    # now 가 max_age(하루)를 넘으면 다시 받는다.
    load_overseas_master("nas", cache_dir=cache, fetch=fetch, now=1000.0 + 86400 + 1)
    assert fetch.calls == 2


def test_load_index_combines_markets(tmp_path):
    fetch = _CountingFetch({
        "nas": [_row(exchange="NAS", symbol="AAPL", rsym="NASAAPL", korean="애플",
                     english="APPLE", stis="2", currency="USD")],
        "tse": [_row(exchange="TSE", symbol="7203", rsym="TSE7203", korean="도요타",
                     english="TOYOTA", stis="2", currency="JPY")],
    })
    index = load_overseas_index(["nas", "tse"], cache_dir=str(tmp_path), fetch=fetch, now=1000.0)
    assert index.resolve("AAPL").exchange == "NAS"
    assert index.resolve("7203").currency == "JPY"

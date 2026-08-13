"""해외 심볼 조회 -- kis.instrument(symbol).

세션에 붙은 마스터 인덱스로 심볼->거래소/통화/유형을 해석한다. 인덱스를 주입해 실 네트워크 없이
검증(거래소 자동 해석, 모호 시 exchange 요구, 미발견)한다.
"""

from __future__ import annotations

import pytest

from kis_trader import InstrumentRecord, KISClient, MasterIndex
from kis_trader.errors import KISUsageError


class FakeTransport:
    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        raise AssertionError("instrument() 는 transport 를 쓰지 않는다")


def _client(index):
    return KISClient(app_key="k", app_secret="s", transport=FakeTransport(), master_index=index)


_INDEX = MasterIndex([
    InstrumentRecord("AAPL", "NAS", "USD", "stock", "애플", "APPLE INC", "NASAAPL"),
    InstrumentRecord("7203", "TSE", "JPY", "stock", "도요타", "TOYOTA", "TSE7203"),
    InstrumentRecord("XYZ", "NAS", "USD", "stock", "", "XYZ NAS", "NASXYZ"),
    InstrumentRecord("XYZ", "HKS", "HKD", "stock", "", "XYZ HK", "HKSXYZ"),
])


def test_instrument_resolves_exchange_and_currency():
    record = _client(_INDEX).instrument("AAPL")
    assert isinstance(record, InstrumentRecord)
    assert record.exchange == "NAS"
    assert record.currency == "USD"
    assert record.security_type == "stock"
    assert record.english_name == "APPLE INC"


def test_instrument_resolves_non_us_market():
    record = _client(_INDEX).instrument("7203")
    assert record.exchange == "TSE"
    assert record.currency == "JPY"


def test_instrument_ambiguous_requires_exchange():
    client = _client(_INDEX)
    with pytest.raises(KISUsageError, match="여러 거래소"):
        client.instrument("XYZ")
    assert client.instrument("XYZ", exchange="HKS").currency == "HKD"


def test_instrument_unknown_symbol_raises():
    with pytest.raises(KISUsageError, match="찾지 못"):
        _client(_INDEX).instrument("MSFT")


def test_instrument_does_not_download_when_index_injected():
    # 주입 인덱스가 있으면 master_fetch 를 절대 호출하지 않는다(네트워크 접촉 없음).
    def exploding_fetch(url):
        raise AssertionError("주입 인덱스가 있으면 다운로드하면 안 된다")

    client = KISClient(app_key="k", app_secret="s", transport=FakeTransport(),
                       master_index=_INDEX, master_fetch=exploding_fetch)
    assert client.instrument("AAPL").exchange == "NAS"


def test_instrument_lazy_build_uses_master_fetch(tmp_path, monkeypatch):
    import io
    import zipfile

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def _row(symbol, exchange, currency):
        cols = [""] * 24
        cols[2], cols[4], cols[5] = exchange, symbol, exchange + symbol
        cols[6], cols[7], cols[8], cols[9] = "이름", "NAME", "2", currency
        return "\t".join(cols)

    def fetch(url):
        # 요청된 시장코드 하나만 채우고 나머지는 빈 마스터로 돌려준다.
        rows = [_row("NVDA", "NAS", "USD")] if url.endswith("nasmst.cod.zip") else []
        code = url.rsplit("/", 1)[-1].removesuffix("mst.cod.zip")  # URL 에서 시장코드 추출
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            member = f"{code}mst.cod"      # KIS 실제 멤버명 규칙과 일치
            archive.writestr(member, ("\n".join(rows) + ("\n" if rows else "")).encode("cp949"))
        return buffer.getvalue()

    client = KISClient(app_key="k", app_secret="s", transport=FakeTransport(), master_fetch=fetch)
    record = client.instrument("NVDA")
    assert record.exchange == "NAS"
    assert record.currency == "USD"

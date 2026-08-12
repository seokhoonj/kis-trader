"""해외 종목 마스터 파일 파싱 (내부) -- 심볼 -> 거래소/통화/종목유형 해석의 원천.

해외는 바(bare) 심볼("AAPL")만으론 거래소를 알 수 없다(같은 티커가 여러 시장에 있을 수 있고, KIS 는
거래소코드 EXCD 를 요구한다). KIS 가 배포하는 시장별 종목 마스터 파일을 받아 심볼 -> 거래소를 찾는다.

마스터 파일: ``https://new.real.download.dws.co.kr/common/master/{code}mst.cod.zip`` (KIS 공식 배포).
``{code}`` = 시장코드(아래 :data:`OVERSEAS_MARKETS`). 압축 해제 시 탭 구분 cp949 텍스트, 24개 컬럼
(공식 헤더 ``해외종목코드정보`` 레이아웃). 이 모듈은 그 텍스트를 :class:`InstrumentRecord` 로 파싱만 한다
(다운로드/캐시는 별도). pandas 의존 없이 표준 라이브러리로 파싱한다.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from ._fsutil import atomic_write_bytes
from .errors import KISError, KISUsageError

#: 마스터 캐시 기본 수명(초). 하루 -- KIS 가 마스터를 매일 갱신한다.
DEFAULT_MASTER_MAX_AGE = 86400

#: 마스터 파일 다운로드 URL 템플릿({code} 자리에 시장코드).
OVERSEAS_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/{code}mst.cod.zip"

#: 해외 시장코드 -> 사람이 읽는 이름(KIS 공식). 미국 3 + 중국 4 + 일본/홍콩/베트남.
OVERSEAS_MARKETS: dict[str, str] = {
    "nas": "나스닥", "nys": "뉴욕", "ams": "아멕스",
    "shs": "상해", "shi": "상해지수", "szs": "심천", "szi": "심천지수",
    "tse": "도쿄", "hks": "홍콩", "hnx": "하노이", "hsx": "호치민",
}

#: 종목유형 코드(마스터 stis 컬럼) -> 이름.
SecurityType = Literal["index", "stock", "etf", "warrant"]
_SECURITY_TYPES: dict[str, SecurityType] = {"1": "index", "2": "stock", "3": "etf", "4": "warrant"}

#: 마스터 레코드의 탭 컬럼 인덱스(공식 헤더 순서). 정체성/메타 필드만 읽는다.
_COL_EXCHANGE = 2       # excd  거래소코드
_COL_SYMBOL = 4         # symb  심볼
_COL_REALTIME_SYMBOL = 5  # rsym  실시간 심볼
_COL_KOREAN_NAME = 6    # knam
_COL_ENGLISH_NAME = 7   # enam
_COL_SECURITY_TYPE = 8  # stis
_COL_CURRENCY = 9       # curr
#: 위 인덱스를 읽으려면 최소 이만큼의 컬럼이 있어야 한다(포맷 변경 감지).
_MIN_COLUMNS = 10


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """해외 마스터 한 종목(불변). 심볼->거래소 해석과 통화/유형/이름 메타를 담는다."""

    symbol: str
    exchange: str                     # KIS 거래소코드(EXCD): NAS/NYS/AMS/...
    currency: str                     # 결제 통화(USD/HKD/JPY/...)
    security_type: SecurityType | str  # stock/etf/index/warrant (미지원 코드면 원값)
    korean_name: str
    english_name: str
    realtime_symbol: str              # 실시간 시세용 심볼(rsym)


def parse_overseas_master(data: bytes) -> list[InstrumentRecord]:
    """마스터 파일 원본(압축 해제된 cp949 텍스트 바이트) -> :class:`InstrumentRecord` 리스트.

    탭 구분·cp949 인코딩. 빈 줄은 건너뛰고, 데이터 줄의 컬럼 수가 기대(24 레이아웃의 최소치)에
    못 미치면 포맷 변경으로 보고 :class:`ValueError`(조용히 자르지 않는다)."""
    text = data.decode("cp949")
    records: list[InstrumentRecord] = []
    for line in text.splitlines():
        if not line.strip():           # 빈 줄(말미 개행 등) skip
            continue
        cols = line.split("\t")
        if len(cols) < _MIN_COLUMNS:   # 포맷 변경 -> fail-closed
            raise ValueError(
                f"해외 마스터 컬럼 수가 예상보다 적다({len(cols)} < {_MIN_COLUMNS}) -- 포맷 변경 의심."
            )
        type_code = cols[_COL_SECURITY_TYPE].strip()
        records.append(
            InstrumentRecord(
                symbol=cols[_COL_SYMBOL].strip(),
                exchange=cols[_COL_EXCHANGE].strip(),
                currency=cols[_COL_CURRENCY].strip(),
                security_type=_SECURITY_TYPES.get(type_code, type_code),
                korean_name=cols[_COL_KOREAN_NAME].strip(),
                english_name=cols[_COL_ENGLISH_NAME].strip(),
                realtime_symbol=cols[_COL_REALTIME_SYMBOL].strip(),
            )
        )
    return records


#: 마스터 zip 을 받아 오는 함수 타입(url -> zip 바이트). 주입해서 테스트/전송계층을 갈아끼운다.
Fetch = Callable[[str], bytes]


def fetch_overseas_master_raw(code: str, *, fetch: Fetch) -> bytes:
    """``code`` 시장의 마스터 zip 을 받아 압축 해제한 원본(.cod) 바이트를 돌려준다. ``fetch(url)`` 는
    zip 바이트를 돌려주는 주입 함수(기본 :func:`urlopen_fetch`)."""
    if code not in OVERSEAS_MARKETS:
        raise KISUsageError(
            f"알 수 없는 해외 시장코드: {code!r} ({'/'.join(OVERSEAS_MARKETS)})."
        )
    zip_bytes = fetch(OVERSEAS_MASTER_URL.format(code=code))
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        # 기대 멤버를 이름으로 명시 선택(첫/유일 엔트리 가정 금지). KIS 는 대소문자를
        # 섞어 배포하므로(nasmst.cod / NASMST.COD) 소문자로 정규화해 대조한다.
        expected = f"{code}mst.cod"
        member = next(
            (name for name in archive.namelist() if name.lower() == expected),
            None,
        )
        if member is None:             # 기대 멤버 없음 -> fail-closed
            raise KISError(
                f"해외 마스터 zip 에 기대 멤버 {expected!r} 가 없다: {code} "
                f"(멤버 {archive.namelist()})."
            )
        return archive.read(member)


def download_overseas_master(code: str, *, fetch: Fetch) -> list[InstrumentRecord]:
    """``code`` 시장의 마스터 파일을 받아 :class:`InstrumentRecord` 리스트로(캐시 없이 매번 다운로드)."""
    return parse_overseas_master(fetch_overseas_master_raw(code, fetch=fetch))


def urlopen_fetch(url: str) -> bytes:
    """기본 마스터 fetcher -- KIS 배포 서버에서 zip 을 받는다(인증 불필요한 정적 파일)."""
    import urllib.request

    # URL 은 고정 KIS 호스트 + 검증된 시장코드 템플릿이라 사용자 입력이 섞이지 않는다.
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


class MasterIndex:
    """심볼 -> 거래소 해석 인덱스. 여러 시장 마스터를 합쳐 심볼로 :class:`InstrumentRecord` 를 찾는다.

    같은 심볼이 여러 거래소에 있으면 ``exchange`` 를 명시해야 한다(오조회 방지). 국내 6자리 코드는
    이 인덱스를 안 거친다(:mod:`~kis_openapi.instrument` 가 KRX 로 판별).
    """

    __slots__ = ("_by_symbol",)

    def __init__(self, records: Iterable[InstrumentRecord]) -> None:
        by_symbol: dict[str, list[InstrumentRecord]] = {}
        for record in records:
            by_symbol.setdefault(record.symbol, []).append(record)
        self._by_symbol = by_symbol

    def resolve(self, symbol: str, *, exchange: str | None = None) -> InstrumentRecord:
        """심볼(과 선택적 ``exchange``)로 마스터 레코드 하나를 찾는다.

        없으면/모호하면(여러 거래소) :class:`~kis_openapi.errors.KISUsageError`. ``exchange`` 를 주면
        그 거래소로 좁힌다."""
        matches = self._by_symbol.get(symbol, [])
        if exchange is not None:
            matches = [record for record in matches if record.exchange == exchange]
        if not matches:
            hint = f" (거래소 {exchange!r})" if exchange is not None else ""
            raise KISUsageError(f"해외 마스터에서 심볼을 찾지 못했다: {symbol!r}{hint}.")
        exchanges = {record.exchange for record in matches}
        if len(exchanges) > 1:
            raise KISUsageError(
                f"심볼 {symbol!r} 이 여러 거래소에 있다: {sorted(exchanges)} "
                f"-- exchange= 로 지정하라."
            )
        return matches[0]


def default_cache_dir() -> str:
    """마스터 캐시 디렉터리(repo 밖, 런타임 캐시). ``XDG_CACHE_HOME`` 을 존중한다."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kis-openapi", "masters")


def load_overseas_master(
    code: str,
    *,
    cache_dir: str | None = None,
    max_age: int = DEFAULT_MASTER_MAX_AGE,
    fetch: Fetch = urlopen_fetch,
    now: float | None = None,
) -> list[InstrumentRecord]:
    """``code`` 시장의 마스터를 캐시 우선으로 로드. 캐시 파일이 ``max_age`` 안이면 다운로드 없이
    읽고, 오래됐거나 없으면 받아서 원자적으로 캐시에 쓴 뒤 파싱한다."""
    if code not in OVERSEAS_MARKETS:   # 파일시스템/경로 조립 전에 fail-closed
        raise KISUsageError(           # 미지의 코드가 stray 캐시 경로를 만들지 못하게
            f"알 수 없는 해외 시장코드: {code!r} ({'/'.join(OVERSEAS_MARKETS)})."
        )
    cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
    path = os.path.join(cache_dir, f"{code}mst.cod")
    stamp = time.time() if now is None else now
    if os.path.exists(path) and (stamp - os.path.getmtime(path)) < max_age:
        with open(path, "rb") as cached:
            return parse_overseas_master(cached.read())
    raw = fetch_overseas_master_raw(code, fetch=fetch)
    os.makedirs(cache_dir, exist_ok=True)
    atomic_write_bytes(path, raw)      # 보안 원자적 쓰기(예측 불가 임시파일, 부분 파일 방지)
    os.utime(path, (stamp, stamp))     # mtime 을 조회 시각으로 -- staleness 판정을 시계와 일치시킴
    return parse_overseas_master(raw)


def load_overseas_index(
    markets: Iterable[str] | None = None,
    *,
    cache_dir: str | None = None,
    max_age: int = DEFAULT_MASTER_MAX_AGE,
    fetch: Fetch = urlopen_fetch,
    now: float | None = None,
) -> MasterIndex:
    """여러 해외 시장 마스터(기본 전체)를 캐시 우선으로 로드해 합친 :class:`MasterIndex` 를 만든다."""
    codes = list(OVERSEAS_MARKETS) if markets is None else list(markets)
    records: list[InstrumentRecord] = []
    for code in codes:
        records.extend(
            load_overseas_master(
                code, cache_dir=cache_dir, max_age=max_age, fetch=fetch, now=now
            )
        )
    return MasterIndex(records)

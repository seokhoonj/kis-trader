"""해외 종목 마스터 파일 파싱 (내부) -- 심볼 -> 거래소/통화/종목유형 해석의 원천.

해외는 바(bare) 심볼("AAPL")만으론 거래소를 알 수 없다(같은 티커가 여러 시장에 있을 수 있고, KIS 는
거래소코드 EXCD 를 요구한다). KIS 가 배포하는 시장별 종목 마스터 파일을 받아 심볼 -> 거래소를 찾는다.

마스터 파일: ``https://new.real.download.dws.co.kr/common/master/{code}mst.cod.zip`` (KIS 공식 배포).
``{code}`` = 시장코드(아래 :data:`OVERSEAS_MARKETS`). 압축 해제 시 탭 구분 cp949 텍스트, 24개 컬럼
(공식 헤더 ``해외종목코드정보`` 레이아웃). 이 모듈은 그 텍스트를 :class:`MasterRecord` 로 파싱만 한다
(다운로드/캐시는 별도). pandas 의존 없이 표준 라이브러리로 파싱한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
class MasterRecord:
    """해외 마스터 한 종목(불변). 심볼->거래소 해석과 통화/유형/이름 메타를 담는다."""

    symbol: str
    exchange: str                     # KIS 거래소코드(EXCD): NAS/NYS/AMS/...
    currency: str                     # 결제 통화(USD/HKD/JPY/...)
    security_type: SecurityType | str  # stock/etf/index/warrant (미지원 코드면 원값)
    korean_name: str
    english_name: str
    realtime_symbol: str              # 실시간 시세용 심볼(rsym)


def parse_overseas_master(data: bytes) -> list[MasterRecord]:
    """마스터 파일 원본(압축 해제된 cp949 텍스트 바이트) -> :class:`MasterRecord` 리스트.

    탭 구분·cp949 인코딩. 빈 줄은 건너뛰고, 데이터 줄의 컬럼 수가 기대(24 레이아웃의 최소치)에
    못 미치면 포맷 변경으로 보고 :class:`ValueError`(조용히 자르지 않는다)."""
    text = data.decode("cp949")
    records: list[MasterRecord] = []
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
            MasterRecord(
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

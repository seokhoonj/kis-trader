"""닫힌 KIS 와이어 어휘 + 공용 타입 별칭의 단일 거주지.

KIS 가 값을 고정된 코드 집합으로만 받는 자리(주문 방향·신용유형·접속 환경·파생
시장/상품 구분 등)의 :class:`~typing.Literal` 별칭과, 여러 모듈이 공유하는 수치/JSON
별칭을 한곳에 모은다. 목적은 **한 어휘 = 한 정의**: 같은 코드 집합이 모듈마다 재정의
되어 어긋나는 일을 막는다.

이 모듈은 표준 라이브러리만 쓰고, 이미 각 도메인 모듈이 소유한 별칭은 **재정의하지
않고 재수출**한다(예: :data:`Side`/:data:`CreditType` 은 :mod:`.order`, :data:`Environment`
은 :mod:`.transport` 가 정본).

명명 규약(자산군 코드의 성격을 이름으로 드러낸다):

* ``Market`` -- 보드/세그먼트 구분 코드(어느 판이냐; 예: 파생 F/O).
* ``Exchange`` -- 거래 체결 장소(어느 거래소냐; 예: NAS/NYS/CME). 거래소는 열린
  대집합이라 Literal 로 닫지 않는다 -- 이 규약은 이름에만 적용한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, TypeAlias

from .order import AlgoStrategy, CreditType, Side
from .transport import Environment

#: 수치 와이어 파라미터의 입력 허용형. KIS 로 나가는 수량/가격 등은 정수·실수·Decimal·
#: 이미 정본화된 문자열 중 무엇으로도 받아 와이어 문자열로 정규화한다.
Numeric: TypeAlias = int | float | Decimal | str

#: JSON 값(재귀). 파싱 전 벤더 페이로드를 정직하게 기술한다. 인바운드 KIS 바디는 실제로
#: 이질적이라 파서 경계에서는 여전히 ``Mapping[str, Any]`` 가 정당하다(:mod:`.transport`
#: 참고); 이 별칭은 스칼라/컨테이너를 명시적으로 좁혀 쓰고 싶은 자리를 위한 것이다.
JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)

#: JSON 객체 -- 문자열 키의 JSON 매핑. 파싱된 KIS 바디를 그대로 실어 나르는 자리(예: 예외의
#: ``raw`` 원본 바디)를 위해 :data:`JSONValue` 를 한 겹 감싼 별칭이다. 저장된 바디는 읽기
#: 전용으로만 다뤄 ``Mapping`` 으로 좁힌다(구체 ``dict`` 타입에 묶지 않는다).
JSONObject: TypeAlias = Mapping[str, JSONValue]

#: 국내 파생 시장(보드) 구분 코드 -- F(지수선물)/O(지수옵션). ``FID_COND_MRKT_DIV_CODE``
#: 로 나간다. 어느 "판"이냐를 고르는 board/segment 코드다(명명 규약의 ``Market``).
DerivativeMarket: TypeAlias = Literal["F", "O"]

#: 해외 파생 상품 구분 -- ``"future"``(선물)/``"option"``(옵션). 국내의 F/O 와 달리 보드가
#: 아니라 상품 종류이며, 엔드포인트/TR-ID 선택 키로 쓰인다.
DerivativeProduct: TypeAlias = Literal["future", "option"]

#: 해외 예약주문의 통화 선택 -- 홍콩(HKS) 예약의 상품유형(HKD/CNY/USD)을 고르는 자리에만 의미가
#: 있고 미지정이면 HKD 로 본다(그 외 거래소에 주면 :class:`~kis_trader.errors.KISUsageError`).
#: ``PRDT_TYPE_CD`` 파생의 입력 어휘다.
ReservedCurrency: TypeAlias = Literal["HKD", "CNY", "USD"]

#: 계좌 조회의 매도매수 필터 -- ``"all"``(전체)/``"buy"``(매수)/``"sell"``(매도). 체결내역·거래내역
#: 조회가 행을 방향으로 좁힐 때 쓰는 입력 어휘로, 와이어 ``SLL_BUY_DVSN_CD``(00/02/01)로 매핑된다.
SideFilter: TypeAlias = Literal["all", "buy", "sell"]

#: 기간 손익 조회의 정렬 순서 -- ``"recent"``(최근순)/``"oldest"``(과거순). 와이어 ``SORT_DVSN``
#: (00/01)로 매핑된다.
ProfitSort: TypeAlias = Literal["recent", "oldest"]

# :data:`AlgoStrategy`(미국주식 TWAP/VWAP 분할주문 전략)은 :mod:`.order` 가 정본이라 재정의하지 않고
# 위에서 import 해 재수출한다(:data:`Side`/:data:`CreditType` 과 같은 방식).

__all__ = [
    "AlgoStrategy",
    "CreditType",
    "DerivativeMarket",
    "DerivativeProduct",
    "Environment",
    "JSONObject",
    "JSONValue",
    "Numeric",
    "ProfitSort",
    "ReservedCurrency",
    "Side",
    "SideFilter",
]

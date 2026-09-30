"""주문 요청(DATA) 타입 -- 불변 :class:`Order`.

주문 타입과 필요한 가격의 관계(지정가는 ``limit_price`` 필수, 스탑은 ``stop_price``
필수)를 **생성 시점에 강제**한다(FIX OrdType 규칙). "두 개의 선택 가격을 가진 한 구조체"가
아니라 타입별 생성자(:meth:`Order.market` / :meth:`Order.limit` / :meth:`Order.stop` /
:meth:`Order.stop_limit`)로 만들어, 잘못된 조합이 애초에 표현 불가능하게 한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal, NamedTuple, cast, get_args

from ._internal._wire import format_wire_decimal
from .errors import KISUsageError
from .instrument import DomesticBoard

if TYPE_CHECKING:
    from collections.abc import Sequence

    # 주문 수치 파라미터의 입력 허용형(int|float|Decimal|str). 주석 전용이라 런타임 순환 import
    # (_literals -> order)을 피하려 TYPE_CHECKING 아래에서 들여온다.
    from ._literals import Numeric

Side = Literal["buy", "sell"]
#: 옵션 권리 방향 -- 콜/풋. 파생(XKFE) 옵션 주문의 상품구분(derivative_item "02" 콜 / "03" 풋)을
#: 정하는 사용자-대면 어휘다(선물엔 없다). 옵션 계약 핸들이 이 값을 받아 발주에 싣는다.
Right = Literal["call", "put"]
#: 파생(XKFE) 상품 구분(FUOP_ITEM_DVSN_CD) -- "01" 선물 / "02" 콜옵션 / "03" 풋옵션. 현금·해외
#: 주문은 ``""``. 야간(STTN) 발주·조회 와이어에 필수이며, 야간 가드는 비어있음만 보므로("99" 같은
#: 미지정 코드가 와이어에 새는 fail-open) 이 Literal 로 유효값만 받게 좁혀 타입체크에서 막는다.
DerivativeItem = Literal["", "01", "02", "03"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]
#: 미국주식 알고리즘 분할주문 전략 -- TWAP(시간 기준 분할)/VWAP(체결량 기준 분할). KIS 가 서버에서
#: 지정 조건에 맞춰 주문을 쪼개 집행하는 "서버자동주문"이며, 미국(NAS/NYS/AMS)만 지원한다(국내·아시아엔
#: 없다). 와이어 주문구분 ``ORD_DVSN`` 35 TWAP / 36 VWAP 로 나간다. 즉시주문은 시간창(START_TIME/
#: END_TIME)으로 집행 구간을 정하거나 정규장 종료까지 집행하고, 예약주문은 정규장 종료 고정이다.
AlgoStrategy = Literal["twap", "vwap"]
#: 국내(KRX) 현금주문 전용 주문구분(가격 결정 방식). 지정가(order_type="limit")/시장가("market")를
#: 넘어서는 KRX 고유 주문구분을 고른다. 해외 주문에는 없다(국내 전용).
#:   ``conditional_limit`` 조건부지정가(02) -- 장중 지정가, 마감 동시호가에 시장가 전환(가격 필요).
#:     (KRX 공식 영문 "Limit-to-Market-on-Close".)
#:   ``immediate_limit`` 최유리지정가(03) -- 접수 시점 상대편 최우선호가에 지정가로 접수해 즉시 체결.
#:     매도면 최우선 매수호가, 매수면 최우선 매도호가. 가격 없음(시장이 정함). (KRX 공식 영문
#:     "Immediately Executable Limit Order"; 시장가 슬리피지를 피하는 즉시체결 대안.)
#:   ``priority_limit`` 최우선지정가(04) -- 접수 시점 같은 방향 최우선호가에 지정가로 접수(체결
#:     우선순위 확보, 즉시 체결은 아님). 매도면 최우선 매도호가, 매수면 최우선 매수호가. 가격 없음.
#:   ``midpoint`` 중간가(21; IOC 23 / FOK 24) -- 수량만 지정, 가격은 최우선 매수/매도호가의 중간값으로
#:     시장이 정한다(가격 없음). KRX 정규장 + NXT 메인마켓 시간대에 가능(전 보드).
#:   ``pre_market_close`` 장전 시간외 종가(05) -- 전일 종가로 체결(가격 없음). KRX 전용, 08:30~08:40.
#:   ``post_market_close`` 장후 시간외 종가(06) -- 당일 종가로 체결(가격 없음). KRX 전용, 15:40~16:00.
#:   ``after_market_limit`` 애프터마켓 지정가(41; IOC 42 / FOK 43) -- KRX 애프터마켓(16:00~20:00) 지정가.
#:     ``limit_price`` 필요. KRX 정규장과 분리된 시장이라 별도 주문구분이다.
#:   ``after_market_immediate_limit`` 애프터마켓 최유리지정가(44; IOC 45 / FOK 46) -- 상대편 최우선호가로
#:     즉시 체결. 가격 없음. KRX 애프터마켓.
#:   ``after_market_priority_limit`` 애프터마켓 최우선지정가(47) -- 같은 방향 최우선호가로 접수. 가격 없음,
#:     day 만(IOC/FOK 없음). KRX 애프터마켓.
#:   ``gtp_limit`` NXT GTP 지정가(27) -- NXT 프리마켓 전용호가(Good Till Pre-Market). ``limit_price`` 필요,
#:     day 만. 미체결은 프리마켓 종료(08:50)에 일괄 취소.
#:   ``gtp_immediate_limit`` NXT GTP 최유리지정가(28) -- 가격 없음, day 만. NXT 프리마켓.
#:   ``gtp_priority_limit`` NXT GTP 최우선지정가(29) -- 가격 없음, day 만. NXT 프리마켓.
#: IOC/FOK 는 별도 주문구분이 아니라 ``time_in_force``(ioc/fok)로 조합한다(midpoint 와 애프터마켓 지정가/
#: 최유리만 지원; 최우선·GTP·시간외종가는 day 전용이라 IOC/FOK 조합이 미매핑되어 거부된다).
#: ``after_market_*`` 는 board="KRX", ``gtp_*`` 는 board="NXT" 에서만 가능하다(다른 보드는 생성 시점 거부).
DomesticDivision = Literal[
    "conditional_limit", "immediate_limit", "priority_limit",
    "midpoint", "pre_market_close", "post_market_close",
    "after_market_limit", "after_market_immediate_limit", "after_market_priority_limit",
    "gtp_limit", "gtp_immediate_limit", "gtp_priority_limit",
]
#: 파생(선물/옵션, XKFE) 주문의 주문구분. 현금주문의 국내 주문구분과 같은 두 코드를 쓰되(조건부지정가·
#: 최유리지정가), 최우선지정가(priority_limit)는 파생에 없어 뺀 좁힌 별칭이다.
DerivativeDivision = Literal["conditional_limit", "immediate_limit"]
#: 파생(XKFE) 주문이 허용하는 division 집합 -- 현금 전용 주문구분(priority_limit·Tier2)은 여기
#: 없어 구성 시점에 거부된다(:class:`Order` __post_init__).
_DERIVATIVE_DIVISIONS = frozenset(get_args(DerivativeDivision))
#: 국내 주문구분의 가격 결합 -- 가격없음(시장 결정, order_type="market", ORD_UNPR "0") vs 지정가 기반
#: (order_type="limit", limit_price 필수). DomesticDivision 를 이 둘로 남김없이 분할한다
#: (exhaustiveness 는 test_ 로 강제 -- 새 division 추가 시 한쪽에 넣지 않으면 테스트가 실패).
_PRICELESS_DIVISIONS = frozenset((
    "immediate_limit", "priority_limit", "midpoint", "pre_market_close", "post_market_close",
    "after_market_immediate_limit", "after_market_priority_limit",
    "gtp_immediate_limit", "gtp_priority_limit",
))
_LIMIT_BASED_DIVISIONS = frozenset((
    "conditional_limit", "after_market_limit", "gtp_limit",
))
#: KRX 애프터마켓(16:00~20:00) 주문구분 -- board="KRX" 전용.
_AFTERMARKET_DIVISIONS = frozenset((
    "after_market_limit", "after_market_immediate_limit", "after_market_priority_limit",
))
#: NXT 프리마켓 GTP(Good Till Pre-Market) 주문구분 -- board="NXT" 전용.
_GTP_DIVISIONS = frozenset(("gtp_limit", "gtp_immediate_limit", "gtp_priority_limit"))
#: 신용주문과 조합 가능한 division -- 애프터마켓/GTP 만(공지가 신용 ORD_DVSN 에 41~47·27~29 추가). 기존
#: 현금 전용 주문구분(조건부/최유리/최우선/중간가)은 신용과 조합 불가(종전대로).
_CREDIT_ALLOWED_DIVISIONS = _AFTERMARKET_DIVISIONS | _GTP_DIVISIONS
#: 거래 세션. ``regular`` 정규장, ``overnight`` 미국 오버나이트 거래(한국 낮 시간대 미국 종목 거래),
#: ``night`` KRX 파생(선물/옵션) 야간장. 세션이 다르면 서로 다른 주문이고 정정·취소 엔드포인트도
#: 다르므로 지문·라우팅으로 구분한다(미국 ``overnight`` 과 KRX ``night`` 은 별개의 세션이다).
Session = Literal["regular", "overnight", "night"]
#: 접수된 주문에 대한 변경 동작(정정/취소). 국내(``_domestic``)·해외(``_overseas``) 주문
#: 엔진이 공유하는 단일 타입 -- 두 엔진 모두 이 alias 를 import 한다(중복 정의 금지).
ChangeAction = Literal["cancel", "modify"]
#: 국내 신용주문 유형 코드(KIS 코드표). 매수/매도별로 유효 코드가 다르고(아래 상수), 신규/상환
#: 여부로 대출일자(LOAN_DT) 요구가 갈린다.
CreditType = Literal["21", "22", "23", "24", "25", "26", "27", "28"]


class OrderFingerprint:
    """주문 요청 지문(멱등 dedup 키)의 인메모리 태그드 유니온 베이스.

    KIS 는 네이티브 멱등키가 없어 이 지문이 "같은 id 를 *다른* 주문에 재사용했는지"를 판별한다.
    변형은 세 가지다 -- :class:`ImmediateOrderFingerprint`(즉시체결), :class:`ReservedOrderFingerprint`
    (예약), :class:`ChangeActionFingerprint`(정정/취소 동작). 각 변형은 자기 의미의 필드만 가져
    (예: 변경 지문의 ``action``, 예약 지문의 ``end_date``) **표현 불가능한 조합이 애초에 없다** --
    예전처럼 action 을 ``stop_price`` 슬롯에, cid 를 ``symbol`` 슬롯에 밀어넣는 스머글링을 없앴다.

    **온-디스크 형식은 뒤쪽으로만 자란다.** :func:`encode_fingerprint`/:func:`decode_fingerprint` 가
    기존 위치 튜플(현행 19-슬롯 문자열; 새 슬롯은 항상 뒤에 기본값과 함께 추가되고 decode 가 구
    레코드의 누락 슬롯을 채운다)과 왕복 코덱을 이룬다. dedup 정체성(무엇이 무엇과 같은가)은 그
    위치 인코딩의 동등성으로 정의되므로 변형이 달라도 **예전 튜플 동등성과 바이트 단위로 동일**하다 --
    재조회 매칭과 이중전송 장벽이 여기에 의존한다. 수치 필드는 와이어와 같은 정본
    문자열(:func:`format_wire_decimal`)이다."""

    if TYPE_CHECKING:
        # 세 변형이 모두 갖는 공통 필드의 타입만 베이스에 노출한다(각 dataclass 변형이 실제 선언;
        # 런타임 필드/슬롯 추가 없음). ``symbol``/``session`` 등 일부 변형에만 있는 필드는 제외한다.
        side: Side
        order_type: OrderType
        quantity: str
        limit_price: str
        exchange: str

    __slots__ = ()

    def _positional(self) -> tuple[str, ...]:
        """이 지문의 온-디스크 위치 인코딩(19-슬롯). 각 변형이 구현한다."""
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        # dedup 정체성 = 위치 인코딩의 동등성. 변형(type)이 달라도 인코딩이 같으면 같다 -- 실제로는
        # exchange 슬롯(action:/reserved/즉시)이 겹치지 않아 교차변형 충돌은 발생하지 않으며, 이
        # 정의는 예전 NamedTuple 튜플 동등성과 정확히 일치한다.
        if not isinstance(other, OrderFingerprint):
            return NotImplemented
        return self._positional() == other._positional()

    def __hash__(self) -> int:
        return hash(self._positional())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._positional()!r})"


@dataclass(frozen=True, slots=True, eq=False)
class ImmediateOrderFingerprint(OrderFingerprint):
    """즉시체결 주문(국내 현금/신용·해외 정규/주간)의 지문.

    ``credit_type`` 은 신용주문 유형(현금주문은 ``""``), ``loan_date`` 는 그 신용주문의 대출일자
    (현금주문·신규신용은 ``""``, 상환신용은 대상 대출일자) -- 같은 종목·수량·가격이라도 현금 vs
    신용, 또 서로 다른 대출을 상환하는 신용주문은 서로 다른 주문이므로 지문으로 구분해야
    replay/conflict 판정이 정확하다. ``division`` 은 국내 주문구분(최유리/최우선/조건부; 그 밖은 ``""``),
    ``board`` 는 국내 체결 보드(KRX/NXT/UN; 구버전 레코드는 기본 "KRX")로, 둘 다 같은 종목·수량이라도
    서로 다른 주문이라 지문으로 구분한다. ``derivative_item`` 은 파생(XKFE) 상품 구분("01" 선물 /
    "02" 콜옵션 / "03" 풋옵션; 현금·해외 주문은 ``""``)으로, 같은 심볼·수량·가격이라도 콜 vs 풋은
    서로 다른 주문이라 지문으로 구분한다. ``algo_strategy``/``algo_start``/``algo_end`` 는 미국주식
    알고리즘 분할주문(TWAP "twap" / VWAP "vwap"; 비-algo 는 ``""``)과 그 집행 시간창(HHMMSS, 정규장
    종료 집행이면 ``""``)으로, 같은 종목·수량·가격이라도 일반주문 vs TWAP vs VWAP, 또 서로 다른
    시간창은 서로 다른 주문이라 지문으로 구분한다."""

    symbol: str
    side: Side
    order_type: OrderType
    quantity: str
    limit_price: str
    stop_price: str
    time_in_force: TimeInForce
    exchange: str
    credit_type: str = ""
    loan_date: str = ""
    session: Session = "regular"
    division: str = ""
    board: str = "KRX"
    derivative_item: str = ""
    algo_strategy: str = ""
    algo_start: str = ""
    algo_end: str = ""

    def _positional(self) -> tuple[str, ...]:
        # 중간 ""/"HKD" = overseas_exchange/currency 슬롯(예약 변형 전용; 즉시 주문은 항상 기본값).
        # 뒤쪽 3 슬롯 = 미국주식 algo(전략/시작/종료; 비-algo 는 전부 "").
        return (
            self.symbol, self.side, self.order_type, self.quantity, self.limit_price,
            self.stop_price, self.time_in_force, self.exchange, self.credit_type,
            self.loan_date, self.session, self.division, self.board, self.derivative_item,
            "", "HKD", self.algo_strategy, self.algo_start, self.algo_end,
        )


@dataclass(frozen=True, slots=True, eq=False)
class ReservedOrderFingerprint(OrderFingerprint):
    """예약주문(국내 현금예약·해외 미국/아시아예약)의 지문 -- 예약은 지정가/시장가·day 뿐이라 stop/신용/
    세션/보드 슬롯이 없다. ``end_date`` 는 예약 유효 종료일(없으면 ``""``; 종료일이 다르면 다른 주문),
    ``exchange`` 는 예약 네임스페이스("reserved" 국내 / "overseas-reserved" 해외 미국 /
    "overseas-reserved-asia" 해외 아시아)로 즉시주문과 dedup 을 분리하고 reconcile 을 예약 경로로
    라우팅한다. ``overseas_exchange`` 는 발주 시점에 해석된 해외 거래소코드(예: "HKS"; 국내·미국 예약은
    ``""``) -- 아시아 취소가 여기서 ``OVRS_EXCG_CD``/``PRDT_TYPE_CD`` 를 재파생하며, 해석된 거래소가
    다르면 다른 주문이라 지문 정체성에 포함한다. ``currency`` 는 예약의 결제통화(홍콩(HKS) 예약의
    상품유형 선택 전용; 그 외는 기본 "HKD") -- CNY/USD 로 발주한 홍콩 예약의 취소가 같은
    ``PRDT_TYPE_CD`` 를 재현하려면 통화가 지문에 영속돼야 하고, 통화가 다르면 다른 주문이라 지문
    정체성에 포함한다. ``algo_strategy`` 는 미국 예약주문의 알고리즘 분할(TWAP "twap" / VWAP "vwap";
    비-algo 는 ``""``) -- 예약 algo 는 정규장 종료 집행 고정이라 시간창이 없고 전략만 지문 정체성에
    포함한다(같은 종목·수량·가격이라도 일반 예약 vs TWAP vs VWAP 는 서로 다른 주문)."""

    symbol: str
    side: Side
    order_type: OrderType
    quantity: str
    limit_price: str
    end_date: str
    exchange: str
    overseas_exchange: str = ""
    currency: str = "HKD"
    algo_strategy: str = ""

    def _positional(self) -> tuple[str, ...]:
        # 온-디스크: end_date 는 예전 stop_price 슬롯, tif="day" 고정, 중간은 기본값(overseas_exchange/
        # currency -- 해외 예약의 해석된 거래소·결제통화, v9). 뒤쪽 3 슬롯 = algo(전략; 예약은 시간창이
        # 없어 시작/종료는 항상 "").
        return (
            self.symbol, self.side, self.order_type, self.quantity, self.limit_price,
            self.end_date, "day", self.exchange, "", "", "regular", "", "KRX", "",
            self.overseas_exchange, self.currency, self.algo_strategy, "", "",
        )


@dataclass(frozen=True, slots=True, eq=False)
class ChangeActionFingerprint(OrderFingerprint):
    """접수된 주문에 대한 정정/취소 동작의 지문(변경 요청의 멱등키). ``original_client_order_id`` 로만
    원주문을 식별하고(그 order_id 로 파생하지 않는다 -- 정정은 원 id 를 새 ODNO 로 재바인딩하므로,
    order_id 를 넣으면 같은 request_id 재시도 시 재계산 값이 달라져 replay 가 CONFLICT 로 깨진다),
    ``action``(cancel/modify)·수량·가격이 변경 의도를 식별한다. ``exchange`` 는 원주문의 거래소(정직한
    값)이며, 온-디스크에선 "action:" 접두가 붙어 원주문 지문과 네임스페이스가 갈린다."""

    original_client_order_id: str
    side: Side
    order_type: OrderType
    quantity: str
    limit_price: str
    action: ChangeAction
    time_in_force: TimeInForce
    exchange: str

    def _positional(self) -> tuple[str, ...]:
        # 온-디스크: symbol 슬롯=원 cid, stop_price 슬롯=action, exchange 슬롯="action:"+원 거래소.
        # 중간 ""/"HKD" = overseas_exchange/currency 슬롯(예약 변형 전용; 변경 동작은 항상 기본값),
        # 뒤쪽 3 슬롯 = algo(변경 동작은 algo 를 싣지 않아 항상 "").
        return (
            self.original_client_order_id, self.side, self.order_type, self.quantity,
            self.limit_price, self.action, self.time_in_force, f"{_ACTION_EXCHANGE_PREFIX}{self.exchange}",
            "", "", "regular", "", "KRX", "", "", "HKD", "", "", "",
        )


#: 인메모리 지문 변형들의 공통 상위형 -- 저장소/재조회가 "어떤 지문이든" 을 annotate 할 때 쓴다.
Fingerprint = OrderFingerprint

#: 온-디스크 위치 인코딩의 슬롯 수(스키마 v10: 미국주식 algo 전략·시작·종료 슬롯 추가; v9 은 예약
#: overseas_exchange·currency, v8 은 파생 derivative_item). 인코딩은 항상 이 길이로 나간다. 구버전
#: (16-슬롯 이하) 레코드는 decode 가 뒤쪽 누락 슬롯을 기본값으로 채워 그대로 읽는다.
_FINGERPRINT_SLOTS = 19
#: 위치 인코딩의 정직한 예약 네임스페이스(exchange 슬롯). decode 가 이 값으로 예약 변형을 판별한다.
_RESERVED_EXCHANGES = frozenset(("reserved", "overseas-reserved", "overseas-reserved-asia"))
#: 변경 동작 지문의 exchange 슬롯 접두 -- decode 가 이 접두로 변경 변형을 판별한다.
_ACTION_EXCHANGE_PREFIX = "action:"
#: 위치 인코딩 뒤쪽 선택 슬롯(idx 8..18)의 기본값 -- 구버전 레코드가 이보다 짧을 때 채운다.
_TRAILING_DEFAULTS = ("", "", "regular", "", "KRX", "", "", "HKD", "", "", "")  # credit_type, loan_date, session, division, board, derivative_item, overseas_exchange, currency, algo_strategy, algo_start, algo_end


def encode_fingerprint(fingerprint: OrderFingerprint) -> list[str]:
    """지문을 온-디스크 위치 튜플(19-슬롯 문자열 리스트)로 인코딩한다 -- 기존 형식에서 뒤쪽 슬롯만
    자란 형태로, 앞 슬롯들은 변경 전 코드가 쓰던 ``list(fp)`` 와 **바이트 동일**해야 한다(dedup
    정체성·재조회 매칭·이중전송 장벽이 여기 의존)."""
    return list(fingerprint._positional())


def _checked_slot(value: str, allowed: frozenset[str], label: str) -> str:
    """지문 슬롯 값이 도메인 허용값에 드는지 확인하고 그대로 돌려준다. persistence 경계라 생성 시
    검증을 우회한 값(손상/변조된 온-디스크 레코드)을 슬롯 개수 검증과 같은 태도로 fail-closed 거부한다
    -- 통과한 값만 호출부에서 도메인 Literal 로 좁힌다(검증 없는 :func:`cast` 는 거짓 계약이다)."""
    if value not in allowed:
        raise ValueError(f"지문 {label} 슬롯이 손상됐다: {value!r}")
    return value


def decode_fingerprint(row: Sequence[object]) -> OrderFingerprint:
    """온-디스크 위치 튜플을 인메모리 지문으로 디코딩한다(구버전 짧은 레코드는 뒤쪽 기본값으로 채움 --
    v1=8슬롯 .. v5-v7=13슬롯, v8=14슬롯, v9=16슬롯, v10=19슬롯). exchange 슬롯(idx 7)으로 변형을 판별한다:
    "action:" 접두=변경 동작, "reserved"/"overseas-reserved"/"overseas-reserved-asia"=예약, 그 밖=즉시
    주문. 슬롯이 8 미만이거나 ``_FINGERPRINT_SLOTS``(19) 초과면 손상/변조로 거부한다(예전 ``Fingerprint
    (*fp)`` 가 필수 필드 부족/인자 과다로 실패하던 것과 같은 fail-closed)."""
    slots = [str(value) for value in row]
    if len(slots) < 8:
        raise ValueError(f"지문 레코드 슬롯이 부족하다(8 미만): {row!r}")
    while len(slots) < _FINGERPRINT_SLOTS:
        slots.append(_TRAILING_DEFAULTS[len(slots) - 8])
    if len(slots) > _FINGERPRINT_SLOTS:                    # 과다 슬롯 = 손상/변조 -> fail-closed
        raise ValueError(f"지문 레코드 슬롯이 과다하다({_FINGERPRINT_SLOTS} 초과): {row!r}")
    symbol, side, order_type, quantity, limit_price, stop_slot, tif, exchange = slots[:8]
    credit_type, loan_date, session, division, board, derivative_item, overseas_exchange, \
        currency, algo_strategy, algo_start, algo_end = slots[8:_FINGERPRINT_SLOTS]
    # 저장분은 전부 str 로 복원된다 -- persistence 경계에서 도메인 허용값인지 검증한 뒤 Literal 로 좁힌다
    # (검증 없이 cast 만 하면 손상/변조된 값을 유효 Literal 이라 거짓 단언하게 된다).
    side = cast(Side, _checked_slot(side, _SIDES, "side"))
    order_type = cast(OrderType, _checked_slot(order_type, _ORDER_TYPES, "order_type"))
    tif = cast(TimeInForce, _checked_slot(tif, _TIFS, "time_in_force"))
    if exchange.startswith(_ACTION_EXCHANGE_PREFIX):
        return ChangeActionFingerprint(
            original_client_order_id=symbol, side=side, order_type=order_type,
            quantity=quantity, limit_price=limit_price,
            action=cast(ChangeAction, _checked_slot(stop_slot, _CHANGE_ACTIONS, "action")),
            time_in_force=tif, exchange=exchange[len(_ACTION_EXCHANGE_PREFIX):],
        )
    if exchange in _RESERVED_EXCHANGES:
        # 예약 algo 는 시간창이 없어(정규장 종료 고정) algo_start/algo_end 슬롯은 항상 "" 다 -- 전략만 읽는다.
        return ReservedOrderFingerprint(
            symbol=symbol, side=side, order_type=order_type, quantity=quantity,
            limit_price=limit_price, end_date=stop_slot, exchange=exchange,
            overseas_exchange=overseas_exchange, currency=currency, algo_strategy=algo_strategy,
        )
    return ImmediateOrderFingerprint(
        symbol=symbol, side=side, order_type=order_type, quantity=quantity,
        limit_price=limit_price, stop_price=stop_slot, time_in_force=tif, exchange=exchange,
        credit_type=credit_type, loan_date=loan_date,
        session=cast(Session, _checked_slot(session, _SESSIONS, "session")),
        division=division, board=board, derivative_item=derivative_item,
        algo_strategy=algo_strategy, algo_start=algo_start, algo_end=algo_end,
    )


class WireRequest(NamedTuple):
    """주문 와이어 요청 -- 시장별 빌더가 조립해 안전 코어(place)에 넘긴다. 세 문자열이 서로
    바뀌어도 타입이 못 잡던 것을 이름으로 막는다."""

    method: str
    path: str
    tr_id: str
    body: dict[str, str]

_SIDES = frozenset(("buy", "sell"))
_ORDER_TYPES = frozenset(("market", "limit", "stop", "stop_limit"))
_TIFS = frozenset(("day", "gtc", "ioc", "fok"))
_NEEDS_LIMIT = frozenset(("limit", "stop_limit"))
_NEEDS_STOP = frozenset(("stop", "stop_limit"))

#: 신용주문 유형(국내, KIS 코드표). 매수/매도별로 유효한 코드가 다르다.
_CREDIT_BUY_TYPES = frozenset(("21", "23", "26", "28"))    # 자기융자신규/유통융자신규/유통대주상환/자기대주상환
_CREDIT_SELL_TYPES = frozenset(("22", "24", "25", "27"))   # 유통대주신규/자기대주신규/자기융자상환/유통융자상환
#: 신규(융자/대주 개시) vs 상환. 대출일자(LOAN_DT)는 신규면 개시일(오늘), 상환이면 대상 대출일자다.
_CREDIT_NEW_TYPES = frozenset(("21", "22", "23", "24"))    # 자기융자/유통대주/유통융자/자기대주 신규
_CREDIT_REPAY_TYPES = frozenset(("25", "26", "27", "28"))  # 자기융자/유통대주/유통융자/자기대주 상환
_SESSIONS = frozenset(("regular", "overnight", "night"))
_CHANGE_ACTIONS = frozenset(("cancel", "modify"))
#: 미국 오버나이트 거래 가능 거래소(시세 EXCD). 오버나이트 거래는 미국(NASD/NYSE/AMEX)만·지정가만. 여기서
#: 구성 시점 검증에 쓴다(Order 는 _overseas 를 import 못 해 목록을 직접 든다) -- _overseas/orders.py
#: `_ORDER_EXCHANGE` 의 US 그룹(market=="US")과 동일해야 하며, 와이어 빌더가 거기서 한 번 더 확인한다.
_OVERNIGHT_EXCHANGES = frozenset(("NAS", "NYS", "AMS"))
#: 미국주식 알고리즘(TWAP/VWAP) 분할주문 가능 거래소(시세 EXCD). algo 는 미국(NASD/NYSE/AMEX)만이라
#: 여기서 구성 시점에 검증한다 -- _overseas/orders.py `_ORDER_EXCHANGE` 의 US 그룹과 동일해야 하며,
#: 와이어 빌더가 거기서 한 번 더 확인한다(오버나이트와 같은 세 거래소지만 의미가 달라 별도 상수).
_ALGO_EXCHANGES = frozenset(("NAS", "NYS", "AMS"))
#: 유효한 algo 전략 값(빈 문자열=비-algo). Order/CLI 가 구성 시점에 이 집합으로 좁힌다.
_ALGO_STRATEGIES = frozenset(("twap", "vwap"))
#: 국내 거래소(MIC). ``division``(국내 주문구분)은 이 거래소에서만 유효하다. _domestic/orders.py
#: `_DOMESTIC_MICS`/`_EXCHANGE_ID` 와 일치해야 한다.
_DOMESTIC_EXCHANGES = frozenset(("XKRX", "XKOS", "NXTE"))
#: KRX 파생상품(선물/옵션) 거래소 MIC. 파생 주문은 현금주문과 같은 Order/지문/스토어를 재사용하되
#: 이 exchange 로 라우팅을 가른다(현금 국내주문과 별개의 와이어 빌더로). ``division`` 은 이 거래소에서도
#: 유효하되 최우선지정가(priority_limit)는 파생에 없다.
_DERIVATIVE_EXCHANGE = "XKFE"
#: 국내 장내채권 주문의 라우팅 마커. 채권은 표준코드(ISIN)로 거래하고 KRX MIC 가 따로 없어, 현금·
#: 파생과 같은 Order/지문/스토어를 재사용하되 이 exchange 값으로 채권 와이어 빌더로 라우팅을 가른다.
#: 온-디스크 지문에선 기존 exchange 슬롯의 한 값(새 슬롯·스키마 증가 없음)이라 기존 주문 지문과
#: 바이트 호환을 유지한다.
_BOND_EXCHANGE = "BOND"
#: 해외선물옵션(08) 주문의 라우팅 마커. 해외 파생은 통화별로 유일한 종목코드(OVRS_FUTR_FX_PDNO,
#: 예: "6BZ22")로 거래하고 국내 거래소 MIC 가 없어, 현금·파생·채권과 같은 Order/지문/스토어를
#: 재사용하되 이 exchange 값으로 해외선물옵션 와이어 빌더로 라우팅을 가른다. 온-디스크 지문에선
#: 기존 exchange 슬롯의 한 값(새 슬롯·스키마 증가 없음)이라 기존 주문 지문과 바이트 호환을 유지한다.
_OVERSEAS_FO_EXCHANGE = "OSFO"
#: 국내 보드별 **미지원** 주문구분 base(= division 있으면 그것, 없으면 order_type)의 blocklist.
#: 보드마다 제외하는 주문구분 집합이 다르다: 장전/장후 시간외 종가는 KRX 전용, 애프터마켓(41~47)은 KRX
#: 전용(NXT/UN 제외), GTP(27~29)는 NXT 전용(KRX/UN 제외). 여기 없는 base(stop 등 Tier 2/미매핑)는 이
#: 검증이 아니라 와이어 빌더에서 판정한다. 구체 집합은 아래 리터럴이 원장이다.
_BOARD_UNSUPPORTED_BASES = {
    "KRX": frozenset(("gtp_limit", "gtp_immediate_limit", "gtp_priority_limit")),
    "NXT": frozenset(("market", "conditional_limit",
                      "pre_market_close", "post_market_close", "stop_limit",
                      "after_market_limit", "after_market_immediate_limit", "after_market_priority_limit")),
    "UN": frozenset(("conditional_limit",
                     "pre_market_close", "post_market_close", "stop_limit",
                     "after_market_limit", "after_market_immediate_limit", "after_market_priority_limit",
                     "gtp_limit", "gtp_immediate_limit", "gtp_priority_limit")),
}
#: 유효한 국내 보드 값. 알 수 없는 board 는 와이어 빌더의 _BOARD_EXCG KeyError 전에 생성 시점 거부.
_DOMESTIC_BOARDS = frozenset(("KRX", "NXT", "UN"))

_KST = timezone(timedelta(hours=9))


def mint_client_order_id() -> str:
    """거래일 내 유일한 ``client_order_id`` 를 발행한다(전송 전, 멱등키).

    날짜(KST) + UUID 조각으로 만들어 날짜를 넘겨도 충돌하지 않게 한다(FIX 권고). KIS는
    네이티브 멱등키가 없으므로 이 id가 유일한 중복-방지 수단이다.
    """
    return f"{datetime.now(_KST):%Y%m%d}-{uuid.uuid4().hex[:16]}"


def coerce_decimal(value: object, name: str) -> Decimal:
    """사용자 입력 수치를 Decimal 로 -- 파싱 실패는 :class:`KISUsageError`(사용자 오류). 주문 계층
    공용(즉시/신용/예약 주문의 수량·단가 강제변환에 함께 쓴다).

    ``Decimal(str(value))`` 는 ``"nan"``/``"inf"``(및 ``float('nan')``/``float('inf')``)도 유효한
    Decimal(비유한값)으로 받아들이므로, 그대로 두면 지문·와이어에 NaN/Infinity 가 실려 재확인·비교가
    깨진다. 파싱 뒤 :meth:`Decimal.is_finite` 로 비유한값을 fail-closed 로 거부한다."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise KISUsageError(f"{name} 는 숫자여야 한다: {value!r}") from err
    if not number.is_finite():
        raise KISUsageError(f"{name} 는 유한한 숫자여야 한다(NaN/Infinity 불가): {value!r}")
    return number


def validate_yyyymmdd(value: str, field_name: str) -> None:
    """``field_name`` 이 실재하는 YYYYMMDD 날짜인지 확인 -- **정확히 8자리 ASCII 숫자**여야 하고
    (7/9자리·공백·비-ASCII 숫자는 거부), 형식만 맞고 불가능한 날짜(20261399 등)도 거부한다. 주문
    계층 공용(신용 loan_date, 예약 end_date, 예약주문조회 기간 등).

    ``str.isdigit`` 은 위첨자·전각 숫자도 참이라 ``str.isascii`` 와 함께 봐 ASCII 0-9 만 허용한다
    (``strptime`` 은 일부 유니코드 숫자·가변폭 매칭을 관용해 8자리가 아닌 입력을 통과시킬 수 있다)."""
    if not (isinstance(value, str) and len(value) == 8 and value.isascii() and value.isdigit()):
        raise KISUsageError(f"{field_name} 는 YYYYMMDD 8자리 숫자여야 한다: {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d")  # noqa: DTZ007 -- 날짜 유효성만 확인
    except ValueError as err:
        raise KISUsageError(f"{field_name} 는 실재하는 YYYYMMDD 날짜여야 한다: {value!r}") from err


def validate_hhmmss(value: str, field_name: str) -> None:
    """``field_name`` 이 실재하는 HHMMSS 시각인지 확인 -- **정확히 6자리 ASCII 숫자**여야 하고
    (5/7자리·공백·비-ASCII 숫자는 거부), 형식만 맞고 불가능한 시각(256000 등)도 거부한다. 미국주식
    algo 분할주문의 집행 시간창(START_TIME/END_TIME)에 쓴다.

    ``str.isdigit`` 은 위첨자·전각 숫자도 참이라 ``str.isascii`` 와 함께 봐 ASCII 0-9 만 허용한다."""
    if not (isinstance(value, str) and len(value) == 6 and value.isascii() and value.isdigit()):
        raise KISUsageError(f"{field_name} 는 HHMMSS 6자리 숫자여야 한다: {value!r}")
    hour, minute, second = int(value[0:2]), int(value[2:4]), int(value[4:6])
    if hour > 23 or minute > 59 or second > 59:
        raise KISUsageError(f"{field_name} 는 실재하는 HHMMSS 시각여야 한다: {value!r}")


def resolve_new_credit_loan_date(
    credit_type: CreditType | None, loan_date: str | None, *, today: str
) -> str | None:
    """신규(상환 아님) 신용주문의 대출일자 미지정 시 개시일 ``today`` 로 채운다 -- 상환/이미 지정/현금은
    그대로 둔다. ``today`` 는 YYYYMMDD 문자열(엣지의 :func:`~kis_trader._internal._datetime._today_kst`
    결과). 시계 의존을 엣지(facade)에 가두려 주입받는 순수 함수다(:class:`Order` 생성자는 loan_date 같은
    지문 필드를 시계로 짓지 않는다). 상환유형은 loan_date 필수라 None 이어도 여기서 채우지 않고
    :class:`Order` 가 거부한다."""
    if credit_type is not None and credit_type not in _CREDIT_REPAY_TYPES and loan_date is None:
        return today
    return loan_date


def reject_bad_change_price_shape(action: ChangeAction, limit_price: Decimal | None) -> None:
    """지정가 원주문 정정취소의 가격형상 검증 -- 지정가 전용 자산(주식/해외)이 공유한다.
    정정=>0보다 큰 limit_price, 취소=>limit_price 없음. 파생은 자체 규칙이라 이걸 쓰지 않는다."""
    if action == "modify" and (limit_price is None or limit_price <= 0):
        raise KISUsageError("정정 주문에는 0보다 큰 limit_price가 필요하다.")
    if action == "cancel" and limit_price is not None:
        raise KISUsageError("취소 주문에는 limit_price를 지정할 수 없다.")


@dataclass(frozen=True, slots=True, kw_only=True)
class Order:
    """한 건의 주문 요청(불변).

    직접 만들기보다 타입별 생성자를 쓰는 것을 권한다. ``client_order_id`` 는 지정하지
    않으면 자동 발행된다.
    """

    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = "day"
    exchange: str = "XKRX"
    credit_type: CreditType | None = None
    loan_date: str | None = None
    session: Session = "regular"
    division: DomesticDivision | None = None
    board: DomesticBoard = "KRX"
    #: 파생(XKFE) 상품 구분 -- "01" 선물 / "02" 콜옵션 / "03" 풋옵션. 현금·해외 주문은 ``""``.
    #: 콜 vs 풋은 같은 심볼·수량·가격이라도 서로 다른 주문이라 지문에 함께 실어 dedup 을 가른다.
    derivative_item: DerivativeItem = ""
    #: 국내 장내채권(BOND) 매도가 지목하는 매수 lot -- 매수일자(BUY_DT, YYYYMMDD)와 매수순번(BUY_SEQ).
    #: 채권 잔고는 종목이 아니라 매수 단위(buy_date+buy_sequence)로 쪼개져 있어, 같은 종목·수량·가격이라도
    #: 서로 다른 lot 의 매도는 서로 다른 주문이다. 이 둘을 지문에 실어야 dedup 장벽이 둘째 매도를
    #: 오차단하지 않는다. 새 지문 슬롯을 만들지 않으려고 기존 loan_date 슬롯(BUY_DT)·derivative_item
    #: 슬롯(BUY_SEQ)을 재사용한다(:meth:`fingerprint` 참고) -- 채권 매수·기타 주문은 둘 다 "" 라
    #: 온-디스크 인코딩이 바이트 동일하다. 채권 매수·기타 주문은 항상 "".
    bond_buy_date: str = ""
    bond_buy_seq: str = ""
    #: 미국주식 알고리즘 분할주문 전략(TWAP/VWAP; 비-algo 는 ``None``). 미국(NAS/NYS/AMS) 지정가
    #: 전용이며 ``ORD_DVSN`` 35/36 으로 나간다. 같은 종목·수량·가격이라도 일반주문 vs TWAP vs VWAP 는
    #: 서로 다른 주문이라 지문에 함께 실어 dedup 을 가른다.
    algo_strategy: AlgoStrategy | None = None
    #: algo 집행 시간창(KST HHMMSS, 같은 날 시작<종료). 둘 다 있으면 ``ALGO_ORD_TMD_DVSN_CD`` "00"
    #: (직접입력)로 START_TIME/END_TIME 을 싣고, 둘 다 비면 "02"(정규장 종료까지 집행). algo 가 아니면 항상 "".
    algo_start: str = ""
    algo_end: str = ""
    client_order_id: str = field(default_factory=mint_client_order_id)

    def __post_init__(self) -> None:
        # 어떤 생성 경로(직접 Order(...) 포함)로도 가격/수량이 Decimal 이 되게 강제한다.
        # frozen 이라 object.__setattr__ 로 다시 쓴다.
        object.__setattr__(self, "quantity", coerce_decimal(self.quantity, "quantity"))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", coerce_decimal(self.limit_price, "limit_price"))
        if self.stop_price is not None:
            object.__setattr__(self, "stop_price", coerce_decimal(self.stop_price, "stop_price"))

        if self.side not in _SIDES:
            raise KISUsageError(f"side 는 buy/sell 중 하나여야 한다: {self.side!r}")
        if self.order_type not in _ORDER_TYPES:
            raise KISUsageError(f"지원하지 않는 order_type: {self.order_type!r}")
        if self.time_in_force not in _TIFS:
            raise KISUsageError(f"지원하지 않는 time_in_force: {self.time_in_force!r}")
        if self.quantity <= 0:
            raise KISUsageError(f"quantity 는 0보다 커야 한다: {self.quantity}")

        needs_limit = self.order_type in _NEEDS_LIMIT
        needs_stop = self.order_type in _NEEDS_STOP
        # 타입 -> 가격 의존성(FIX): 필요한 가격은 있어야, 불필요한 가격은 없어야 한다.
        if needs_limit and self.limit_price is None:
            raise KISUsageError(f"{self.order_type} 주문은 limit_price 가 필요하다")
        if not needs_limit and self.limit_price is not None:
            raise KISUsageError(f"{self.order_type} 주문에는 limit_price 를 줄 수 없다")
        if needs_stop and self.stop_price is None:
            raise KISUsageError(f"{self.order_type} 주문은 stop_price 가 필요하다")
        if not needs_stop and self.stop_price is not None:
            raise KISUsageError(f"{self.order_type} 주문에는 stop_price 를 줄 수 없다")
        if self.limit_price is not None and self.limit_price <= 0:
            raise KISUsageError(f"limit_price 는 0보다 커야 한다: {self.limit_price}")
        if self.stop_price is not None and self.stop_price <= 0:
            raise KISUsageError(f"stop_price 는 0보다 커야 한다: {self.stop_price}")

        # loan_date 는 신용주문에서만 의미 있다(현금주문에 대출일자를 주면 표현 불가능한 상태).
        if self.loan_date is not None and self.credit_type is None:
            raise KISUsageError("loan_date 는 신용주문(credit_type)에만 줄 수 있다.")
        if self.loan_date is not None:
            validate_yyyymmdd(self.loan_date, "loan_date")
        if self.credit_type is not None:
            # 신용주문은 국내(KRX) 마진 전용 -- 해외 거래소와 조합하면 라우팅이 해외 빌더로 새어
            # 신용 의미가 조용히 사라진다. 생성 시점에 fail-closed.
            if self.exchange != "XKRX":
                raise KISUsageError(
                    f"신용주문은 국내(XKRX)만 지원한다 -- credit_type 과 exchange={self.exchange!r} 는 "
                    f"조합할 수 없다."
                )
            # 매수/매도별 유효 코드가 다르다 -- 반대 side 코드를 조용히 통과시키지 않는다(잘못된
            # 신용 종류로 체결되면 상환·이자 구조가 달라진다).
            valid = _CREDIT_BUY_TYPES if self.side == "buy" else _CREDIT_SELL_TYPES
            if self.credit_type not in valid:
                raise KISUsageError(
                    f"{self.side} 신용주문의 credit_type 은 {sorted(valid)} 중 하나여야 한다: "
                    f"{self.credit_type!r}"
                )
            # 신규/상환 모두 loan_date 필수 -- Order 생성자는 loan_date(지문 필드)를 시계로 짓지 않는다
            # (생성 시각에 따라 지문이 바뀌면 KST 자정 경계 replay 가 dedup 을 못 한다). 상환은 대상 대출을,
            # 신규는 개시일을 지정해야 하며, 신규 개시일 기본값(오늘 KST)은 엣지 facade(credit_buy/
            # credit_sell)가 resolve_new_credit_loan_date 로 채운 뒤 넘긴다.
            if self.credit_type in _CREDIT_REPAY_TYPES:
                if self.loan_date is None:
                    raise KISUsageError(
                        "상환 신용주문(credit_type 25/26/27/28)은 대상 대출의 loan_date(YYYYMMDD)가 "
                        "필요하다."
                    )
            elif self.loan_date is None:  # 신규 신용 -- 개시일 필수(facade 가 오늘로 채운다)
                raise KISUsageError(
                    "신규 신용주문(credit_type 21/22/23/24)은 개시일 loan_date(YYYYMMDD)가 필요하다 -- "
                    "credit_buy/credit_sell 가 전송 시 오늘(KST)로 채운다."
                )

        if self.session not in _SESSIONS:
            raise KISUsageError(f"지원하지 않는 session: {self.session!r}")
        # KRX 파생(XKFE)은 night(야간장)만·미국 overnight 과 별개다 -- overnight 은 미국 전용이라
        # XKFE 와 조합하면 fail-closed(아래 overnight 블록의 미국 거래소 검증도 XKFE 를 거부하지만,
        # 여기서 파생 관점의 명시적 메시지를 준다).
        if self.exchange == _DERIVATIVE_EXCHANGE and self.session == "overnight":
            raise KISUsageError("XKFE(파생) 주문은 night 세션만 유효하다(overnight 은 미국 전용).")
        # night(야간장)은 KRX 파생(XKFE) 전용 세션이다 -- 다른 거래소와 조합하면(예: KRX 주식 XKRX +
        # night) 라우팅이 어긋나 의도와 다른 주문이 나갈 수 있어, overnight 의 미국 거래소 검증과
        # 대칭으로 생성 시점에 fail-closed.
        if self.session == "night" and self.exchange != _DERIVATIVE_EXCHANGE:
            raise KISUsageError(
                f"night 세션은 파생(XKFE) 전용이다 -- exchange={self.exchange!r} 와 조합할 수 없다."
            )
        # 야간(STTN) 발주·조회 와이어는 상품구분(FUOP_ITEM_DVSN_CD)이 필수라 derivative_item 이
        # 비면 나갈 수 없다. 공개 핸들은 항상 채우므로 이 검사는 raw 생성의 fail-open 만 닫는다.
        if self.session == "night" and not self.derivative_item:
            raise KISUsageError("파생 야간 주문에는 종목구분(derivative_item)이 필요하다")
        if self.session == "overnight":
            # 미국 오버나이트 거래는 미국(NASD/NYSE/AMEX)만·지정가만 -- 그 밖은 생성 시점에 fail-closed.
            if self.exchange not in _OVERNIGHT_EXCHANGES:
                raise KISUsageError(
                    f"미국 오버나이트 거래(session='overnight')는 미국 거래소만 지원한다 "
                    f"({'/'.join(sorted(_OVERNIGHT_EXCHANGES))}): exchange={self.exchange!r}"
                )
            if self.order_type != "limit":
                raise KISUsageError("미국 오버나이트 거래는 지정가만 지원한다(price 를 지정하라).")
            if self.time_in_force != "day":
                # 주간 와이어엔 TIF 필드가 없어 조용히 day 로 나간다 -- 정규 해외주문처럼 fail-closed
                # (그렇지 않으면 지문의 TIF 와 실제 전송이 어긋난다).
                raise KISUsageError(
                    f"미국 오버나이트 거래는 time_in_force='day' 만 지원한다: {self.time_in_force!r}"
                )
            if self.credit_type is not None:
                raise KISUsageError("미국 오버나이트 거래는 신용주문과 조합할 수 없다.")

        # division(주문구분: 최유리/최우선/조건부)은 국내 현금주문과 파생(XKFE) 전용 -- 그 밖의 해외
        # 거래소·신용·미국 오버나이트와 조합하면 라우팅이 어긋나 의도와 다른 주문이 나갈 수 있어,
        # 생성 시점에 fail-closed. 파생에는 최우선지정가(priority_limit)가 없다.
        if self.division is not None:
            if self.exchange not in _DOMESTIC_EXCHANGES and self.exchange != _DERIVATIVE_EXCHANGE:
                raise KISUsageError(
                    f"division(주문구분)은 국내 현금주문·파생(XKFE) 전용이다 -- exchange={self.exchange!r} "
                    f"와 조합할 수 없다."
                )
            if self.exchange == _DERIVATIVE_EXCHANGE and self.division not in _DERIVATIVE_DIVISIONS:
                raise KISUsageError(
                    f"파생(XKFE) 주문은 이 주문구분을 지원하지 않는다(division={self.division!r}); "
                    f"조건부지정가·최유리지정가만 가능하다."
                )
            if self.credit_type is not None and self.division not in _CREDIT_ALLOWED_DIVISIONS:
                raise KISUsageError(
                    "division 은 신용주문과 조합할 수 없다(애프터마켓/GTP 주문구분만 예외)."
                )
            if self.session == "overnight":
                raise KISUsageError("division 은 미국 오버나이트 거래와 조합할 수 없다.")
            # division<->order_type<->price 결합을 DATA 경계에서 강제한다 -- 최유리/최우선은 시장이 가격을
            # 정하는 가격없는 시장가 기반, 조건부는 지정가 기반. 이 결합이 없으면 Order.market/limit 생성자로
            # 잘못된 조합이 만들어져 와이어에 조용히 틀린 가격(또는 price 0)이 나간다(fail-open).
            if self.division in _PRICELESS_DIVISIONS:
                if self.order_type != "market" or self.limit_price is not None:
                    raise KISUsageError(
                        f"{self.division} 은 시장이 가격을 정하므로 가격 없는 시장가 기반이어야 한다."
                    )
            elif self.division in _LIMIT_BASED_DIVISIONS:
                if self.order_type != "limit":
                    raise KISUsageError(
                        f"{self.division} 은 지정가(limit) 기반이어야 한다(limit_price 필요)."
                    )
            else:
                # DomesticDivision Literal 에 멤버를 추가하면서 위 두 집합 중 한쪽에 분류하지
                # 않으면 가격 결합 규칙 없이 조용히 통과한다 -- fail-closed 로 막는다(방어적).
                raise KISUsageError(
                    f"division {self.division!r} 의 가격 결합 규칙이 정의되지 않았다"
                )

        # 장내채권 매도 lot 지목(BUY_DT/BUY_SEQ)은 채권(BOND) **매도** 전용이다 -- 다른 거래소나
        # 채권 매수에 실리면 재사용 지문 슬롯(loan_date/derivative_item)을 오염시켜 무관한 주문의 dedup
        # 정체성을 바꾸므로 생성 시점에 fail-closed. 채권 매도는 둘 다 있어야(부분 lot 은 무의미) 하고,
        # BUY_DT 는 실재하는 YYYYMMDD 여야 한다.
        is_bond = self.exchange == _BOND_EXCHANGE
        if self.bond_buy_date or self.bond_buy_seq:
            if not (is_bond and self.side == "sell"):
                raise KISUsageError(
                    "bond_buy_date/bond_buy_seq 는 장내채권(BOND) 매도 주문에만 줄 수 있다."
                )
            if not (self.bond_buy_date and self.bond_buy_seq):
                raise KISUsageError(
                    "장내채권 매도는 매수 lot 의 bond_buy_date 와 bond_buy_seq 가 모두 필요하다."
                )
        if self.bond_buy_date:
            validate_yyyymmdd(self.bond_buy_date, "bond_buy_date")
        # 채권 지문은 매수 lot 을 loan_date 슬롯(BUY_DT)·derivative_item 슬롯(BUY_SEQ)에 재사용한다 --
        # BOND 주문이 그 필드를 독립적으로 실으면(신용/파생 의미) 지문 정체성이 겹쳐, 서로 다른 lot 의
        # 매도가 한 지문으로 붕괴해 둘째 매도를 오차단하는 collision 이 난다. BOND 는 credit_type/loan_date/
        # derivative_item 를 가질 수 없다(생성 시점 fail-closed로 그 슬롯을 순수하게 lot 전용으로 남긴다).
        if is_bond and (self.derivative_item or self.loan_date or self.credit_type):
            raise KISUsageError(
                "장내채권(BOND) 주문은 credit_type/loan_date/derivative_item 를 가질 수 없다."
            )

        if self.board not in _DOMESTIC_BOARDS:
            raise KISUsageError(f"지원하지 않는 board: {self.board!r} (KRX/NXT/UN).")
        # 보드별 미지원 주문구분 -- 국내 주문에만. 보드마다 제외 집합이 다르므로 와이어 전(생성 시점)에
        # fail-closed. base = division 있으면 그것, 없으면 order_type.
        if self.exchange in _DOMESTIC_EXCHANGES:
            base = self.division or self.order_type
            if base in _BOARD_UNSUPPORTED_BASES.get(self.board, frozenset()):
                raise KISUsageError(
                    f"{self.board} 보드는 이 주문구분을 지원하지 않는다(base={base!r})."
                )

        # 미국주식 algo(TWAP/VWAP) 분할주문 -- 미국(NAS/NYS/AMS) 지정가·정규세션 전용이라 그 밖은
        # 생성 시점에 fail-closed(신용/파생/채권/국내 division 은 위 블록들이 이미 거부하지만, 명시적
        # 메시지를 준다). 시간창(algo_start/algo_end)은 둘 다 있거나 둘 다 없어야 하고(있으면 START/
        # END 로 나가는 직접입력, 없으면 정규장 종료 집행), 있으면 실재 HHMMSS 이고 start < end 여야 한다.
        if self.algo_strategy is not None:
            if self.algo_strategy not in _ALGO_STRATEGIES:
                raise KISUsageError(
                    f"지원하지 않는 algo_strategy: {self.algo_strategy!r} (twap/vwap)."
                )
            if self.exchange not in _ALGO_EXCHANGES:
                raise KISUsageError(
                    f"미국주식 algo(TWAP/VWAP)는 미국 거래소만 지원한다 "
                    f"({'/'.join(sorted(_ALGO_EXCHANGES))}): exchange={self.exchange!r}"
                )
            if self.order_type != "limit":
                raise KISUsageError("미국주식 algo(TWAP/VWAP)는 지정가만 지원한다(limit_price 를 지정하라).")
            if self.time_in_force != "day":
                raise KISUsageError(
                    f"미국주식 algo(TWAP/VWAP)는 time_in_force='day' 만 지원한다: {self.time_in_force!r}"
                )
            if self.session != "regular":
                raise KISUsageError(
                    f"미국주식 algo(TWAP/VWAP)는 정규 세션 전용이다 -- session={self.session!r} 와 "
                    f"조합할 수 없다."
                )
            if self.credit_type is not None or self.division is not None:
                raise KISUsageError("미국주식 algo(TWAP/VWAP)는 신용주문·주문구분(division)과 조합할 수 없다.")
            # 시간창: 둘 다 있거나 둘 다 없음(반쪽은 표현 불가능한 상태).
            if bool(self.algo_start) != bool(self.algo_end):
                raise KISUsageError(
                    "algo 시간창은 시작(algo_start)과 종료(algo_end)를 둘 다 주거나 둘 다 비워야 한다"
                    "(정규장 종료 집행)."
                )
            if self.algo_start:
                validate_hhmmss(self.algo_start, "algo_start")
                validate_hhmmss(self.algo_end, "algo_end")
                if self.algo_start >= self.algo_end:
                    raise KISUsageError(
                        f"algo 시간창은 시작이 종료보다 앞서야 한다: {self.algo_start} >= {self.algo_end}"
                    )
        elif self.algo_start or self.algo_end:
            # 전략 없이 시간창만 주는 것은 표현 불가능한 상태(무엇을 분할할지 없음).
            raise KISUsageError("algo 시간창(algo_start/algo_end)은 algo_strategy 없이는 줄 수 없다.")

    @property
    def fingerprint(self) -> ImmediateOrderFingerprint:
        """이 주문의 요청 지문 -- 같은 ``client_order_id`` 를 *다른* 주문에 재사용했는지
        판별하는 데 쓴다(멱등 replay 는 지문이 같을 때만 허용). 수치는 와이어와 **같은**
        정본(:func:`format_wire_decimal`)으로 -- 그래야 ``10`` 과 ``1E1`` 이 같은 지문이 된다.
        """
        return ImmediateOrderFingerprint(
            symbol=self.symbol, side=self.side, order_type=self.order_type,
            quantity=format_wire_decimal(self.quantity),
            limit_price="" if self.limit_price is None else format_wire_decimal(self.limit_price),
            stop_price="" if self.stop_price is None else format_wire_decimal(self.stop_price),
            time_in_force=self.time_in_force, exchange=self.exchange,
            credit_type=self.credit_type or "",
            # 채권 매도의 매수 lot 을 기존 슬롯에 재사용해 싣는다(새 슬롯 없이 lot 별 dedup): BUY_DT 는
            # loan_date 슬롯, BUY_SEQ 는 derivative_item 슬롯. 신용주문(loan_date)·파생(derivative_item)과
            # 채권 매도는 상호 배타적이라(exchange 로 갈림) 값이 섞이지 않는다.
            loan_date=self.loan_date or self.bond_buy_date,
            session=self.session, division=self.division or "", board=self.board,
            derivative_item=self.derivative_item or self.bond_buy_seq,
            algo_strategy=self.algo_strategy or "",
            algo_start=self.algo_start, algo_end=self.algo_end,
        )

    # --- 타입별 생성자(권장 진입점) ------------------------------------
    @classmethod
    def _make(
        cls, symbol: str, side: Side, order_type: OrderType, quantity: Numeric, *,
        limit_price: Numeric | None = None, stop_price: Numeric | None = None,
        time_in_force: TimeInForce = "day", exchange: str = "XKRX",
        credit_type: CreditType | None = None, loan_date: str | None = None,
        session: Session = "regular", division: DomesticDivision | None = None,
        board: DomesticBoard = "KRX",
        algo_strategy: AlgoStrategy | None = None, algo_start: str = "", algo_end: str = "",
        client_order_id: str | None = None,
    ) -> Order:
        quantity_dec = coerce_decimal(quantity, "quantity")
        limit_dec = None if limit_price is None else coerce_decimal(limit_price, "limit_price")
        stop_dec = None if stop_price is None else coerce_decimal(stop_price, "stop_price")
        # client_order_id 를 안 주면 필드의 default_factory 가 발행하도록 아예 넘기지 않는다
        # (None 을 넘기면 factory 를 덮어써 멱등키가 사라진다).
        if client_order_id is None:
            return cls(
                symbol=symbol, side=side, order_type=order_type, quantity=quantity_dec,
                limit_price=limit_dec, stop_price=stop_dec,
                time_in_force=time_in_force, exchange=exchange,
                credit_type=credit_type, loan_date=loan_date, session=session,
                division=division, board=board,
                algo_strategy=algo_strategy, algo_start=algo_start, algo_end=algo_end,
            )
        return cls(
            symbol=symbol, side=side, order_type=order_type, quantity=quantity_dec,
            limit_price=limit_dec, stop_price=stop_dec,
            time_in_force=time_in_force, exchange=exchange,
            credit_type=credit_type, loan_date=loan_date, session=session,
            division=division, board=board,
            algo_strategy=algo_strategy, algo_start=algo_start, algo_end=algo_end,
            client_order_id=client_order_id,
        )

    @classmethod
    def credit(
        cls, symbol: str, *, side: Side, quantity: Numeric, credit_type: CreditType,
        limit_price: Numeric | None = None, loan_date: str | None = None,
        time_in_force: TimeInForce = "day", division: DomesticDivision | None = None,
        board: DomesticBoard = "KRX", client_order_id: str | None = None,
    ) -> Order:
        """국내 신용(융자/대주) 주문 -- ``limit_price`` 를 주면 지정가, 없으면 시장가. ``credit_type`` 은
        매수/매도별 신용유형(매수 21/23/26/28, 매도 22/24/25/27).

        ``loan_date``(YYYYMMDD)는 대출일자다: **상환**유형(25/26/27/28)은 상환 대상 대출을, **신규**유형
        (21/22/23/24)은 개시일을 지정해야 하며 **둘 다 필수**다(:class:`Order` 생성자는 loan_date 를 시계로
        채우지 않는다). 상위 주문 경로(:meth:`~kis_trader.domestic.stock.DomesticStock.credit_buy`/
        ``credit_sell``)가 신규 생략 시 오늘(KST)로 채워 주므로, 직접 :meth:`credit` 을 쓸 때만 명시한다.
        ``division`` 은 애프터마켓(``after_market_*``, board="KRX")/GTP(``gtp_*``, board="NXT") 주문구분만
        신용과 조합할 수 있다(기타 현금 전용 주문구분은 __post_init__ 이 거부). 애프터마켓 지정가/최유리는
        ``time_in_force`` 로 IOC/FOK 조합 가능, 그 외 division 은 day 만. 일반 신용은 국내(XKRX)만."""
        order_type: OrderType = "limit" if limit_price is not None else "market"
        return cls._make(
            symbol, side, order_type, quantity, limit_price=limit_price,
            credit_type=credit_type, loan_date=loan_date, division=division, board=board,
            time_in_force=time_in_force, client_order_id=client_order_id,
        )

    @classmethod
    def market(cls, symbol: str, *, side: Side, quantity: Numeric,
               time_in_force: TimeInForce = "day", exchange: str = "XKRX",
               division: DomesticDivision | None = None, board: DomesticBoard = "KRX",
               client_order_id: str | None = None) -> Order:
        """시장가 주문. ``division`` 은 국내 현금주문 전용 주문구분(최유리/최우선 등, 가격 없음).
        ``board`` 는 체결 보드(KRX/NXT/UN=SOR)."""
        return cls._make(symbol, side, "market", quantity, time_in_force=time_in_force,
                          exchange=exchange, division=division, board=board,
                          client_order_id=client_order_id)

    @classmethod
    def limit(cls, symbol: str, *, side: Side, quantity: Numeric, limit_price: Numeric,
              time_in_force: TimeInForce = "day", exchange: str = "XKRX",
              session: Session = "regular", division: DomesticDivision | None = None,
              board: DomesticBoard = "KRX",
              algo_strategy: AlgoStrategy | None = None, algo_start: str = "", algo_end: str = "",
              client_order_id: str | None = None) -> Order:
        """지정가 주문. ``session='overnight'`` 은 미국 오버나이트 거래(미국 종목만). ``division`` 은 국내
        현금주문 전용 주문구분(조건부지정가 등, 가격 필요). ``board`` 는 체결 보드(KRX/NXT/UN=SOR).
        ``algo_strategy``(twap/vwap)는 미국주식 알고리즘 분할주문으로, ``algo_start``/``algo_end``(**KST**
        HHMMSS, 같은 날 시작<종료·자정 넘김 불가)를 주면 그 시간창에, 비우면 정규장 종료까지 집행한다
        (미국 NAS/NYS/AMS 전용; 미국 정규장은 KST 로 자정을 넘어 전체 세션은 시간창 생략으로 낸다)."""
        return cls._make(symbol, side, "limit", quantity, limit_price=limit_price,
                          time_in_force=time_in_force, exchange=exchange, session=session,
                          division=division, board=board, algo_strategy=algo_strategy,
                          algo_start=algo_start, algo_end=algo_end, client_order_id=client_order_id)

    @classmethod
    def stop(cls, symbol: str, *, side: Side, quantity: Numeric, stop_price: Numeric,
             time_in_force: TimeInForce = "day", exchange: str = "XKRX",
             client_order_id: str | None = None) -> Order:
        """스탑(역지정) 주문 -- ``stop_price`` 도달 시 시장가로 전환."""
        return cls._make(symbol, side, "stop", quantity, stop_price=stop_price,
                          time_in_force=time_in_force, exchange=exchange, client_order_id=client_order_id)

    @classmethod
    def stop_limit(cls, symbol: str, *, side: Side, quantity: Numeric, limit_price: Numeric,
                   stop_price: Numeric, time_in_force: TimeInForce = "day", exchange: str = "XKRX",
                   board: DomesticBoard = "KRX", client_order_id: str | None = None) -> Order:
        """스탑지정가(역지정) 주문 -- ``stop_price``(조건가격) 도달 시 ``limit_price`` 지정가로 접수.
        국내(KRX) 현금주문에서 ORD_DVSN 22 로 나간다. ``stop_price`` 는 KIS ``CNDT_PRIC``(조건가격),
        ``limit_price`` 는 ``ORD_UNPR``. ``board`` 는 체결 보드(스톱지정가는 KRX 전용이라 NXT/UN 은
        생성 시점에 :class:`~kis_trader.errors.KISUsageError` 로 거부)."""
        return cls._make(symbol, side, "stop_limit", quantity, limit_price=limit_price,
                          stop_price=stop_price, time_in_force=time_in_force, exchange=exchange,
                          board=board, client_order_id=client_order_id)

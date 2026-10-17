"""MCP 도구의 순수 핸들러 -- ``kis_trader`` 공개 API 를 호출해 JSON 안전 dict 를 돌려준다.

``mcp`` 에 의존하지 않아 단독으로 테스트된다. :mod:`kis_trader.mcp.server` 가 이 함수들을 MCP 도구로
등록한다. 각 함수는 도메인 계산을 하지 않고 패키지 결과를 필드 선택·직렬화만 한다(안전커널 규율).

직렬화는 frozen dataclass 를 공개 필드 dict 로 바꾸되 ``_raw`` 와 밑줄 필드를 제외하고 ``Decimal`` 을
문자열로 바꾼다 -- 원본 응답·자격증명이 새어 나가지 않게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast, get_args

from .._literals import Direction
from ..account import _IRP_PRODUCT_CODE, StockAccount
from ..bar import Interval
from ..client import KISClient
from ..domestic.market import InvestorType, NetBuyBasis
from ..domestic.ranking import VolumeMetric
from ..errors import KISUsageError
from ..instrument import DomesticMarket, DomesticMarketFilter, Region
from ..order import DomesticDivision, Side
from ..overseas._engine.orders import resolve_risk_currency
from ..overseas.namespace import OverseasIndexKind
from ..transport import Environment
from ._guardrails import (
    ModifyOrderPlan,
    RealOrderGate,
    StockOrderPlan,
    check_allowlist,
    make_stock_order_plan,
)
from ._policy import (
    _SWITCHABLE_PROFILES,
    _account_masked,
    is_orderable_account,
    require_overseas_general_account,
)

if TYPE_CHECKING:
    from ..domestic.index import DomesticIndex
    from ..overseas.index import OverseasIndex

#: ``quote`` 의 세션 선택자 -- 정규장(regular)/시간외 단일가(after_hours, 국내 전용). MCP 도구의
#: 입력 스키마(:mod:`kis_trader.mcp.server`)와 아래 검증이 한 출처를 쓰도록 여기서 정의한다.
Session = Literal["regular", "after_hours"]


class ListProfilesResult(TypedDict):
    """MCP ``list_profiles`` 도구의 JSON 응답 형상."""

    profiles: list[str]
    active_profile: str | None
    environment: Environment
    switching_enabled: bool


#: 검증 집합을 리터럴에서 파생한다(한 출처) -- 리터럴에 값이 추가되면 검증도 자동으로 따라간다.
_REGIONS = frozenset(get_args(Region))
_QUOTE_SESSIONS = frozenset(get_args(Session))

#: 결과에 **절대** 실려선 안 되는 자격증명 키(방어적 denylist). 현재 직렬화 대상은 엔티티뿐이라
#: 이 키들이 나올 일이 없지만, 미래에 raw 매핑을 돌려주는 핸들러가 생겨도 시크릿이 새지 않게 한다.
#: (``account``/``cano`` 같은 값은 정당한 마스킹 대상이라 여기 넣지 않는다 -- 계좌는 _account_masked 로 처리.)
_SECRET_KEYS = frozenset({
    "app_key", "appkey", "app_secret", "appsecret", "secret", "secret_key",
    "access_token", "token", "approval_key", "authorization",
})


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and key.lower().replace("-", "_") in _SECRET_KEYS


def _serialize(value: Any) -> Any:
    """frozen dataclass/Decimal/list/dict 를 JSON 안전 값으로. ``_raw``·밑줄 필드/키와 자격증명 키
    (``_SECRET_KEYS``)는 제외한다(원본 응답·자격증명 누출 방지)."""
    if is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for dc_field in fields(value):
            if dc_field.name.startswith("_") or _is_secret_key(dc_field.name):  # _raw·내부·시크릿 제외
                continue
            out[dc_field.name] = _serialize(getattr(value, dc_field.name))
        return out
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        # 엔티티의 date/datetime/time 필드(Quote.as_of, ExecutionReport.recorded_at 등)를 ISO 문자열로.
        # 안 바꾸면 strict json.dumps 소비자가 크래시하고 SDK 폴백은 비-ISO str() 포맷을 내보낸다.
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, Mapping):
        # 매핑 분기도 밑줄 키를 제외 -- dataclass 분기와 같은 규율(중첩 매핑의 _raw/내부키 누출 방지).
        # ``Mapping`` 으로 받아 ``MappingProxyType`` (freeze_vendor_payload 결과)도 재귀 처리한다.
        return {key: _serialize(item) for key, item in value.items()
                if not (isinstance(key, str) and key.startswith("_")) and not _is_secret_key(key)}
    return value


def _active_stock_account(kis: KISClient) -> StockAccount:
    """잔고/보유/미체결 도구는 주식 계좌 뷰 전용 -- 주식 계좌(위탁 01/연금저축 22/IRP 29)가 아니면
    명확히 거부한다(계좌 파사드는 다형이라 파생 뷰가 올 수 있다 -- 파생은 MCP 미노출). 전환 도구가
    파생 프로필을 애초에 못 고르지만, 기동 프로필이 파생을 가리키는 경우를 위한 방어심층이다."""
    account = kis.account
    if not isinstance(account, StockAccount):
        raise KISUsageError(
            f"이 도구는 주식 계좌(위탁·ISA/연금저축/IRP) 전용이다 -- 파생은 MCP 에 노출하지 않는다"
            f"(현재 계좌 뷰: {type(account).__name__})."
        )
    return account


def _require_overseas_eligible(active_profile: str | None, kis: KISClient) -> StockAccount:
    """해외 조회(``region="overseas"``)가 열리는지 확인하고 그 주식 계좌 뷰를 돌려준다 -- 위탁(general,
    상품코드 01) + **실전만**. isa/pension/irp 는 거부하고(해외는 general 전용), 모의(paper)도 거부한다.
    해외 조회가 실전 전용인 것은 **MCP 정책**이다(라이브러리가 금지하는 게 아니라, 이 서버가 모의 해외
    데이터를 노출하지 않기로 한 것). 'general(위탁) + 상품코드 01' 축은 해외 주문 게이트와 공유하는 단일
    leaf(:func:`require_overseas_general_account`)로 가르고 -- ISA 는 상품코드가 위탁과 같은 01 이라
    상품코드로 구별할 수 없어 이름으로 막는다 -- 실전 요건만 여기서 추가로 건다(주문은 모의도 허용)."""
    account = _active_stock_account(kis)
    require_overseas_general_account(active_profile, account)
    if kis.environment != "real":
        raise KISUsageError(
            "해외 조회는 실전 전용이다(MCP 정책) -- use_environment('real') 로 환경을 바꾸라."
        )
    return account


def quote(
    kis: KISClient, symbol: str, *, region: str = "domestic", session: str = "regular"
) -> dict[str, Any]:
    """종목 현재가 스냅샷. ``region="overseas"`` 면 해외(거래소 자동 판별). ``session="after_hours"`` 면
    국내 시간외 단일가(예상체결가·최우선호가) -- **국내 전용**이다. 검증 순서: region -> session -> 조합."""
    if region not in _REGIONS:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    if session not in _QUOTE_SESSIONS:
        raise KISUsageError(f"session 은 {sorted(_QUOTE_SESSIONS)} 중 하나여야 한다 (받은 값: {session!r}).")
    if session == "after_hours":
        if region != "domestic":
            raise KISUsageError("시간외(after_hours)는 국내(domestic) 전용이다.")
        out: dict[str, Any] = _serialize(kis.domestic.stock(symbol).after_hours_quote())
        return out
    handle = kis.overseas.stock(symbol) if region == "overseas" else kis.domestic.stock(symbol)
    out = _serialize(handle.quote())
    return out


def _require_positive_limit(limit: int) -> None:
    """리스트 핸들러의 행 상한 검증 -- bool(int 서브클래스)·비int·1 미만을 거부한다. 음수 limit 은
    ``[:limit]`` 에서 마지막 행만 빼고 돌려주는(에러 아닌 오답) 함정이라 fail-closed 로 막는다."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise KISUsageError(f"limit 은 1 이상의 정수여야 한다: {limit!r}")


def list_profiles(
    stored_profiles: list[str], *, active_profile: str | None, environment: Environment,
    switching_enabled: bool,
) -> ListProfilesResult:
    """현재 환경에 저장된 프로필 중 ``use_profile`` 로 전환 가능한 이름만 정렬해 돌려준다.

    파생·사용자 정의 프로필은 저장돼 있어도 MCP 전환 정책 밖이므로 제외한다. 직접-자격증명 고정 슬롯처럼
    전환 불가한 세션은 저장분과 무관하게 빈 목록을 돌려준다. 현재 활성 프로필·환경·전환 가능 여부를 함께
    실어 에이전트가 목록과 현재 선택을 한 번에 확인하게 한다.
    """
    return {
        "profiles": (
            sorted(set(stored_profiles) & _SWITCHABLE_PROFILES) if switching_enabled else []
        ),
        "active_profile": active_profile,
        "environment": environment,
        "switching_enabled": switching_enabled,
    }


def search(kis: KISClient, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """이름/질의로 국내 종목 후보를 찾는다(코드 추측 금지 -- 실제 코드는 여기서만 온다)."""
    _require_positive_limit(limit)
    hits = kis.domestic.search_stocks(query)
    out: list[dict[str, Any]] = [_serialize(h) for h in list(hits)[:limit]]
    return out


def ranking_change(kis: KISClient, *, direction: str = "gainers", limit: int = 10) -> list[dict[str, Any]]:
    """국내 등락률 순위. ``direction="gainers"`` 상승 / ``"losers"`` 하락."""
    if direction not in ("gainers", "losers"):
        raise KISUsageError(f"direction 은 'gainers'/'losers' 만 (받은 값: {direction!r}).")
    _require_positive_limit(limit)
    rows = kis.domestic.ranking.by_change(direction=cast(Direction, direction))
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def ranking_volume(kis: KISClient, *, metric: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """국내 거래량/거래대금 순위. ``metric`` average_volume/cumulative_trading_amount(거래대금)/
    volume_growth/turnover; 생략 시 라이브러리 기본(거래대금)."""
    if metric is not None and metric not in _VOLUME_METRICS:
        raise KISUsageError(f"metric 은 {sorted(_VOLUME_METRICS)} 중 하나여야 한다 (받은 값: {metric!r}).")
    _require_positive_limit(limit)
    extra = {} if metric is None else {"metric": cast(VolumeMetric, metric)}
    rows = kis.domestic.ranking.by_volume(**extra)
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def ranking_market_cap(kis: KISClient, *, limit: int = 10) -> list[dict[str, Any]]:
    """국내 시가총액 상위 순위."""
    _require_positive_limit(limit)
    rows = kis.domestic.ranking.by_market_cap()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


# --- 시세·수급(투자자 매매동향) 조회 -- 읽기 전용, 계좌 불필요 ---------------------
#: 검증 집합을 리터럴에서 파생한다(한 출처) -- 리터럴이 바뀌면 검증도 자동으로 따라간다.
_INTERVALS = frozenset(get_args(Interval))
_MARKETS = frozenset(get_args(DomesticMarket))
_NET_BUY_MARKETS = frozenset(get_args(DomesticMarketFilter))
_NET_BUY_BASES = frozenset(get_args(NetBuyBasis))
_INVESTOR_TYPES = frozenset(get_args(InvestorType))
_SIDES = frozenset(get_args(Side))
_VOLUME_METRICS = frozenset(get_args(VolumeMetric))

#: index_bars 의 해외 kind 검증 집합 -- 라이브러리 단일 출처 :data:`OverseasIndexKind` 에서 파생(재선언 금지).
_INDEX_KINDS = frozenset(get_args(OverseasIndexKind))


def _require_start_for_period(interval: str) -> None:
    """기간봉(1d/1wk/1mo)은 라이브러리가 start 없으면 raise하므로(분봉 1m만 예외) MCP 경계에서 미리
    막아 명확한 메시지를 준다 -- LLM이 bars(symbol)로 자연스럽게 부르는 실패를 차단."""
    if interval != "1m":
        raise KISUsageError(
            f"기간봉(interval={interval!r})은 start(YYYYMMDD)가 필요하다 -- 분봉(1m)만 생략 가능.")


def stock_bars(
    kis: KISClient, symbol: str, *, region: str = "domestic", interval: str = "1d",
    start: str | None = None, end: str | None = None, adjusted: bool = True, limit: int = 500,
) -> list[dict[str, Any]]:
    """국내/해외 종목 기간봉(OHLCV). ``region`` domestic/overseas. ``interval`` 일(1d)·주(1wk)·월(1mo)·분(1m).
    기간봉(1d/1wk/1mo)은 ``start`` 가 **필수**(YYYYMMDD), ``end`` 는 생략 시 당일까지; 분봉(1m)만 생략 가능.
    과거 가격(YTD 수익률 등) 계산용 -- 현재가는 ``quote`` 를 쓸 것. 최대 ``limit`` 행(기본 500)."""
    if region not in _REGIONS:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    if interval not in _INTERVALS:
        raise KISUsageError(f"interval 은 {sorted(_INTERVALS)} 중 하나여야 한다 (받은 값: {interval!r}).")
    if start is None:
        _require_start_for_period(interval)
    _require_positive_limit(limit)
    handle = kis.overseas.stock(symbol) if region == "overseas" else kis.domestic.stock(symbol)
    rows = handle.bars(cast(Interval, interval), start=start, end=end, adjusted=adjusted, max_bars=limit)
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def _index_handle(
    kis: KISClient, code: str, *, region: str, kind: str | None
) -> DomesticIndex | OverseasIndex:
    """region/kind 를 검증하고 지수 핸들을 돌려준다(index_bars/index_quote 공통 라우팅).

    ``region="overseas"`` 는 ``kind`` index(기본)/fx/bond/gold, ``region="domestic"`` 은 국내 지수코드
    전용이며 ``kind`` 를 주면 거부한다. kind 미지정이면 라이브러리 기본(index) -- MCP 가 재기술하지 않는다."""
    if region not in _REGIONS:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    if region == "overseas":
        if kind is not None and kind not in _INDEX_KINDS:
            raise KISUsageError(f"kind 는 {sorted(_INDEX_KINDS)} 중 하나여야 한다 (받은 값: {kind!r}).")
        extra = {} if kind is None else {"kind": cast(OverseasIndexKind, kind)}
        return kis.overseas.index(code, **extra)
    if kind is not None:
        raise KISUsageError("kind 는 해외(region='overseas') 전용이다 -- 국내 지수엔 쓸 수 없다.")
    return kis.domestic.index(code)


def index_bars(
    kis: KISClient, code: str, *, region: str = "overseas", kind: str | None = None,
    interval: str = "1d", start: str | None = None, end: str | None = None, limit: int = 500,
) -> list[dict[str, Any]]:
    """지수·환율·국채·금 기간봉. ``region="overseas"`` 는 ``kind`` index(기본)/fx(환율, 예 code
    ``FX@KRW``=원/달러)/bond/gold, ``region="domestic"`` 은 국내 지수코드 전용(``kind`` 는 해외 전용이라
    주면 거부). 기간봉(1d/1wk/1mo)은 ``start`` 필수, 분봉(1m)은 지수·환율만. 지수류는 ``adjusted`` 없음."""
    handle = _index_handle(kis, code, region=region, kind=kind)
    if interval not in _INTERVALS:
        raise KISUsageError(f"interval 은 {sorted(_INTERVALS)} 중 하나여야 한다 (받은 값: {interval!r}).")
    if start is None:
        _require_start_for_period(interval)
    _require_positive_limit(limit)
    rows = handle.bars(cast(Interval, interval), start=start, end=end, max_bars=limit)
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def index_quote(
    kis: KISClient, code: str, *, region: str = "overseas", kind: str | None = None
) -> dict[str, Any]:
    """지수·환율·국채·금 현재가 스냅샷.

    ``region="overseas"`` 는 ``kind`` index(기본)/fx/bond/gold, ``region="domestic"`` 은
    국내 지수코드 전용이며 ``kind`` 를 주면 거부한다.
    """
    out: dict[str, Any] = _serialize(_index_handle(kis, code, region=region, kind=kind).quote())
    return out


def order_book(kis: KISClient, symbol: str, *, region: str = "domestic") -> dict[str, Any]:
    """종목 호가창(매수/매도 호가·잔량 스냅샷). ``region`` domestic/overseas."""
    if region not in _REGIONS:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    handle = kis.overseas.stock(symbol) if region == "overseas" else kis.domestic.stock(symbol)
    out: dict[str, Any] = _serialize(handle.order_book())
    return out


def trades(kis: KISClient, symbol: str, *, region: str = "domestic", limit: int = 40) -> list[dict[str, Any]]:
    """종목 최근 체결 내역(time & sales; 체결가·수량·시각, 최신순). ``region`` domestic/overseas. 최대 ``limit`` 행."""
    if region not in _REGIONS:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    _require_positive_limit(limit)
    handle = kis.overseas.stock(symbol) if region == "overseas" else kis.domestic.stock(symbol)
    rows = handle.trades()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def status(kis: KISClient, symbol: str) -> dict[str, Any]:
    """국내 종목 상태(거래정지·관리종목·투자경고 등 거래 가능 여부 플래그) -- 국내 전용."""
    out: dict[str, Any] = _serialize(kis.domestic.stock(symbol).status())
    return out


def stock_investor_flows(kis: KISClient, symbol: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """국내 종목의 일자별 투자자 수급 -- 개인·외국인·기관 순매수(수량·대금). 최신순, 최대 ``limit`` 행."""
    _require_positive_limit(limit)
    rows = kis.domestic.stock(symbol).investor_flows()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def market_investor_flows(
    kis: KISClient, *, market: str = "KOSPI", limit: int = 60,
) -> list[dict[str, Any]]:
    """시장 전체(KOSPI/KOSDAQ) 투자자 수급 -- 외국인·개인·기관 순매수. 최신순, 최대 ``limit`` 행."""
    if market not in _MARKETS:
        raise KISUsageError(f"market 은 {sorted(_MARKETS)} 중 하나여야 한다 (받은 값: {market!r}).")
    _require_positive_limit(limit)
    rows = kis.domestic.market.investor_flows(market=cast(DomesticMarket, market))
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def investor_net_buy_stocks(
    kis: KISClient, *, market: str = "all", basis: str = "volume", side: str = "buy",
    investor: str = "all", limit: int = 30,
) -> list[dict[str, Any]]:
    """투자자별 순매수 상위 종목. ``market`` all/KOSPI/KOSDAQ, ``basis`` volume(수량)/amount(대금),
    ``side`` buy(순매수)/sell(순매도), ``investor`` all/foreign/institutional/other. 최대 ``limit`` 행."""
    if market not in _NET_BUY_MARKETS:
        raise KISUsageError(f"market 은 {sorted(_NET_BUY_MARKETS)} 중 하나여야 한다 (받은 값: {market!r}).")
    if basis not in _NET_BUY_BASES:
        raise KISUsageError(f"basis 는 {sorted(_NET_BUY_BASES)} 중 하나여야 한다 (받은 값: {basis!r}).")
    if side not in _SIDES:
        raise KISUsageError(f"side 는 {sorted(_SIDES)} 중 하나여야 한다 (받은 값: {side!r}).")
    if investor not in _INVESTOR_TYPES:
        raise KISUsageError(f"investor 는 {sorted(_INVESTOR_TYPES)} 중 하나여야 한다 (받은 값: {investor!r}).")
    _require_positive_limit(limit)
    rows = kis.domestic.market.investor_net_buy_stocks(
        market=cast(DomesticMarketFilter, market), basis=cast(NetBuyBasis, basis),
        side=cast(Side, side), investor=cast(InvestorType, investor))
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def balance(
    kis: KISClient, *, region: str = "domestic", market: str | None = None,
    active_profile: str | None = None,
) -> dict[str, Any]:
    """계좌 잔고 요약. ``region="domestic"`` 은 활성 계좌 종류로 분기한다 -- 위탁·ISA/연금저축(상품코드
    01/22)은 국내주식 잔고, IRP(29)는 퇴직연금 잔고 렌즈. ``region="overseas"`` 는 해외주식 잔고
    (general·실전 전용, MCP 정책)로, ``market`` 생략 시 시장별 요약을 ``{"by_market": [...]}`` 로 감싸
    돌려준다(단건 조회와 **같은 dict 종류** -- 호출자가 응답 shape 로 분기하지 않게). 도메인 수치는 패키지가 만든다."""
    if region == "overseas":
        account = _require_overseas_eligible(active_profile, kis)
        result = account.overseas.balance(market=market)  # market=None -> 시장별 리스트, 지정 -> 단건
        serialized = _serialize(result)
        # 항상 dict 로 통일 -- 시장별 집계(list)는 {by_market: [...]} 로 감싼다(한 가지 반환 종류).
        if isinstance(serialized, list):
            return {"by_market": serialized}
        return cast("dict[str, Any]", serialized)
    if region != "domestic":
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    account = _active_stock_account(kis)
    if account.product_code == _IRP_PRODUCT_CODE:
        out: dict[str, Any] = _serialize(account.pension.balance())
        return out
    out = _serialize(account.domestic.balance())
    return out


def positions(
    kis: KISClient, *, region: str = "domestic", market: str | None = None, limit: int = 100,
    active_profile: str | None = None,
) -> list[dict[str, Any]]:
    """보유 종목. ``region="domestic"`` 은 활성 계좌 종류로 분기(위탁·ISA/연금저축=국내주식 보유,
    IRP(29)=퇴직연금 잔고의 보유내역). ``region="overseas"`` 는 해외주식 보유(general·실전 전용)."""
    _require_positive_limit(limit)
    if region == "overseas":
        account = _require_overseas_eligible(active_profile, kis)
        rows: Any = account.overseas.positions(market=market)
    elif region == "domestic":
        account = _active_stock_account(kis)
        if account.product_code == _IRP_PRODUCT_CODE:
            rows = account.pension.balance().positions  # 퇴직연금은 보유내역이 잔고에 실린다
        else:
            rows = account.domestic.positions()
    else:
        raise KISUsageError(f"region 은 {sorted(_REGIONS)} 중 하나여야 한다 (받은 값: {region!r}).")
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def open_orders(kis: KISClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """주문 가능한 국내주식 계좌(위탁·ISA 01·연금저축 22)의 미체결(정정취소가능) 주문. IRP(29)는 주문이
    없어 조회 대상이 아니다(거부). 상품코드가 아니라 주문 능력(orderable)으로 가른다 -- 국내 미체결
    엔드포인트(TTTC0084R)는 상품코드로 막히지 않고 라이브러리 ``account.domestic.open_orders()`` 경로에도
    상품 게이트가 없어, 주문을 낼 수 있는 계좌의 취소 가능 주문을 상품코드로 숨기지 않는다."""
    _require_positive_limit(limit)
    account = _active_stock_account(kis)
    if not is_orderable_account(account):
        raise KISUsageError(
            f"미체결(정정취소가능) 주문 조회는 주문 가능한 국내주식 계좌(위탁·ISA 01·연금저축 22) "
            f"전용이다 -- 현재 계좌는 주문 대상이 아니다(상품코드 {account.product_code}). IRP(29)는 "
            "조회전용이라 미체결 주문이 없다."
        )
    rows = account.domestic.open_orders()
    out: list[dict[str, Any]] = [_serialize(r) for r in list(rows)[:limit]]
    return out


def order_preview(
    kis: KISClient, capped_currencies: frozenset[str], *, symbol: str, side: Side, quantity: int,
    limit_price: str | None = None, division: DomesticDivision | None = None,
    region: str = "domestic", exchange: str | None = None, has_domestic_risk_cap: bool = False,
) -> dict[str, Any]:
    """주문 **미리보기**(dry-run) -- 전송하지 않고 나갈 티켓만 되읽어 보여준다. 실제 전송은 ``place_order``
    가 가드레일(이중게이트+RiskLimits+allowlist+사람확인)을 거쳐 한다. 이 도구는 무엇이 나갈지 미리
    확인하는 용도다(``sent: false``). 티켓은 ``place_order`` 와 **같은** 형상 검증(``make_stock_order_plan``)과
    해외 실전의 통화별 한도 선검사를 거쳐 만든다 -- preview 가 통과시킨 주문은 place_order 도 같은
    형상으로 받는다(형상/거부 divergence 방지; 가격·구분 결합 등 깊은 검증은 집행 시
    :class:`~kis_trader.order.Order` 가 한다). 국내 티켓은 ``exchange`` 를 싣지 않고, 해외 티켓만 싣는다."""
    plan = make_stock_order_plan(
        region=region, symbol=symbol, side=side, quantity=quantity,
        limit_price=limit_price, division=division, exchange=exchange,
    )
    # place_order 와 같은 정직한 선거부: 실전인데 그 region 의 캡이 없으면 preview 도 거부한다 -- 국내는
    # 크기 캡, 해외는 결제통화 한도. place 의 refusal 을 preview 가 양 region 대칭으로 비춘다(환경은 활성
    # 슬롯 환경 = kis.environment; place 의 gate.is_real() 과 동치).
    _require_domestic_cap(
        is_real=kis.environment == "real", region=plan.region, has_domestic_risk_cap=has_domestic_risk_cap)
    _require_overseas_currency_cap(plan, capped_currencies, is_real=kis.environment == "real")
    ticket: dict[str, Any] = {
        "sent": False,
        "note": "미리보기 -- 전송되지 않았다. 실주문은 place_order 가 가드레일·사람확인을 거쳐 전송한다.",
        "environment": kis.environment,
        "account": _account_masked(kis),
        "region": plan.region,
        "symbol": plan.symbol,
        "side": plan.side,
        "quantity": plan.quantity,
        "order_type": plan.order_type,
        "limit_price": plan.limit_price,
        "division": plan.division,
    }
    if plan.region == "overseas":
        ticket["exchange"] = plan.exchange
    return ticket


def reconcile(kis: KISClient, *, client_order_id: str) -> dict[str, Any]:
    """결과 불명 주문의 사후 확정(유일한 사후 진실). 미해소면 in-flight 로 남긴다.

    ``refresh=True`` 로 브로커를 **실제로** 재스캔한다 -- 사후확정 도구는 저장된 접수 스냅샷(체결 0)을
    진실로 돌려주면 안 된다(그걸 보고 재주문하면 이중매수)."""
    report = kis.orders.reconcile(client_order_id, refresh=True)
    return {
        "client_order_id": client_order_id,
        "resolved": report is not None,
        "report": _serialize(report) if report is not None else None,
    }


# --- 실주문 plan 핸들러(순수: 가드레일 선검증, 와이어 전) --------------------
# server 가 이 plan 으로 elicitation 확인 뒤 라이브러리(buy/sell/cancel/modify)를 호출한다. 어떤 검사든
# 어기면 여기서 KISUsageError 로 거부되어 주문은 전송되지 않는다(fail-closed, 사람/AI 에 의존 안 함).
def _require_overseas_currency_cap(
    plan: StockOrderPlan, capped_currencies: frozenset[str], *, is_real: bool
) -> None:
    """해외 **실전** 주문의 리스크 검사 통화 금액 한도(통화별 캡) 선검사 -- 거래소로 리스크 검사
    통화를 판별해 그 통화의 MCP 캡(``KIS_MCP_MAX_ORDER_NOTIONAL_<CCY>``)이 없으면 와이어 전에 거부한다(early fail-closed,
    정직한 preview). 안전 엔진(:meth:`~kis_trader.risk.RiskLimits.check`)의 해외 통화별 캡 강제가 와이어
    backstop 이고, 이 선검사는 같은 거부를 더 일찍·명확히 올린다. 거래소->통화 표는 해외 엔진이 소유
    한다(여기서 중복하지 않음). 모의(paper)/국내는 대상 아님."""
    if plan.region != "overseas" or not is_real:
        return
    ccy = resolve_risk_currency(plan.exchange) if plan.exchange is not None else None
    if ccy is None or ccy not in capped_currencies:
        raise KISUsageError(
            f"해외 실전 주문엔 결제통화 금액 한도가 필요하다 -- 환경변수 "
            f"KIS_MCP_MAX_ORDER_NOTIONAL_{ccy or '<통화>'} 를 설정하라(fail-closed -- 무한도 전송 금지)."
        )


def _require_domestic_cap(*, is_real: bool, region: str, has_domestic_risk_cap: bool) -> None:
    """국내 실주문은 국내(KRW)에 적용되는 fat-finger 캡이 있어야 한다 -- 해외 통화별 캡만 있고 국내 캡이
    없는 RiskLimits 로 국내 실주문이 무한도로 열리는 것을 막는다(해외 :func:`_require_overseas_currency_
    cap` 의 국내 짝; 게이트의 '실전=캡 필수' 약속을 region 별로 참이 되게 한다). 모의/해외는 대상 아님."""
    if is_real and region == "domestic" and not has_domestic_risk_cap:
        raise KISUsageError(
            "국내 실전 주문엔 국내(KRW)에 적용되는 fat-finger 캡이 필요하다 -- 해외 통화캡만 설정하면 "
            "국내 주문이 무한도로 열린다(fail-closed). 크기(size) 캡 -- 수량 또는 금액 -- 을 설정하라"
            "(RiskLimits.max_order_quantity/max_order_notional 또는 KIS_MCP_MAX_ORDER_QTY/"
            "KIS_MCP_MAX_ORDER_NOTIONAL). 가격 collar 는 크기 캡이 아니라 이 게이트를 만족하지 못한다."
        )


def plan_place_order(
    gate: RealOrderGate, allowlist: frozenset[str] | None,
    capped_currencies: frozenset[str] = frozenset(), *,
    has_risk_limits: bool,
    region: str, symbol: str, side: str, quantity: int, limit_price: str | None = None,
    division: str | None = None, exchange: str | None = None, has_domestic_risk_cap: bool = False,
) -> StockOrderPlan:
    """매수/매도 주문 계획 -- 이중게이트 -> (실전) RiskLimits 필수 -> 티켓 빌드 -> (실전) region 별 캡
    선검사(국내 KRW 캡 / 해외 통화별 캡) -> allowlist 순으로 검증한다. 실전에서 RiskLimits(fail-closed
    캡)가 없으면 거부한다(관례: 캡은 사용자 설정, 미설정=거부). 실전에선 **그 주문의 region 에 적용되는**
    캡이 있어야 한다 -- 국내는 KRW 캡(``has_domestic_risk_cap``), 해외는 결제통화 한도가 없으면 와이어 전에
    거부한다(해외 통화캡만 있고 국내 캡이 없는 상태로 국내 실주문이 무한도로 새지 않게 한다)."""
    gate.require_executable()
    if gate.is_real() and not has_risk_limits:
        raise KISUsageError(
            "실전 주문은 RiskLimits(fat-finger 캡) 설정이 필요하다 -- 미설정이면 전송하지 않는다"
            "(fail-closed). KISClient(risk=RiskLimits(...)) 또는 MCP 캡 환경변수로 설정."
        )
    _require_domestic_cap(is_real=gate.is_real(), region=region, has_domestic_risk_cap=has_domestic_risk_cap)
    plan = make_stock_order_plan(
        region=region, symbol=symbol, side=side, quantity=quantity, limit_price=limit_price,
        division=division, exchange=exchange,
    )
    _require_overseas_currency_cap(plan, capped_currencies, is_real=gate.is_real())
    check_allowlist(symbol, allowlist, is_real=gate.is_real())
    return plan


def plan_cancel_order(gate: RealOrderGate, *, client_order_id: str) -> str:
    """취소 계획 -- 이중게이트만(취소는 리스크를 늘리지 않음). 기존 ``client_order_id`` 를 지목한다."""
    gate.require_executable()
    if not isinstance(client_order_id, str) or not client_order_id:
        raise KISUsageError(f"client_order_id 는 비어 있지 않은 문자열이어야 한다: {client_order_id!r}")
    return client_order_id


def plan_modify_order(
    gate: RealOrderGate, allowlist: frozenset[str] | None = None, *,
    has_risk_limits: bool,
    client_order_id: str, limit_price: str, quantity: int | None = None,
    symbol: str | None = None, side: Side | None = None,
    region: str = "domestic", has_domestic_risk_cap: bool = False,
) -> ModifyOrderPlan:
    """정정 계획 -- 새 가격을 거는 변경이라 신규 주문처럼 (실전) RiskLimits 를 요구한다(fail-closed).
    원주문의 ``region`` 에 적용되는 캡이 있어야 한다(국내는 KRW 캡 ``has_domestic_risk_cap`` -- place 와 같은
    region 별 fail-closed; 해외 통화캡만으론 국내 정정이 무한도로 열리지 않게 한다. 해외 결제통화 캡은
    와이어 backstop :meth:`~kis_trader.risk.RiskLimits.check` 가 강제한다). 원주문 종목(``symbol``)이
    저장소에서 해석되면 신규 주문과 같이 실전 allowlist 도 확인한다 -- 정정은 노출을 늘릴 수 있어(재가격·
    수량) place 와 같은 종목 통제를 받아야 한다. 종목을 모르면(저장소 미보유) allowlist 는 건너뛴다."""
    gate.require_executable()
    if gate.is_real() and not has_risk_limits:
        raise KISUsageError(
            "실전 정정은 RiskLimits 설정이 필요하다(fail-closed) -- 새 가격이 캡을 통과해야 한다."
        )
    _require_domestic_cap(is_real=gate.is_real(), region=region, has_domestic_risk_cap=has_domestic_risk_cap)
    if not isinstance(client_order_id, str) or not client_order_id:
        raise KISUsageError(f"client_order_id 는 비어 있지 않은 문자열이어야 한다: {client_order_id!r}")
    if not isinstance(limit_price, (str, int)):
        raise KISUsageError(f"limit_price 는 문자열/정수 스칼라여야 한다: {limit_price!r}")
    if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0):
        raise KISUsageError(f"quantity 는 양의 정수여야 한다: {quantity!r}")
    if symbol is not None:
        check_allowlist(symbol, allowlist, is_real=gate.is_real())
    return ModifyOrderPlan(client_order_id=client_order_id, limit_price=str(limit_price),
                           quantity=quantity, symbol=symbol, side=side)

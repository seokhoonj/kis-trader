"""해외주식 시장 순위 조회 (내부).

사용자면은 해외 순위 네임스페이스(:class:`~kis_trader.overseas.ranking.OverseasRankingQueries`,
``kis.overseas.ranking``)다. 모든 순위가 거래소(``EXCD``)를 받고 응답의 ``output2`` 배열이 순위
목록이다(``output1`` 은 조회 요약). 공통 행(순위/코드/이름/현재가/전일대비/거래량/거래대금)만 매핑
하고 순위별 고유 지표는 ``_raw`` 에 둔다.

KIS URL/TR-ID:
- 거래량순위: ``GET .../overseas-stock/v1/ranking/trade-vol`` ``HHDFS76310010``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ..._internal._response import (
    _missing_block_error,
    _raise_if_error,
)
from ..._internal._wire import (
    _apply_change_sign,
    optional_decimal,
    required_decimal,
    required_int,
)
from ..._literals import Direction, FluctuationDirection
from ...errors import KISUsageError
from ...transport import Transport
from ..entities.ranking import OverseasRankedStock

_TRADE_VOL = ("/uapi/overseas-stock/v1/ranking/trade-vol", "HHDFS76310010")
_TRADE_AMOUNT = ("/uapi/overseas-stock/v1/ranking/trade-pbmn", "HHDFS76320010")
_TRADE_GROWTH = ("/uapi/overseas-stock/v1/ranking/trade-growth", "HHDFS76330000")
_MARKET_CAP = ("/uapi/overseas-stock/v1/ranking/market-cap", "HHDFS76350100")
_UPDOWN = ("/uapi/overseas-stock/v1/ranking/updown-rate", "HHDFS76290000")
_VOLUME_SURGE = ("/uapi/overseas-stock/v1/ranking/volume-surge", "HHDFS76270000")
_BUY_STRENGTH = ("/uapi/overseas-stock/v1/ranking/volume-power", "HHDFS76280000")
_TURNOVER = ("/uapi/overseas-stock/v1/ranking/trade-turnover", "HHDFS76340000")
_PRICE_FLUCT = ("/uapi/overseas-stock/v1/ranking/price-fluct", "HHDFS76260000")
_NEW_HIGHLOW = ("/uapi/overseas-stock/v1/ranking/new-highlow", "HHDFS76300000")
#: 상승/하락 구분(GUBN). KIS 명세: 0(하락율), 1(상승율).
_UPDOWN_GUBN = {"gainers": "1", "losers": "0"}
#: 급등/급락 구분(price-fluct GUBN). KIS 명세: 0(급락), 1(급등).
_FLUCT_GUBN = {"risers": "1", "fallers": "0"}
#: 신고/신저 구분(new-highlow GUBN). KIS 명세: 1(신고), 0(신저).
_HIGHLOW_GUBN = {"high": "1", "low": "0"}


def _parse_ranking(
    rows: Sequence[Mapping[str, Any]],
) -> list[OverseasRankedStock]:
    # 엔드포인트별 스키마 차이 흡수(라이브 확인 2026-10-16): 일부 순위 TR(new-highlow·volume-surge·
    # volume-power·price-fluct)은 명시적 ``rank`` 필드가 없어 순위=행 순서이고, 한글/영문명을
    # ``knam``/``enam`` 로 준다(market-cap·updown-rate 는 ``rank``/``name``/``ename``). 응답 단위로
    # 스키마를 한 번 정한다 -- 명시 rank 가 하나라도 있으면 전 행이 그 값을 써야 하고(혼재 시 누락행은
    # required_int 가 fail-closed), 전무하면 유효행 순서(연속, 패딩 빈행은 건너뛰어 공백 없음).
    has_explicit_rank = any(str(r.get("rank") or "").strip() for r in rows)
    ranked: list[OverseasRankedStock] = []
    for row in rows:
        symbol = str(row.get("symb", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("sign", "")).strip()
        rank = required_int(row.get("rank"), "rank") if has_explicit_rank else len(ranked) + 1
        ranked.append(
            OverseasRankedStock(
                rank=rank,
                exchange=str(row.get("excd", "")).strip(),
                symbol=symbol,
                name=str(row.get("name") or row.get("knam") or "").strip(),
                english_name=str(row.get("ename") or row.get("enam") or "").strip(),
                current_price=required_decimal(row.get("last"), "last"),
                change=_apply_change_sign(required_decimal(row.get("diff"), "diff"), sign),
                change_percent=_apply_change_sign(required_decimal(row.get("rate"), "rate"), sign),
                volume=required_int(row.get("tvol"), "tvol"),
                trading_amount=optional_decimal(row.get("tamt"), "tamt"),
                _raw=row,
            )
        )
    return ranked


def _fetch_ranking(
    transport: Transport, *, path: str, tr: str, params: Mapping[str, str]
) -> list[OverseasRankedStock]:
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params=dict(params), idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, (list, tuple)):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return _parse_ranking(rows)


def fetch_by_volume(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 거래량 순위. ``exchange`` 는 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX)."""
    path, tr = _TRADE_VOL
    params = {
        "EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
        "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": "",
    }
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_amount(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 거래대금 순위."""
    path, tr = _TRADE_AMOUNT
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_trade_growth(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 거래증가율 순위."""
    path, tr = _TRADE_GROWTH
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_market_cap(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 시가총액 순위."""
    path, tr = _MARKET_CAP
    # CURR_GB(통화구분) 필수 -- 누락 시 'INPUT FIELD NOT FOUND [CURR_GB]'. "0" 로 조회 확인
    # (2026-10-16 US/NAS 장중 라이브: rt_cd=0, 100 rows). 공식 예제는 CURR_GB 를 누락하나 라이브는 요구한다.
    # "0" 은 NAS 로만 검증 -- 비-US 거래소(HKS/SHS/TSE/HSX...)의 올바른 CURR_GB 값은 미검증(needs-probe).
    params = {"EXCD": exchange, "CURR_GB": "0", "VOL_RANG": "1",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_change(
    transport: Transport, *, exchange: str, direction: Direction = "gainers"
) -> list[OverseasRankedStock]:
    """한 거래소의 등락률 순위. ``direction="gainers"`` 상승률 / ``"losers"`` 하락률(KIS 명세 GUBN 1/0)."""
    try:
        gubn = _UPDOWN_GUBN[direction]
    except KeyError:
        raise KISUsageError(f"direction 은 {sorted(_UPDOWN_GUBN)} 중 하나: {direction!r}") from None
    path, tr = _UPDOWN
    params = {"EXCD": exchange, "GUBN": gubn, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_volume_surge(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 거래량 급증 순위."""
    path, tr = _VOLUME_SURGE
    params = {"EXCD": exchange, "MINX": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_buy_strength(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 매수 체결강도 순위."""
    path, tr = _BUY_STRENGTH
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_turnover(transport: Transport, *, exchange: str) -> list[OverseasRankedStock]:
    """한 거래소의 거래 회전율 순위."""
    path, tr = _TURNOVER
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_price_fluctuation(
    transport: Transport, *, exchange: str, direction: FluctuationDirection = "risers"
) -> list[OverseasRankedStock]:
    """한 거래소의 가격 급등/급락 순위. ``direction="risers"`` 급등 / ``"fallers"`` 급락(KIS 명세 GUBN 1/0)."""
    try:
        gubn = _FLUCT_GUBN[direction]
    except KeyError:
        raise KISUsageError(f"direction 은 {sorted(_FLUCT_GUBN)} 중 하나: {direction!r}") from None
    path, tr = _PRICE_FLUCT
    params = {"EXCD": exchange, "GUBN": gubn, "MINX": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_new_highlow(
    transport: Transport, *, exchange: str, extreme: Literal["high", "low"] = "high", sustained: bool = True
) -> list[OverseasRankedStock]:
    """한 거래소의 신고가/신저가 순위. ``extreme="high"`` 신고 / ``"low"`` 신저(KIS 명세 GUBN 1/0).
    ``sustained=True`` 돌파유지 / ``False`` 일시돌파(KIS 명세 GUBN2 1/0)."""
    try:
        gubn = _HIGHLOW_GUBN[extreme]
    except KeyError:
        raise KISUsageError(f"extreme 은 {sorted(_HIGHLOW_GUBN)} 중 하나: {extreme!r}") from None
    path, tr = _NEW_HIGHLOW
    # 필수: EXCD/NDAY/MINX/VOL_RANG/GUBN(1 신고/0 신저)/GUBN2(1 유지/0 일시)/KEYB/AUTH.
    # NDAY·MINX 둘 다 필요 -- NDAY 누락 시 'INPUT FIELD NOT FOUND [NDAY]'. "0" 로 조회 확인
    # (2026-10-16 US/NAS 장중 라이브: rt_cd=0, 100 rows). 공식 예제는 NDAY 를 누락하나 라이브는 요구한다.
    # "0" 은 NAS 로만 검증 -- 비-US 거래소의 올바른 NDAY 값은 미검증(needs-probe).
    params = {"EXCD": exchange, "NDAY": "0", "GUBN": gubn, "GUBN2": "1" if sustained else "0",
              "MINX": "0", "VOL_RANG": "0", "KEYB": "", "AUTH": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)

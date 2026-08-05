"""해외주식 시장 순위 조회 (내부).

사용자면은 해외 순위 네임스페이스(:class:`~kis_openapi.overseas_ranking.OverseasRankingQueries`,
``kis.overseas_ranking``)다. 모든 순위가 거래소(``EXCD``)를 받고 응답의 ``output2`` 배열이 순위
목록이다(``output1`` 은 조회 요약). 공통 행(순위/코드/이름/현재가/전일대비/거래량/거래대금)만 매핑
하고 순위별 고유 지표는 ``_raw`` 에 둔다.

KIS URL/TR-id:
- 거래량순위: ``GET .../overseas-stock/v1/ranking/trade-vol`` ``HHDFS76310010``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .._domestic.market_data import (
    _apply_change_sign,
    _missing_block_error,
    _raise_if_error,
)
from .._wire import required_decimal, required_int
from ..errors import KISUsageError
from ..overseas_ranking_items import RankedOverseasStock
from ..transport import Transport

_TRADE_VOL = ("/uapi/overseas-stock/v1/ranking/trade-vol", "HHDFS76310010")
_TRADE_AMOUNT = ("/uapi/overseas-stock/v1/ranking/trade-pbmn", "HHDFS76320010")
_TRADE_GROWTH = ("/uapi/overseas-stock/v1/ranking/trade-growth", "HHDFS76330000")
_MARKET_CAP = ("/uapi/overseas-stock/v1/ranking/market-cap", "HHDFS76350100")
_UPDOWN = ("/uapi/overseas-stock/v1/ranking/updown-rate", "HHDFS76290000")
_VOLUME_SURGE = ("/uapi/overseas-stock/v1/ranking/volume-surge", "HHDFS76270000")
_BUY_STRENGTH = ("/uapi/overseas-stock/v1/ranking/volume-power", "HHDFS76280000")
_TURNOVER = ("/uapi/overseas-stock/v1/ranking/trade-turnover", "HHDFS76340000")
#: 상승/하락 구분(GUBN). 원장: 0(하락율), 1(상승율).
_UPDOWN_GUBN = {"gainers": "1", "losers": "0"}


def _parse_ranking(rows: list[Mapping[str, Any]]) -> list[RankedOverseasStock]:
    ranked: list[RankedOverseasStock] = []
    for row in rows:
        symbol = str(row.get("symb", "")).strip()
        if not symbol:
            continue
        sign = str(row.get("sign", "")).strip()
        ranked.append(
            RankedOverseasStock(
                rank=required_int(row.get("rank"), "rank"),
                exchange=str(row.get("excd", "")).strip(),
                symbol=symbol,
                name=str(row.get("name", "")).strip(),
                english_name=str(row.get("ename", "")).strip(),
                last=required_decimal(row.get("last"), "last"),
                change=_apply_change_sign(required_decimal(row.get("diff"), "diff"), sign),
                change_percent=_apply_change_sign(required_decimal(row.get("rate"), "rate"), sign),
                volume=required_int(row.get("tvol"), "tvol"),
                amount=required_decimal(row.get("tamt"), "tamt"),
                _raw=row,
            )
        )
    return ranked


def _fetch_ranking(
    transport: Transport, *, path: str, tr: str, params: Mapping[str, str]
) -> list[RankedOverseasStock]:
    resp = transport.request(
        method="GET", path=path, tr_id=tr, params=dict(params), idempotent=True
    )
    _raise_if_error(resp)
    rows = resp.body.get("output2")
    if not isinstance(rows, list):             # 성공 응답인데 배열 아님 -> fail-closed
        raise _missing_block_error("output2", resp)
    return _parse_ranking(rows)


def fetch_by_volume(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 거래량 순위. ``exchange`` 는 거래소코드(NAS/NYS/AMS/HKS/SHS/SZS/TSE/HNX/HSX)."""
    path, tr = _TRADE_VOL
    params = {
        "EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
        "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": "",
    }
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_amount(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 거래대금 순위."""
    path, tr = _TRADE_AMOUNT
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_trade_growth(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 거래증가율 순위."""
    path, tr = _TRADE_GROWTH
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_market_cap(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 시가총액 순위."""
    path, tr = _MARKET_CAP
    params = {"EXCD": exchange, "VOL_RANG": "1",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_change(
    transport: Transport, *, exchange: str, top: str = "gainers"
) -> list[RankedOverseasStock]:
    """한 거래소의 등락률 순위. ``top="gainers"`` 상승률 / ``"losers"`` 하락률(원장 GUBN 1/0)."""
    try:
        gubn = _UPDOWN_GUBN[top]
    except KeyError:
        raise KISUsageError(f"top 은 {sorted(_UPDOWN_GUBN)} 중 하나: {top!r}") from None
    path, tr = _UPDOWN
    params = {"EXCD": exchange, "GUBN": gubn, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_volume_surge(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 거래량 급증 순위."""
    path, tr = _VOLUME_SURGE
    params = {"EXCD": exchange, "MINX": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_buy_strength(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 매수 체결강도 순위."""
    path, tr = _BUY_STRENGTH
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)


def fetch_by_turnover(transport: Transport, *, exchange: str) -> list[RankedOverseasStock]:
    """한 거래소의 거래 회전율 순위."""
    path, tr = _TURNOVER
    params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0",
              "KEYB": "", "AUTH": "", "PRC1": "", "PRC2": ""}
    return _fetch_ranking(transport, path=path, tr=tr, params=params)

"""국내주식 실시간 파서 -- 체결가(틱).

원장 Response Body 필드순을 그대로 ``^`` 인덱스에 매핑한다(필드순이 정본). 체결가 KRX/NXT/통합
(H0STCNT0/H0NXCNT0/H0UNCNT0)은 동일 46필드 레이아웃이라 파서를 공유한다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from .._registry import TRSpec, register
from ..messages import TradeTick

# H0STCNT0 응답 필드 순서(원장 Response Body). 인덱스 = ^ 위치.
_TRADE_TICK_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN",
    "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE", "NEW_MKOP_CLS_CODE",
    "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN",
    "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE", "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)


def _decimal(value: str) -> Decimal:
    """실시간 숫자 필드 -> Decimal. 빈 값/파싱 불가는 0 으로(스트림 중단 방지)."""
    try:
        return Decimal(value) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def parse_trade_tick(fields: list[str]) -> TradeTick:
    """H0STCNT0/H0NXCNT0/H0UNCNT0 한 레코드(46필드) -> :class:`TradeTick`."""
    raw = MappingProxyType(dict(zip(_TRADE_TICK_FIELDS, fields, strict=False)))
    return TradeTick(
        symbol=raw["MKSC_SHRN_ISCD"],
        time=raw["STCK_CNTG_HOUR"],
        current_price=_decimal(raw["STCK_PRPR"]),
        change_sign=raw["PRDY_VRSS_SIGN"],
        change=_decimal(raw["PRDY_VRSS"]),
        change_percent=_decimal(raw["PRDY_CTRT"]),
        open=_decimal(raw["STCK_OPRC"]),
        high=_decimal(raw["STCK_HGPR"]),
        low=_decimal(raw["STCK_LWPR"]),
        best_ask=_decimal(raw["ASKP1"]),
        best_bid=_decimal(raw["BIDP1"]),
        trade_volume=_decimal(raw["CNTG_VOL"]),
        accumulated_volume=_decimal(raw["ACML_VOL"]),
        accumulated_value=_decimal(raw["ACML_TR_PBMN"]),
        conclusion_strength=_decimal(raw["CTTR"]),
        trade_sign=raw["CCLD_DVSN"],
        business_date=raw["BSOP_DATE"],
        trading_halted=raw["TRHT_YN"] == "Y",
        static_vi_reference_price=_decimal(raw["VI_STND_PRC"]),
        _raw=raw,
    )


# KRX / NXT / 통합 체결가는 같은 46필드 레이아웃 -> 파서 공유.
for _tr_id in ("H0STCNT0", "H0NXCNT0", "H0UNCNT0"):
    register(TRSpec(_tr_id, field_count=len(_TRADE_TICK_FIELDS), parser=parse_trade_tick))

"""국내주식 매매(trading) 파사드 -- :class:`Trading`.

계좌 식별정보(``cano`` 계좌번호 앞 8자리, ``product_code`` 상품코드 뒤 2자리)를 갖고 잔고를
조회한다. KIS는 잔고조회를 매매(trading) 그룹에 두므로(주문과 같은 경로 계열) 여기 산다 --
주문 실행(buy/sell/place/cancel)은 주문 안전 코어(``orders``)가 맡고, 추후 이 파사드로 합류한다.

한 번의 잔고조회가 종목 배열(``output1``)과 계좌 요약(``output2``)을 함께 준다. KIS가 느린
heavy TR로 표시하므로(유량 주의), 둘 다 필요하면 :meth:`portfolio` 로 **한 번만** 조회한다.
:meth:`balance` 는 요약만(현금 확인용 1콜), :meth:`positions` 는 종목만 필요할 때 쓴다.

KIS URL/TR-id:
- 주식잔고조회: ``GET /uapi/domestic-stock/v1/trading/inquire-balance``
  실전 ``TTTC8434R`` / 모의 ``VTTC8434R`` (모의 지원).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from ..._wire import format_wire_decimal
from ...errors import KisError, KisUsageError
from ...transport import RawResponse, Transport
from .balance import Balance, Portfolio, Position, parse_balance, parse_positions
from .orderable import (
    BuyableAmount,
    SellableQuantity,
    parse_buyable_amount,
    parse_sellable_quantity,
)

_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "TTTC8434R", "demo": "VTTC8434R"}
_BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
_BUYABLE_TR = {"real": "TTTC8908R", "demo": "VTTC8908R"}
_SELLABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"
_SELLABLE_TR = "TTTC8408R"  # 모의투자 미지원 -- demo TR 없음

#: 잔고 종목배열 연속조회 페이지 상한(무한 루프 방지). 여기 닿으면 부분 결과로 자르지 않고
#: 예외로 fail-closed 한다(연속조회가 남았는데 멈추면 보유종목을 누락한다).
_MAX_BALANCE_PAGES = 100


class Trading:
    """한 계좌의 국내주식 매매/잔고 표면. 하나의 :class:`Transport` 를 공유한다."""

    def __init__(
        self,
        transport: Transport,
        *,
        cano: str,
        product_code: str,
        environment: Literal["real", "demo"] = "real",
    ) -> None:
        self._transport = transport
        self._cano = cano
        self._product_code = product_code
        self._environment = environment

    def balance(self) -> Balance:
        """계좌의 현금·자산 요약만 조회한다(1콜). 실패/요약 부재 시 :class:`KisError`.

        요약(``output2``)은 계좌 단위라 첫 페이지로 완결된다. 종목까지 필요하면 :meth:`portfolio`.
        """
        resp = self._fetch_balance_page("", "")
        _raise_if_error(resp)
        summary = _first_summary(resp.body)
        if summary is None:
            raise KisError(
                "잔고 응답에 계좌 요약(output2)이 없다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        return parse_balance(summary)

    def positions(self) -> list[Position]:
        """보유 종목 전체를 조회한다(연속조회 소진까지). 0수량 잔여 lot도 포함(필터는 호출자 몫)."""
        rows, _summary = self._walk_holdings()
        return parse_positions(rows)

    def portfolio(self) -> Portfolio:
        """현금·자산 요약과 보유 종목을 **한 번의 조회 순회로** 함께 얻는다.

        무거운 TR을 balance()+positions() 로 두 번 치는 대신, 종목 페이지를 연속조회로 끝까지
        모으면서 첫 페이지의 계좌 요약(``output2``)을 함께 취한다.
        """
        rows, summary = self._walk_holdings()
        if summary is None:
            raise KisError("잔고 응답에 계좌 요약(output2)이 없다.")
        return Portfolio(balance=parse_balance(summary), positions=tuple(parse_positions(rows)))

    # --- 주문가능 여력(사전점검) --------------------------------------
    def buyable_amount(
        self, symbol: str | None = None, *, limit_price: object | None = None
    ) -> BuyableAmount:
        """매수가능 여력을 조회한다 -- 현금 기준과 미수 포함 최대(주문 전 사전점검).

        ``symbol`` 을 생략하면 금액만 조회한다(종목별 수량 필드는 0). ``limit_price`` 를 주면
        지정가 기준으로, 생략하면 시장가 기준으로 계산한다(시장가는 증거금율을 반영해 최대수량을
        산정한다). ``symbol`` 없이 ``limit_price`` 만 주는 조합은 의미가 없어 :class:`KisUsageError`.
        숫자 아닌 ``limit_price`` 도 전송 전 :class:`KisUsageError`, 실패/응답 부재는 :class:`KisError`.
        """
        if symbol is None and limit_price is not None:
            raise KisUsageError("limit_price 는 symbol 과 함께 줘야 한다(금액만 조회엔 단가가 무의미).")
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._product_code,
            "PDNO": symbol or "",
            "ORD_UNPR": _format_order_unit_price(limit_price),
            "ORD_DVSN": "00" if limit_price is not None else "01",  # 지정가/시장가
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        resp = self._transport.request(
            method="GET", path=_BUYABLE_PATH, tr_id=_BUYABLE_TR[self._environment],
            params=params, idempotent=True,
        )
        _raise_if_error(resp)
        output = resp.body.get("output")
        if not isinstance(output, Mapping):
            raise KisError(
                "매수가능조회 응답에 output 이 없다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        return parse_buyable_amount(output, symbol=symbol or "")

    def sellable_quantity(self, symbol: str) -> SellableQuantity:
        """한 종목의 매도가능 수량을 조회한다. **모의투자 미지원**(demo면 사전 :class:`KisUsageError`).

        실패/응답(``output1``) 부재는 :class:`KisError`.
        """
        if self._environment == "demo":
            raise KisUsageError(
                "매도가능수량조회(inquire-psbl-sell)는 모의투자 미지원이다 -- 실전에서만 조회 가능. "
                "모의에서는 잔고조회의 sellable_quantity(ord_psbl_qty)를 참고하라."
            )
        params = {"CANO": self._cano, "ACNT_PRDT_CD": self._product_code, "PDNO": symbol}
        resp = self._transport.request(
            method="GET", path=_SELLABLE_PATH, tr_id=_SELLABLE_TR, params=params, idempotent=True
        )
        _raise_if_error(resp)
        output1 = resp.body.get("output1")
        if not isinstance(output1, Mapping):
            raise KisError(
                "매도가능수량조회 응답에 output1 이 없다.",
                rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
            )
        return parse_sellable_quantity(output1, symbol=symbol)

    # --- 내부 조회 ----------------------------------------------------
    def _walk_holdings(self) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
        """종목 배열(``output1``)을 연속조회로 끝까지 모으고, 첫 페이지의 계좌 요약을 함께 돌려준다.

        상한(``_MAX_BALANCE_PAGES``)까지 갔는데 연속조회가 남으면 부분 결과로 자르지 않고
        예외를 올린다(보유종목 누락 방지). ``output1`` 이 리스트가 아니면(손상) fail-closed.
        """
        rows: list[Mapping[str, Any]] = []
        summary: Mapping[str, Any] | None = None
        ctx_fk, ctx_nk = "", ""
        for _page in range(_MAX_BALANCE_PAGES):
            resp = self._fetch_balance_page(ctx_fk, ctx_nk)
            _raise_if_error(resp)
            if summary is None:  # 계좌 요약은 첫 페이지에서 취한다(계좌 단위라 페이지 불변)
                summary = _first_summary(resp.body)
            page = resp.body.get("output1")
            # 빈 계좌도 KIS는 output1 을 빈 배열로 준다 -> 부재/비배열은 손상으로 보고 fail-closed
            # (조용히 빈 결과로 두면 보유종목을 통째로 놓친다).
            if not isinstance(page, list):
                raise KisError(
                    "잔고 응답의 output1 이 종목 배열이 아니다.",
                    rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
                )
            rows.extend(page)
            ctx_nk = str(resp.body.get("ctx_area_nk100", "")).strip()
            ctx_fk = str(resp.body.get("ctx_area_fk100", "")).strip()
            if not ctx_nk:  # 연속조회 키 없음 -> 마지막 페이지
                break
        else:
            raise KisError(
                f"잔고 조회가 {_MAX_BALANCE_PAGES}페이지 상한에 도달했으나 연속조회가 남아있다 "
                f"-- 부분 결과로 자르지 않는다. 재시도하거나 수동 확인하라."
            )
        return rows, summary

    def _fetch_balance_page(self, ctx_fk: str, ctx_nk: str) -> RawResponse:
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._product_code,
            "AFHR_FLPR_YN": "N",              # 시간외단일가 아님(정규장 기준)
            "OFL_YN": "",
            "INQR_DVSN": "02",               # 02 종목별
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",               # 전일매매 포함
            "CTX_AREA_FK100": ctx_fk,
            "CTX_AREA_NK100": ctx_nk,
        }
        return self._transport.request(
            method="GET", path=_BALANCE_PATH, tr_id=_BALANCE_TR[self._environment],
            params=params, idempotent=True,  # 읽기 -- 타임아웃에 재시도해도 안전
        )


def _first_summary(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """잔고 요약(``output2``)을 꺼낸다 -- KIS가 '길이 1 배열'로 주기도, 단일 객체로 주기도 한다.

    배열의 첫 원소가 매핑이 아니면(손상) ``None`` 을 돌려 호출자가 :class:`KisError` 로 fail-closed
    하게 한다(``parse_balance`` 에 비매핑을 넘겨 ``AttributeError`` 를 내지 않도록).
    """
    summary = body.get("output2")
    if isinstance(summary, list):
        first = summary[0] if summary else None
        return first if isinstance(first, Mapping) else None
    if isinstance(summary, Mapping):
        return summary
    return None


def _format_order_unit_price(limit_price: object | None) -> str:
    """주문 단가를 KIS 와이어 정본 문자열로 -- ``None`` 이면 빈 문자열(시장가/금액만 조회).

    형제 주문 경로와 **같은** 정본(:func:`format_wire_decimal`)을 쓴다. 숫자가 아니거나
    유한 양수가 아니면(0/음수/NaN/Inf) 와이어에 실어 보내기 전에 :class:`KisUsageError` 로
    거른다(단가는 0보다 큰 유한값이어야 한다 -- ``Order`` 와 동일한 가드).
    """
    if limit_price is None:
        return ""
    try:
        price = Decimal(str(limit_price))
    except (ArithmeticError, ValueError) as err:
        raise KisUsageError(f"limit_price 는 숫자여야 한다: {limit_price!r}") from err
    if not price.is_finite() or price <= 0:
        raise KisUsageError(f"limit_price 는 0보다 큰 유한값이어야 한다: {limit_price!r}")
    return format_wire_decimal(price)


def _raise_if_error(resp: RawResponse) -> None:
    if not resp.ok:
        raise KisError(
            f"KIS 조회 요청 실패: {resp.msg1}",
            rt_cd=resp.rt_cd, msg_cd=resp.msg_cd, msg1=resp.msg1, raw=resp.body,
        )

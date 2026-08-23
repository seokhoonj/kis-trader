"""해외 계좌 조회에서 market 자동 -- positions/open_orders 를 market 생략 시 전체 시장 그룹 순회·합산.

KIS는 해외 잔고/미체결을 거래소그룹+통화(US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)별로만 준다. market 을
생략하면 7개 그룹을 모두 돌아 합쳐 준다. FakeTransport 로 네트워크 없이 검증.
"""

from __future__ import annotations

import threading

from kis_trader import KISClient
from kis_trader.overseas._engine._parse import _MARKETS
from kis_trader.transport import RawResponse

_ALL = list(_MARKETS)          # US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM


class FakeTransport:
    """OVRS_EXCG_CD 별로 종목 1개씩 돌려주는 가짜 전송(그룹당 1 페이지)."""

    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": dict(params or {})})
        excg = (params or {}).get("OVRS_EXCG_CD", "")
        if "inquire-balance" in path:      # 잔고: output1 종목 + output2 요약
            row = {"ovrs_pdno": f"SYM_{excg}", "ovrs_item_name": "x", "ovrs_cblc_qty": "1",
                   "ord_psbl_qty": "1", "pchs_avg_pric": "1", "frcr_pchs_amt1": "1",
                   "now_pric2": "1", "ovrs_stck_evlu_amt": "1", "frcr_evlu_pfls_amt": "0",
                   "evlu_pfls_rt": "0", "ovrs_excg_cd": excg}
            body = {"output1": [row], "output2": {}, "tr_cont": ""}
        else:                              # 미체결: output 배열
            body = {"output": [{"pdno": f"SYM_{excg}", "prdt_name": "x", "odno": "1",
                    "ft_ord_qty": "1", "ft_ccld_qty": "0", "nccs_qty": "1", "ft_ord_unpr3": "1",
                    "sll_buy_dvsn_cd": "02", "ovrs_excg_cd": excg}]}
        return RawResponse(rt_cd="0", msg_cd="0", msg1="", body=body, tr_cont="")


def _client(transport):
    return KISClient(app_key="k", app_secret="s", account="12345678-01",
                     environment="real", transport=transport)


def test_positions_market_omitted_visits_all_groups():
    fake = FakeTransport()
    k = _client(fake)
    positions = k.account.overseas.positions()          # market 생략
    # 7개 그룹 각각 조회했나 (각 그룹당 최소 1콜)
    seen = {c["params"].get("OVRS_EXCG_CD") for c in fake.calls}
    assert {_MARKETS[m][0] for m in _ALL} <= seen
    # 합산 결과가 그룹 수만큼
    assert len(positions) == len(_ALL)


def test_positions_market_given_hits_only_that_group():
    fake = FakeTransport()
    k = _client(fake)
    k.account.overseas.positions(market="US")
    excgs = {c["params"].get("OVRS_EXCG_CD") for c in fake.calls}
    assert excgs == {"NASD"}                              # US 그룹만


def test_open_orders_market_omitted_visits_all_groups():
    fake = FakeTransport()
    k = _client(fake)
    orders = k.account.overseas.open_orders()
    seen = {c["params"].get("OVRS_EXCG_CD") for c in fake.calls}
    assert {_MARKETS[m][0] for m in _ALL} <= seen
    assert len(orders) == len(_ALL)


def test_flat_verb_also_aggregates():
    # 네임스페이스뿐 아니라 flat verb 도 같은 동작(둘 다 같은 엔진).
    fake = FakeTransport()
    k = _client(fake)
    assert len(k.account.overseas.positions()) == len(_ALL)

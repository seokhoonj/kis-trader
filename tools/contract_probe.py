"""라이브 contract probe -- 실서버로 계약 확인(요청오류·빈값·패딩). 로컬/세션 전용, CI 아님.
공개 집계만 출력. 시크릿/실데이터 값 출력 금지."""
from __future__ import annotations

import argparse

from kis_trader import KISClient
from tools.contracts import load_contracts

# 엔드포인트 name -> (세션에서 호출하는 recipe). 위험군만(YAGNI). 안전 샘플 인자.
RECIPES = {
    "overseas_index_quote": lambda k: k.overseas.index(".DJI", kind="index").quote(),
    "overseas_ranking_change": lambda k: k.overseas.ranking.by_change(exchange="NAS"),
    "overseas_ranking_market_cap": lambda k: k.overseas.ranking.by_market_cap(exchange="NAS"),
    "overseas_ranking_new_highlow": lambda k: k.overseas.ranking.by_new_high_low(exchange="NAS"),
    "overseas_ranking_execution_strength": lambda k: k.overseas.ranking.by_execution_strength(exchange="NAS"),
    "overseas_ranking_sharp_change": lambda k: k.overseas.ranking.by_sharp_change(exchange="NAS"),
    "overseas_ranking_volume_surge": lambda k: k.overseas.ranking.by_volume_surge(exchange="NAS"),
    "stock_bars_1m": lambda k: k.domestic.stock("005930").bars(interval="1m"),
    # 재무·가치·수익자산 랭킹: 공유 헬퍼 _fundamentals_params 의 13개 FID 키가 실서버에서 수락되는지
    # (rt_cd=0) 확인한다. 거부되면 ERROR(예: INPUT FIELD NOT FOUND). year 는 결산 완료된 최근 회계연도.
    "domestic_ranking_financial_ratio": lambda k: k.domestic.ranking.by_financial_ratio(year=2024),
    "domestic_ranking_valuation": lambda k: k.domestic.ranking.by_valuation(year=2024),
    "domestic_ranking_profit_asset": lambda k: k.domestic.ranking.by_profit_asset(year=2024),
}


def _rows(result) -> list:
    if isinstance(result, list):
        return result
    pts = getattr(result, "points", None) or getattr(result, "ticks", None)
    return list(pts) if pts is not None else [result]


def _anomalies(contract, rows: list) -> list[str]:
    flags = []
    if "no_trailing_zero_volume_padding" in contract.invariants:
        trailing = 0  # 꼬리에서부터 연속된 vol=0 봉만 센다(장중 저유동 0-vol 은 패딩 아님)
        for r in reversed(rows):
            if getattr(r, "volume", None) != 0:
                break
            trailing += 1
        if trailing:
            flags.append(f"trailing zero-volume bars: {trailing}/{len(rows)}")
    if "nonempty_when_market_open" in contract.invariants and not rows:
        flags.append("EMPTY (시장 열림 가정 시 이상; 닫혔으면 deferred)")
    return flags


def main() -> None:
    ap = argparse.ArgumentParser(description="라이브 contract probe (실서버; 로컬 전용)")
    ap.add_argument("--profile", default="general")
    ap.add_argument("--area")
    ap.add_argument("--names")
    args = ap.parse_args()

    contracts = {c.name: c for c in load_contracts(args.area)}
    names = args.names.split(",") if args.names else list(contracts)

    kis = KISClient(profile=args.profile, environment="real", orderable=False)
    try:
        for name in names:
            c = contracts.get(name)
            recipe = RECIPES.get(name)
            if c is None or recipe is None:
                print(f"[{name}] SKIP (no contract or no recipe)")
                continue
            try:
                result = recipe(kis)
            except Exception as e:  # noqa: BLE001 -- probe 는 모든 실패를 기록
                print(f"[{name}] ERROR {type(e).__name__}: {str(e)[:100]}")
                continue
            rows = _rows(result)
            flags = _anomalies(c, rows)
            status = "ANOMALY" if flags else "OK"
            print(f"[{name}] {status} n={len(rows)} {'| ' + '; '.join(flags) if flags else ''}")
    finally:
        kis.close()


if __name__ == "__main__":
    main()

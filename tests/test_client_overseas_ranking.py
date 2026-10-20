"""해외주식 순위 -- kis.overseas.ranking.by_volume(). 필드는 원장 응답예시 실값."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kis_trader import KISClient, OverseasRankedStock, OverseasRankingQueries, TurnoverRanking
from kis_trader.errors import KISError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, *, response):
        self.response = response
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, *, method, path, tr_id, params=None, body=None, idempotent):
        with self._lock:
            self.calls.append({"path": path, "tr_id": tr_id, "params": params})
        return self.response


def _client(t):
    return KISClient(app_key="k", app_secret="s", environment="real", transport=t)


def _resp(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}, "output2": rows})


def test_overseas_ranking_accessor():
    assert isinstance(_client(FakeTransport(response=_resp([]))).overseas.ranking,
                      OverseasRankingQueries)


def test_by_volume_maps_and_params():
    rows = [{"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "테슬라", "ename": "TESLA",
             "last": "250.5", "sign": "2", "diff": "5.5", "rate": "2.24", "tvol": "120000000",
             "tamt": "30000000000", "a_tvol": "90000000"}]
    fake = FakeTransport(response=_resp(rows))
    ranked = _client(fake).overseas.ranking.by_volume(exchange="NAS")
    assert isinstance(ranked[0], OverseasRankedStock)
    ranked_stock = ranked[0]
    assert ranked_stock.rank == 1
    assert ranked_stock.exchange == "NAS"
    assert ranked_stock.symbol == "TSLA"
    assert ranked_stock.current_price == Decimal("250.5")
    assert ranked_stock.change == Decimal("5.5")                    # sign 2 -> 상승
    assert ranked_stock.volume == 120000000
    assert ranked_stock.trading_amount == Decimal(30000000000)   # tamt 거래대금
    assert ranked_stock._raw["a_tvol"] == "90000000"                # 평균거래량은 _raw
    call = fake.calls[0]
    assert call["path"] == "/uapi/overseas-stock/v1/ranking/trade-vol"
    assert call["tr_id"] == "HHDFS76310010"
    assert call["params"]["EXCD"] == "NAS"


def test_ranking_parses_rankless_knam_schema():
    # new-highlow/volume-surge 류 응답: 명시 rank 없음(순위=행 순서), 이름은 knam/enam (라이브 2026-10-16).
    rows = [
        {"excd": "NAS", "symb": "AAA", "knam": "에이", "enam": "AAA Inc",
         "last": "10", "sign": "2", "diff": "1", "rate": "1.0", "tvol": "5"},
        {"excd": "NAS", "symb": "BBB", "knam": "비이", "enam": "BBB Inc",
         "last": "20", "sign": "5", "diff": "2", "rate": "1.5", "tvol": "6"},
    ]
    ranked = _client(FakeTransport(response=_resp(rows))).overseas.ranking.by_new_high_low(exchange="NAS")
    assert [r.rank for r in ranked] == [1, 2]             # rank 필드 없음 -> 행 순서
    assert ranked[0].name == "에이" and ranked[0].english_name == "AAA Inc"
    assert ranked[1].symbol == "BBB"
    assert ranked[1].change == Decimal(-2)              # sign 5 -> 하락


def test_rankless_skips_blank_row_without_rank_gap():
    # rank 없는 응답에 빈 symbol 패딩행이 중간에 끼어도 유효행은 연속 순위(공백 없음).
    rows = [
        {"excd": "NAS", "symb": "AAA", "knam": "에이", "last": "10", "sign": "2", "diff": "1", "rate": "1", "tvol": "5"},
        {"excd": "NAS", "symb": "",    "knam": "",   "last": "",   "sign": "",  "diff": "",  "rate": "",  "tvol": ""},
        {"excd": "NAS", "symb": "CCC", "knam": "씨이", "last": "30", "sign": "2", "diff": "1", "rate": "1", "tvol": "7"},
    ]
    ranked = _client(FakeTransport(response=_resp(rows))).overseas.ranking.by_new_high_low(exchange="NAS")
    assert [r.rank for r in ranked] == [1, 2]             # 빈행 건너뛰고 1,2 연속 (3 아님)
    assert [r.symbol for r in ranked] == ["AAA", "CCC"]


def test_mixed_present_and_missing_rank_fails_closed():
    # 명시 rank 가 하나라도 있으면 전 행이 그 값을 써야 한다 -- 누락행은 조용히 위치로 때우지 않고 fail-closed.
    rows = [
        {"rank": "1", "excd": "NAS", "symb": "AAA", "name": "에이", "last": "10", "sign": "2", "diff": "1", "rate": "1", "tvol": "5"},
        {"excd": "NAS", "symb": "BBB", "name": "비이", "last": "20", "sign": "2", "diff": "1", "rate": "1", "tvol": "6"},
    ]
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(rows))).overseas.ranking.by_new_high_low(exchange="NAS")


def test_by_volume_blank_tamt_reads_none():
    # 라이브에서 빈 tamt 가 와도 크래시하지 않고 trading_amount 는 None (optional 완화 회귀 고정).
    rows = [{"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "테슬라", "ename": "TESLA",
             "last": "250.5", "sign": "2", "diff": "5.5", "rate": "2.24", "tvol": "120000000",
             "tamt": "", "a_tvol": "90000000"}]
    fake = FakeTransport(response=_resp(rows))
    assert _client(fake).overseas.ranking.by_volume(exchange="NAS")[0].trading_amount is None


def test_by_volume_down_sign_and_missing_output():
    rows = [{"rank": "2", "excd": "NAS", "symb": "X", "name": "x", "ename": "X", "last": "10",
             "sign": "5", "diff": "1.0", "rate": "9.1", "tvol": "1", "tamt": "10"}]
    fake = FakeTransport(response=_resp(rows))
    assert _client(fake).overseas.ranking.by_volume(exchange="NAS")[0].change == Decimal("-1.0")
    fake2 = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}}))
    with pytest.raises(KISError):
        _client(fake2).overseas.ranking.by_volume(exchange="NAS")


def test_overseas_ranking_non_mapping_row_fails_closed():
    rows = [
        {"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "테슬라", "ename": "TESLA",
         "last": "250.5", "sign": "2", "diff": "5.5", "rate": "2.24", "tvol": "120000000",
         "tamt": "30000000000"},
        None,
    ]
    with pytest.raises(KISError):
        _client(FakeTransport(response=_resp(rows))).overseas.ranking.by_volume(exchange="NAS")


def test_by_volume_bad_value_fails_closed():
    rows = [{"rank": "1", "excd": "NAS", "symb": "X", "name": "x", "ename": "X", "last": "n/a",
             "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    fake = FakeTransport(response=_resp(rows))
    with pytest.raises(KISError):
        _client(fake).overseas.ranking.by_volume(exchange="NAS")


def _one(rows):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}, "output2": rows})


def test_by_trading_amount_growth_market_cap_route_correctly():
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    cases = [
        ("by_trading_amount", "/uapi/overseas-stock/v1/ranking/trade-pbmn", "HHDFS76320010"),
        ("by_volume_growth", "/uapi/overseas-stock/v1/ranking/trade-growth", "HHDFS76330000"),
        ("by_market_cap", "/uapi/overseas-stock/v1/ranking/market-cap", "HHDFS76350100"),
    ]
    for verb, path, tr in cases:
        fake = FakeTransport(response=_one(row))
        result = getattr(_client(fake).overseas.ranking, verb)(exchange="NAS")
        assert result[0].symbol == "T"
        assert fake.calls[0]["path"] == path
        assert fake.calls[0]["tr_id"] == tr
        assert fake.calls[0]["params"]["EXCD"] == "NAS"


def test_by_change_gubn_and_bad_direction():
    import pytest as _pytest

    from kis_trader.errors import KISUsageError as _U
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas.ranking.by_change(exchange="NAS", direction="gainers")
    assert fake.calls[0]["path"] == "/uapi/overseas-stock/v1/ranking/updown-rate"
    assert fake.calls[0]["params"]["GUBN"] == "1"                # gainers=1(상승율)
    fake2 = FakeTransport(response=_one(row))
    _client(fake2).overseas.ranking.by_change(exchange="NAS", direction="losers")
    assert fake2.calls[0]["params"]["GUBN"] == "0"               # losers=0(하락율)
    with _pytest.raises(_U):
        _client(fake2).overseas.ranking.by_change(exchange="NAS", direction="nope")


def test_more_overseas_rankings_route():
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1",
            "tover": "1", "shar": "1"}]   # tover/shar: turnover 전용 필드(그 외 순위는 무시)
    cases = [
        ("by_volume_surge", "/uapi/overseas-stock/v1/ranking/volume-surge", "HHDFS76270000", "MINX"),
        ("by_execution_strength", "/uapi/overseas-stock/v1/ranking/volume-power", "HHDFS76280000", "NDAY"),
        ("by_turnover", "/uapi/overseas-stock/v1/ranking/trade-turnover", "HHDFS76340000", "NDAY"),
    ]
    for verb, path, tr, key in cases:
        fake = FakeTransport(response=_one(row))
        assert getattr(_client(fake).overseas.ranking, verb)(exchange="NAS")[0].rank == 1
        assert fake.calls[0]["path"] == path
        assert fake.calls[0]["tr_id"] == tr
        assert key in fake.calls[0]["params"]


def test_by_turnover_maps_dedicated_entity():
    # 거래 회전율 순위는 전용 엔티티(TurnoverRanking). _raw 실키: tover(회전율)·shar(상장주식수),
    # tamt 없음 -> trading_amount None. 명시 rank 있음(라이브 2026-10-16 ranking-values.txt).
    rows = [{"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "테슬라", "ename": "TESLA",
             "last": "250.5", "sign": "5", "diff": "5.5", "rate": "2.24", "tvol": "120000000",
             "tover": "0.37", "shar": "3180000000"}]
    fake = FakeTransport(response=_one(rows))
    ranked = _client(fake).overseas.ranking.by_turnover(exchange="NAS")
    assert isinstance(ranked[0], TurnoverRanking)
    turnover_ranking = ranked[0]
    assert turnover_ranking.rank == 1
    assert turnover_ranking.exchange == "NAS"
    assert turnover_ranking.symbol == "TSLA"
    assert turnover_ranking.name == "테슬라" and turnover_ranking.english_name == "TESLA"
    assert turnover_ranking.current_price == Decimal("250.5")
    assert turnover_ranking.change == Decimal("-5.5")                   # sign 5 -> 하락
    assert turnover_ranking.volume == 120000000
    assert turnover_ranking.trading_amount is None                      # tamt 없음
    assert turnover_ranking.turnover_ratio == Decimal("0.37")           # tover 거래회전율(%)
    assert turnover_ranking.shares_outstanding == 3180000000            # shar 상장주식수
    assert turnover_ranking._raw["tover"] == "0.37"
    assert fake.calls[0]["path"] == "/uapi/overseas-stock/v1/ranking/trade-turnover"
    assert fake.calls[0]["tr_id"] == "HHDFS76340000"


def test_by_turnover_missing_output_fails_closed():
    # 성공 응답인데 output2 배열이 없으면 fail-closed (자매 by_volume 과 동일 보장).
    fake = FakeTransport(response=RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={"output1": {}}))
    with pytest.raises(KISError):
        _client(fake).overseas.ranking.by_turnover(exchange="NAS")


def test_by_turnover_blank_required_value_fails_closed():
    # 전용 지표 tover/shar 는 required -- 빈 값이면 0 으로 조작하지 않고 fail-closed.
    rows = [{"rank": "1", "excd": "NAS", "symb": "TSLA", "name": "t", "ename": "T", "last": "250",
             "sign": "2", "diff": "5", "rate": "2", "tvol": "1", "tover": "", "shar": "1"}]
    fake = FakeTransport(response=_one(rows))
    with pytest.raises(KISError):
        _client(fake).overseas.ranking.by_turnover(exchange="NAS")


def test_price_fluct_and_new_highlow_gubn_codes():
    row = [{"rank": "1", "excd": "NAS", "symb": "T", "name": "n", "ename": "N", "last": "1",
            "sign": "2", "diff": "1", "rate": "1", "tvol": "1", "tamt": "1"}]
    # price-fluct: gainers -> GUBN 1, losers -> GUBN 0
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas.ranking.by_sharp_change(exchange="NAS", direction="losers")
    assert fake.calls[0]["path"] == "/uapi/overseas-stock/v1/ranking/price-fluct"
    assert fake.calls[0]["tr_id"] == "HHDFS76260000"
    assert fake.calls[0]["params"]["GUBN"] == "0"
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas.ranking.by_sharp_change(exchange="NAS", direction="gainers")
    assert fake.calls[0]["params"]["GUBN"] == "1"
    # new-highlow: high/low -> GUBN 1/0, sustained -> GUBN2 1/0
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas.ranking.by_new_high_low(exchange="NAS", extreme="low", sustained=False)
    assert fake.calls[0]["path"] == "/uapi/overseas-stock/v1/ranking/new-highlow"
    assert fake.calls[0]["tr_id"] == "HHDFS76300000"
    assert fake.calls[0]["params"]["GUBN"] == "0"
    assert fake.calls[0]["params"]["GUBN2"] == "0"
    fake = FakeTransport(response=_one(row))
    _client(fake).overseas.ranking.by_new_high_low(exchange="NAS")   # 기본 high/sustained
    assert fake.calls[0]["params"]["GUBN"] == "1"
    assert fake.calls[0]["params"]["GUBN2"] == "1"
    # new_highlow 는 NDAY 와 MINX 를 둘 다 요구한다 -- NDAY 누락 시 'INPUT FIELD NOT FOUND [NDAY]'
    # (2026-10-16 US 장중 라이브로 확정). PRC1/PRC2 는 이 엔드포인트에 없다.
    params = fake.calls[0]["params"]
    assert params["MINX"] == "0"
    assert params["NDAY"] == "0"
    assert "PRC1" not in params and "PRC2" not in params


def test_overseas_ranking_bad_direction_raises():
    import pytest

    from kis_trader.errors import KISUsageError
    fake = FakeTransport(response=_one([]))
    with pytest.raises(KISUsageError):
        _client(fake).overseas.ranking.by_sharp_change(exchange="NAS", direction="nope")

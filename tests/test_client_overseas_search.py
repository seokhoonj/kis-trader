"""해외주식 조건검색."""

from decimal import Decimal

import pytest

from kis_trader import KISClient, OverseasStockSearch
from kis_trader.errors import KISError, KISUsageError
from kis_trader.transport import RawResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(
        self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""
    ):
        self.calls.append({"path": path, "tr_id": tr_id, "params": params, "tr_cont": tr_cont})
        return next(self.responses)


def _response(symbol, *, excd="NAS", tr_cont=""):
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont, body={
        "output1": {"zdiv": "2", "stat": "Y", "crec": "1", "trec": "2", "nrec": "1"},
        "output2": [{"rsym": f"D{excd}{symbol}", "excd": excd, "name": symbol,
                     "ename": symbol, "symb": symbol, "last": "160.5", "shar": "1000",
                     "valx": "160500", "plow": "159", "phigh": "162", "popen": "160",
                     "tvol": "10000", "rate": "1.25", "diff": "2", "sign": "2",
                     "avol": "1600", "eps": "6.5", "per": "24.7", "rank": "1",
                     "e_ordyn": "○"}],
    })


def test_overseas_search_maps_filters():
    # KIS 조건검색(HHDFS76410000)은 다음조회를 지원하지 않는다 -- 단일 응답(최대 100건)을 그대로 매핑한다.
    fake = FakeTransport([_response("AAPL")])
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    result = client.overseas.search_stocks(
        "NAS", price=(160, 200), per=(10, 30), volume=(1000, 100000)
    )
    assert isinstance(result, OverseasStockSearch)
    assert len(result.matches) == 1
    assert result.matches[0].price == Decimal("160.5")
    assert result.matches[0].is_tradable   # e_ordyn "○" = 매매가능(라이브-프로브 확정; "O"/"Y" 아님)
    assert result.total_count == 2
    params = fake.calls[0]["params"]
    assert fake.calls[0]["tr_id"] == "HHDFS76410000"
    assert params["KEYB"] == ""                # 다음조회 미지원 -- KEYB 공백 고정
    assert (params["CO_YN_PRICECUR"], params["CO_ST_PRICECUR"], params["CO_EN_PRICECUR"]) == ("1", "160", "200")
    # 모든 필터가 전달되는지(price 만이 아니라 per/volume 도) -- **filters forward 회귀 가드.
    assert (params["CO_YN_PER"], params["CO_ST_PER"], params["CO_EN_PER"]) == ("1", "10", "30")
    assert (params["CO_YN_VOLUME"], params["CO_ST_VOLUME"], params["CO_EN_VOLUME"]) == ("1", "1000", "100000")
    assert params["CO_YN_RATE"] == ""          # 미지정 필터는 빈 값
    assert len(fake.calls) == 1                # 단일 조회 -- 페이지 루프 없음


def test_overseas_search_us_fans_out_and_merges():
    # "US" 는 KIS 조건검색을 나스닥·뉴욕·아멕스에 각각 걸어 합친다(TR 이 거래소당 한 번이라).
    fake = FakeTransport([_response("AAPL", excd="NAS"), _response("KO", excd="NYS"),
                          _response("GME", excd="AMS")])
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    result = client.overseas.search_stocks("US", price=(10, 500))
    assert isinstance(result, OverseasStockSearch)
    assert result.exchange == "US"
    assert [c["params"]["EXCD"] for c in fake.calls] == ["NAS", "NYS", "AMS"]
    assert [m.symbol for m in result.matches] == ["AAPL", "KO", "GME"]
    # 합쳐진 match 는 자기 원래 거래소를 유지한다(집계가 뭉개지 않는다).
    assert [m.exchange for m in result.matches] == ["NAS", "NYS", "AMS"]
    assert result.total_count == 6                   # trec(2) x 3 거래소


def test_overseas_search_us_lowercase_also_fans_out():
    fake = FakeTransport([_response("AAPL", excd="NAS"), _response("KO", excd="NYS"),
                          _response("GME", excd="AMS")])
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    result = client.overseas.search_stocks("us", price=(10, 500))   # 소문자도 동일
    assert [c["params"]["EXCD"] for c in fake.calls] == ["NAS", "NYS", "AMS"]
    assert len(result.matches) == 3


def test_overseas_search_us_partial_failure_propagates():
    # 뒤 거래소 조회가 실패하면 부분 결과를 내지 않고 전체가 실패한다(fail-closed).
    err = RawResponse(rt_cd="1", msg_cd="E", msg1="fail", tr_cont="", body={})
    fake = FakeTransport([_response("AAPL", excd="NAS"), err])
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    with pytest.raises(KISError):
        client.overseas.search_stocks("US", price=(10, 500))


def test_overseas_search_rejects_blank_exchange():
    with pytest.raises(KISUsageError):
        KISClient(app_key="k", app_secret="s", environment="real", transport=FakeTransport([])).overseas.search_stocks("")


def test_overseas_search_missing_output_fails_closed():
    response = RawResponse(rt_cd="0", msg_cd="X", msg1="ok", body={})
    with pytest.raises(KISError):
        KISClient(app_key="k", app_secret="s", environment="real", transport=FakeTransport([response])).overseas.search_stocks("NAS")


def _page(*, count, first_rank, trec, tr_cont, excd="NAS"):
    """연속조회 한 페이지 -- count 건을 first_rank 부터 증가하는 누적순위로 채운다."""
    rows = [{"rsym": f"D{excd}S{rank}", "excd": excd, "name": f"S{rank}",
             "ename": f"S{rank}", "symb": f"S{rank}", "last": "160.5", "shar": "1000",
             "valx": "160500", "plow": "159", "phigh": "162", "popen": "160",
             "tvol": "10000", "rate": "1.25", "diff": "2", "sign": "2",
             "avol": "1600", "eps": "6.5", "per": "24.7", "rank": str(rank),
             "e_ordyn": "○"}
            for rank in range(first_rank, first_rank + count)]
    return RawResponse(rt_cd="0", msg_cd="X", msg1="ok", tr_cont=tr_cont, body={
        "output1": {"zdiv": "2", "stat": "Y", "crec": str(count), "trec": str(trec),
                    "nrec": str(count)},
        "output2": rows,
    })


def test_overseas_search_paginates_with_rank_cursor():
    # 조건검색은 100건/페이지 + tr_cont="F" 로 더 있음을 알린다. 다음 KEYB = 직전 페이지 마지막 행의
    # 누적순위(rank), tr_cont="N". 3페이지(100/100/50)가 전부 모이고 커서가 올바른지 가드한다.
    pages = [_page(count=100, first_rank=1, trec=250, tr_cont="F"),
             _page(count=100, first_rank=101, trec=250, tr_cont="F"),
             _page(count=50, first_rank=201, trec=250, tr_cont="")]
    fake = FakeTransport(pages)
    client = KISClient(app_key="k", app_secret="s", environment="real", transport=fake)
    result = client.overseas.search_stocks("NAS", price=(10, 500))
    assert len(result.matches) == 250                     # 전 페이지 누적
    assert result.total_count == 250                       # output1.trec 그대로
    assert [m.rank for m in result.matches[:3]] == [1, 2, 3]
    assert result.matches[-1].rank == 250
    assert len(fake.calls) == 3
    # 1페이지: KEYB 공백 + tr_cont 공백.
    assert fake.calls[0]["params"]["KEYB"] == ""
    assert fake.calls[0]["tr_cont"] == ""
    # 2·3페이지: 직전 페이지 마지막 rank 를 KEYB 로, tr_cont="N".
    assert fake.calls[1]["params"]["KEYB"] == "100"
    assert fake.calls[1]["tr_cont"] == "N"
    assert fake.calls[2]["params"]["KEYB"] == "200"
    assert fake.calls[2]["tr_cont"] == "N"


def test_overseas_search_stuck_cursor_fails_closed():
    # 서버가 계속 "더 있음"(tr_cont="F")만 돌려주면(커서가 진전 못 함) 상한에서 fail-closed --
    # 부분 결과로 자르지 않고 KISError 를 던진다.
    class _StuckTransport:
        def __init__(self):
            self.calls = 0

        def request(self, *, method, path, tr_id, params=None, body=None, idempotent, tr_cont=""):
            self.calls += 1
            return _page(count=100, first_rank=1, trec=99999, tr_cont="F")

    client = KISClient(app_key="k", app_secret="s", environment="real", transport=_StuckTransport())
    with pytest.raises(KISError):
        client.overseas.search_stocks("NAS", price=(10, 500))

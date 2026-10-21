# 시장·지수

개별 종목이 아닌 **시장 전체**의 투자자 수급·프로그램매매·자금·제도, 그리고 **지수**. 시장
집계는 `kis.domestic.market`, 지수는 `kis.domestic.index(...)` 핸들로 봅니다. (한 종목의 수급은
[종목 수급](flows.md).)

## 투자자 수급

```python
market = kis.domestic.market

market.investor_flows()                                             # 시장 전체 투자자 동향
market.investor_net_buy_stocks()                                    # 투자자별 순매수 상위 종목
market.investor_snapshot(market_code="0001", industry_code="0001")  # 시장·업종별 세부 매수/매도/순매수
```

## 프로그램 매매

```python
market.program_trades()           # 일별 프로그램매매 종합(차익/비차익; KOSPI/KOSDAQ)
market.program_flow()             # 당일 시간대별 프로그램 순매수 대금
market.program_investor_trades()  # 당일 프로그램매매 투자자 집계
market.foreign_broker_trades()    # 외국계 창구 매매
```

## 자금·금리

```python
market.funds()           # 증시자금 종합(예탁금·신용융자잔고·펀드유형별·시가총액) 추이
market.interest_rates()  # 국내·해외 주요 금리·채권지수 스냅샷
```

## 제도·상태

```python
market.limit_stocks()             # 상한가/하한가 도달 종목
market.vi_events()                # VI(변동성완화장치) 발동 현황
market.lendable_stocks()          # 대주 가능 종목·한도
market.credit_eligible_stocks()   # 신용주문 가능·불가 종목
market.futures_market_schedule()  # 국내선물 인접 영업일·장 시작/종료
```

## 의견·뉴스

```python
market.broker_opinions(member_code="003")  # 한 증권사(회원사 코드)가 낸 종목 투자의견·목표가
market.news()                              # 시황·공시 뉴스 제목 피드
```

## 지수

지수 핸들은 `kis.domestic.index(이름_또는_코드)` — 이름(`"KOSPI"`)이나 업종코드(`"0001"`) 둘 다 됩니다.

```python
index = kis.domestic.index("KOSPI")

index.quote()                       # 지수 현재가
index.bars("1d", start="20240101")  # 지수 차트 (일/주/월)
index.categories()                  # 시장 요약 + 하위 업종 지수 목록
index.recent_prices()               # 스냅샷 + 최근 최대 100건 일/주/월 지수 시세+보조지표
index.intraday()                    # 당일 시간대별 (1m/5m/10m)
index.ticks()                       # 당일 10초 시계열
```

예상체결 지수:

```python
index.expected_snapshot()  # 동시호가 대표 예상체결 지수 + 시장별 목록
index.expected_trend()     # 장 시작 전·마감 예상체결 지수 추이
```

## 해외 지수·환율·국채·금

해외 지수류는 `kis.overseas.index(코드, kind=...)` 로 봅니다 -- `kind` 는 `index`(지수)·`fx`(환율)·
`bond`(국채)·`gold`(금선물). 모든 종류가 현재가 스냅샷을 주며, 지수·환율은 기간봉(일/주/월)과
분봉(`1m`)을, 국채·금은 기간봉만 줍니다.

```python
kis.overseas.index("SPX").quote()                                     # S&P500 현재가
kis.overseas.index_quotes(["SPX", "NDX"])                             # 여러 해외지수 현재가
kis.overseas.index("SPX").bars("1d", start="20240101")                # S&P500 기간봉
kis.overseas.index("FX@KRW", kind="fx").bars("1d", start="20240101")  # 환율(원/달러) 히스토리
kis.overseas.index("FX@KRW", kind="fx").bars("1m", max_bars=100)      # 환율 당일 분봉
```

기간봉(`1d`/`1wk`/`1mo`)은 `start` 가 필요하고 `end` 는 생략 시 오늘까지입니다(분봉 `1m` 만 생략 가능).

::: {.callout-note}
## 알아두면 좋은 용어
- **VI (변동성완화장치)** — 가격이 급변하면 단일가매매로 전환하는 안전장치.
  [KRX 안내](https://data.krx.co.kr/contents/MDC/STAT/issue/MDCSTAT224.jsp)는 기본 2분과 30초 이내의
  임의종료시간을 설명하며, 단일가매매가 연장되면 실제 해제시각도 늦어질 수 있습니다. `vi_events` 로 발동 현황.
- **프로그램매매** — 여러 종목을 바스켓으로 한 번에 사고파는 기관 매매. **차익**(선물-현물 차익거래)과
  **비차익**으로 나뉩니다. 일별 종합은 `program_trades`, 당일 시간대별 흐름은 `program_flow`.
- **대주(貸株)** — 없는 주식을 빌려서 파는 공매도의 재원. `lendable_stocks` 로 빌릴 수 있는 종목·한도를 봅니다.
:::

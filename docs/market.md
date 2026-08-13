# 시장·수급

한 종목의 수급, 그리고 시장 전체의 흐름을 본다.

## 종목 수급 (외국인·기관)

```python
s = kis.domestic.stock("005930")

s.investor_flows()             # 개인/외국인/기관 매매동향
s.foreign_net_buy_trend()      # 외국인 순매수 추이
s.detailed_investor_history()  # 상세 투자자별
```

## 프로그램 매매

```python
s.program_trades()        # 종목 프로그램 매매
s.daily_program_trades()  # 일별
```

## 공매도·신용

```python
s.short_sale_trend()      # 공매도 추이
s.credit_balance_trend()  # 신용잔고 추이
```

## 시장 전체 수급

`kis.domestic.market` — 개별 종목이 아닌 **시장 전체** 투자자 수급·지수.

```python
m = kis.domestic.market

m.investor_flows()           # 시장 전체 투자자 동향
m.investor_net_buy_stocks()  # 투자자별 순매수 상위 종목
m.program_trades()           # 시장 프로그램 매매
m.foreign_broker_trades()    # 외국계 창구 매매
m.broker_opinions()          # 증권사 의견
m.news()                     # 뉴스
m.vi_events()                # VI 발동 현황
m.interest_rates()           # 시장 금리
```

::: {.callout-note}
## 알아두면 좋은 용어
- **VI (변동성완화장치)** — 가격이 급변하면 2~10분 단일가로 전환하는 안전장치. `vi_events` 로 발동 현황.
- **프로그램매매** — 여러 종목을 바스켓으로 한 번에 사고파는 기관 매매. **차익**(선물-현물 차익거래)과
  **비차익**으로 나뉜다.
- **수급** — 누가 사고 파는지(개인·외국인·기관). 외국인/기관 순매수는 방향성 힌트로 자주 본다.
:::

## 캘린더 (배당·공모·권리)

`kis.domestic.calendar.*` — 시장 전체 일정.

```python
c = kis.domestic.calendar

c.dividends(start="20240101", end="20241231")   # 배당
c.ipo_subscriptions(start="20240101", end="…")  # 공모주 청약
c.rights_offerings(…)                           # 유상증자
c.bonus_issues(…)                               # 무상증자
c.shareholder_meetings(…)                       # 주주총회
c.merger_splits(…)                              # 합병·분할
c.listings(…)                                   # 상장
```

::: {.callout-note}
계좌에 배정된 **내** 권리 내역은 [계좌](account.md)의 `account.rights(...)` 로 본다.
여기 캘린더는 시장 전체 일정이다.
:::

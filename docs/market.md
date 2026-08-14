# 시장 전체

개별 종목이 아닌 **시장 전체**의 투자자 수급·프로그램매매·자금·제도 정보. `kis.domestic.market`
에 모여 있습니다. (한 종목의 수급은 [종목 수급](flows.md).)

## 투자자 수급

```python
m = kis.domestic.market

m.investor_flows()           # 시장 전체 투자자 동향
m.investor_net_buy_stocks()  # 투자자별 순매수 상위 종목
m.investor_snapshot(market_code="0001", industry_code="0001")  # 시장·업종별 세부 매수/매도/순매수
```

## 프로그램 매매

```python
m.program_trades()             # 일별 프로그램매매 종합(차익/비차익; KOSPI/KOSDAQ)
m.program_flow()               # 당일 시간대별 프로그램 순매수 대금
m.program_investor_trades()    # 당일 프로그램매매 투자자 집계
m.foreign_broker_trades()      # 외국계 창구 매매
```

## 자금·금리

```python
m.funds()           # 증시자금 종합(예탁금·신용융자잔고·펀드유형별·시가총액) 추이
m.interest_rates()  # 국내·해외 주요 금리·채권지수 스냅샷
```

## 제도·상태

```python
m.limit_stocks()             # 상한가/하한가 도달 종목
m.vi_events()                # VI(변동성완화장치) 발동 현황
m.lendable_stocks()          # 대주 가능 종목·한도
m.credit_eligible_stocks()   # 신용주문 가능·불가 종목
m.futures_market_schedule()  # 국내선물 인접 영업일·장 시작/종료
```

## 의견·뉴스

```python
m.broker_opinions(broker="003")  # 한 증권사(회원사 코드)가 낸 종목 투자의견·목표가
m.news()                         # 시황·공시 뉴스 제목 피드
```

::: {.callout-note}
## 알아두면 좋은 용어
- **VI (변동성완화장치)** — 가격이 급변하면 2~10분 단일가로 전환하는 안전장치. `vi_events` 로 발동 현황.
- **프로그램매매** — 여러 종목을 바스켓으로 한 번에 사고파는 기관 매매. **차익**(선물-현물 차익거래)과
  **비차익**으로 나뉩니다. 일별 종합은 `program_trades`, 당일 시간대별 흐름은 `program_flow`.
- **대주(貸株)** — 없는 주식을 빌려서 파는 공매도의 재원. `lendable_stocks` 로 빌릴 수 있는 종목·한도를 봅니다.
:::

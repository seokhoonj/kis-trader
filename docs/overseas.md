# 해외 — `kis.overseas`

파라미터·KIS URL·TR-id·필드는 각 메서드 docstring에.

## 핸들

```python
kis.overseas.stock("AAPL")     # OverseasStock (거래소 자동: NAS)
kis.overseas.index("SPX")      # OverseasIndex
kis.overseas.futures("ESU24")  # OverseasDerivative
```

### OverseasStock

```python
s = kis.overseas.stock("AAPL")

# 시세
s.quote(); s.current_price(); s.bars("1d"); s.order_book(); s.trades()

# 주문
s.buy(quantity=1, limit_price=150); s.sell(quantity=1, limit_price=155)
s.daytime_buy(quantity=1, limit_price=150)   # 미국 주간거래
s.reserve_buy(quantity=1, limit_price=150)   # 미국 예약
```

## 계좌 — `kis.overseas.account`

```python
a = kis.overseas.account
a.positions(market=None)     # None = 7개 시장그룹 전체 합산
a.balance(market="NAS")      # 통화별 요약(시장 지정)
a.open_orders(market=None)
a.buyable(symbol="AAPL", exchange="NAS", price=150)
a.present_balance(…); a.settlement_balance(…)
a.period_profit(start=, end=); a.transactions(start=, end=)
a.foreign_margin()           # 통화별 외화 증거금
```

## 순위 — `kis.overseas.ranking`

```python
r = kis.overseas.ranking
r.by_change(); r.by_volume(); r.by_amount(); r.by_market_cap()
r.by_turnover(); r.by_volume_surge(); r.by_new_highlow()
```

## 참조·파생 데이터

```python
kis.overseas.quotes([("NAS","AAPL"), ("HKS","00700")])  # 멀티종목
kis.overseas.search_stocks(**filters)                    # 종목 검색
kis.overseas.news(…); kis.overseas.corporate_actions(…)
kis.overseas.industries(…); kis.overseas.industry_stocks(…)
kis.overseas.futures_details([...]); kis.overseas.option_details([...])
kis.overseas.futures_open_interest(…)                    # 미결제추이(CFTC)
```

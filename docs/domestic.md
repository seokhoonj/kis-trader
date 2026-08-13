# 국내 — `kis.domestic`

파라미터·KIS URL·TR-id·필드는 각 메서드 docstring에.

## 핸들

```python
kis.domestic.stock("005930")      # DomesticStock
kis.domestic.index("0001")        # Index (KOSPI)
kis.domestic.bond("KR6000012345") # Bond
kis.domestic.elw("58J297")        # ELW
kis.domestic.futures("101W09")    # FuturesContract
kis.domestic.option("201W09")     # OptionContract
```

### DomesticStock

```python
s = kis.domestic.stock("005930")

# 시세
s.quote(); s.bars("1d", start="20240101"); s.order_book(); s.trades()
s.recent_prices(); s.after_hours_quote()

# 수급·프로그램
s.investor_flows(); s.broker_activity(); s.foreign_net_buy_trend()
s.program_trades()

# 종목정보·재무
s.profile(); s.status()
s.financial_ratios(); s.income_statement(); s.balance_sheet()
s.earnings_estimate(); s.analyst_opinions()

# 분석
s.short_sale_trend(); s.credit_balance_trend(); s.volume_profile()

# ETF
s.nav(); s.etf_components(); s.nav_history()

# 주문가능·주문
s.buyable(); s.sellable(); s.credit_buyable()
s.buy(quantity=10, limit_price=70000); s.sell(quantity=10, limit_price=71000)
```

`bars(interval, start=, end=, max_bars=)` 는 `1m`(당일 분봉)/`1d`/`1wk`/`1mo` 를 흡수. 특정일 분봉은 `minute_bars_on(...)`.

### 선물·옵션

```python
f = kis.domestic.futures("101W09")
f.quote(); f.order_book(); f.bars("1d"); f.expected_execution_trend()
f.underlying_quote()          # 선물 전용 (옵션엔 없음)
```

## 계좌 — `kis.domestic.account`

```python
a = kis.domestic.account
a.balance(); a.positions(); a.portfolio(); a.assets()
a.open_orders()                                # 미체결/정정취소가능
a.trade_profits(start=, end=)                  # 종목별 실현손익
a.daily_profits(start=, end=)                  # 일별 실현손익
a.reserved_orders(start=, end=)                # 예약주문 목록
a.cancel_reserved_order(sequence)
a.modify_reserved_order(sequence, quantity=…, limit_price=…)
```

## 순위 — `kis.domestic.ranking`

```python
r = kis.domestic.ranking
r.by_change(top="gainers")   # 등락률
r.by_volume()                # 거래량
r.by_market_cap()            # 시가총액
r.by_short_sale(window="1d") # 공매도
r.by_credit_balance(); r.by_dividend(); r.by_near_high_low()
r.by_views()                 # HTS 조회상위
# 그 외: by_disparity/by_quote_balance/by_volume_power/by_finance_ratio/
#        by_valuation/by_profit_asset/by_overtime_change …
```

## 시장 — `kis.domestic.market`

```python
m = kis.domestic.market
m.investor_flows(); m.investor_net_buy_stocks()
m.program_trades(); m.foreign_broker_trades()
m.broker_opinions(); m.interest_rates(); m.news(); m.vi_events()
```

## 캘린더 — `kis.domestic.calendar`

```python
c = kis.domestic.calendar
c.dividends(…); c.ipo_subscriptions(…); c.rights_offerings(…)
c.listings(…); c.shareholder_meetings(…); c.merger_splits(…)
```

## ELW

```python
kis.domestic.elw_ranking.by_volume()          # 순위
kis.domestic.elw_screener.search(…)           # 스크리너
```

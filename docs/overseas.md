# 해외주식

해외는 `kis.overseas.*`. 심볼만 주면 **거래소는 자동으로 해석**된다.

## 시세

```python
s = kis.overseas.stock("AAPL")  # 거래소 자동 (NAS)

s.quote()  # 현재가
s.current_price()  # 현재가(간단)
s.bars("1d")  # 일봉
s.order_book()  # 호가
s.trades()  # 체결
```

## 주문

```python
s.buy(quantity=1, limit_price=150)
s.sell(quantity=1, limit_price=160)

s.daytime_buy(quantity=1, limit_price=150)  # 미국 주간거래
s.reserve_buy(quantity=1, limit_price=150)  # 미국 예약
```

주문 안전장치는 국내와 동일 → [주문](orders.md).

## 계좌

```python
a = kis.overseas.account

a.positions(market=None)  # None = 전체 시장 합산
a.balance(market="NAS")  # 통화별 요약(시장 지정)
a.present_balance()  # 체결기준 현재잔고
a.buyable(symbol="AAPL", exchange="NAS", price=150)
a.period_profit(start="20240101", end="20240630")
a.transactions(start="20240101", end="20240630")
a.foreign_margin()  # 통화별 외화 증거금
```

## 순위·검색

```python
r = kis.overseas.ranking
r.by_change(); r.by_volume(); r.by_amount(); r.by_market_cap()

kis.overseas.search_stocks(**filters)  # 종목 검색(가격·규모 등)
kis.overseas.news(…)
kis.overseas.industries(…)  # 업종
```

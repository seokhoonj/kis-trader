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

::: {.callout-note}
**주간거래(daytime)** 는 한국 낮 시간대에 미국주식을 사고파는 별도 세션이다(정규장은 한국
기준 밤). 낮에 대응하고 싶을 때 `daytime_buy`/`daytime_sell` 로 낸다. **예약(reserve)** 은
장 열리기 전에 미리 걸어두는 주문.
:::

주문 안전장치는 국내와 동일 → [주문](orders.md).

## 계좌

```python
a = kis.overseas.account

a.positions(market=None)  # None = 전체 시장 합산
a.balance(market="NAS")  # 통화별 요약(시장 지정)
a.present_balance()  # 체결기준 잔고 (오늘 체결분 포함)
a.settlement_balance()  # 결제기준 잔고 (결제 완료분만)
a.buyable(symbol="AAPL", exchange="NAS", price=150)
a.period_profit(start="20240101", end="20240630")
a.transactions(start="20240101", end="20240630")
a.foreign_margin()  # 통화별 외화 증거금
```

::: {.callout-note}
## 체결기준 vs 결제기준
주식은 **T+2 결제**라(체결 후 2영업일 뒤 실제 결제), "지금 잔고"가 두 가지다.

- **`present_balance` (체결기준)** — 오늘 매매까지 반영. "내가 지금 들고 있는 것"에 가깝다.
- **`settlement_balance` (결제기준)** — 결제 완료된 것만. 정산·인출가능 금액 확인용.

오늘 산 종목은 `present_balance` 엔 바로 잡히고, `settlement_balance` 엔 결제일(D+2) 전까진 안 잡힌다.
:::

## 순위·검색

```python
r = kis.overseas.ranking
r.by_change(); r.by_volume(); r.by_amount(); r.by_market_cap()

kis.overseas.search_stocks(**filters)  # 종목 검색(가격·규모 등)
kis.overseas.news(…)
kis.overseas.industries(…)  # 업종
```

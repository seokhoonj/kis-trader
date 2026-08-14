# 해외주식

해외는 `kis.overseas.*`. 심볼만 주면 **거래소는 자동으로 해석**됩니다.

## 시세

```python
s = kis.overseas.stock("AAPL")  # 거래소 자동 (NAS)

s.quote()                       # 현재가
s.current_price()               # 현재가(간단)
s.bars("1d", start="20240101")  # 일봉 (기간봉은 start 필요)
s.order_book()                  # 호가
s.trades()                      # 체결
```

## 주문

```python
s.buy(quantity=1, limit_price=150)
s.sell(quantity=1, limit_price=160)

s.buy(quantity=1, limit_price=150)            # 미국 정규장 (한국 시간 밤~새벽)
s.overnight_buy(quantity=1, limit_price=150)  # 미국 오버나이트 세션 (한국 낮)
s.reserve_buy(quantity=1, limit_price=150)    # 미국 예약 (장 열리기 전 미리)
```

::: {.callout-note}
## 오버나이트(overnight) 세션이란
미국 정규장(9:30–16:00 ET)은 **한국 시간으로 밤 11:30~새벽 6시**입니다. 그래서:

- **`buy` / `sell`** — 미국 **정규장**에서 체결 (한국 기준 밤~새벽).
- **`overnight_buy` / `overnight_sell`** — 미국 **오버나이트 세션**(정규장 밖 장외)에서 거래.
  이 시간대가 **한국 낮**이라, 밤새 안 깨어 있어도 낮에 미국주식을 매매할 수 있습니다.

미국 현지 기준으로 "오버나이트"이고(그래서 이름도 overnight), 한국 사용자에겐 낮 시간대인 셈입니다.
:::

주문 안전장치는 국내와 동일 → [주문](orders.md).

## 계좌

```python
a = kis.overseas.account

a.positions(market=None)                             # None = 전체 시장 합산
a.balance(market="US")                               # 통화별 요약 (시장: US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)
a.present_balance()                                  # 체결기준 잔고 (오늘 체결분 포함)
a.settlement_balance(basis_date="20240630")          # 결제기준 잔고 (결제일 기준)
a.buyable(symbol="AAPL", exchange="NAS", price=150)
a.period_profit(start="20240101", end="20240630")
a.transactions(start="20240101", end="20240630")
a.foreign_margin()                                   # 통화별 외화 증거금
```

::: {.callout-note}
## 체결기준 vs 결제기준
주식은 **T+2 결제**라(체결 후 2영업일 뒤 실제 결제), "지금 잔고"가 두 가지입니다.

- **`present_balance` (체결기준)** — 오늘 매매까지 반영. "내가 지금 들고 있는 것"에 가깝습니다.
- **`settlement_balance` (결제기준)** — 결제 완료된 것만. 정산·인출가능 금액 확인용.

오늘 산 종목은 `present_balance` 엔 바로 잡히고, `settlement_balance` 엔 결제일(D+2) 전까진 안 잡힙니다.
:::

## 순위·검색

```python
r = kis.overseas.ranking
r.by_change(exchange="NAS")                                                                # 모든 순위가 거래소(exchange)를 받습니다
r.by_volume(exchange="NAS"); r.by_amount(exchange="NAS"); r.by_market_cap(exchange="NAS")

kis.overseas.search_stocks("NAS", price=(10, 500), change_percent=(5, 30))                 # 거래소 + 범위 조건
kis.overseas.news(…)
kis.overseas.industries("NAS")                                                             # 업종
```

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

아시아(홍콩·중국·일본·베트남) 예약도 같은 `reserve_buy`/`reserve_sell` 이며, 거래소는 자동 판별됩니다.
취소는 `kis.orders.cancel(...)` 로 합니다(미국 예약은 예약번호로 취소).

```python
hk = kis.overseas.stock("00700")                              # 홍콩 (자동 판별)
rep = hk.reserve_buy(quantity=100, limit_price=350)           # 홍콩 예약
hk.reserve_buy(quantity=100, limit_price=350, currency="CNY") # 홍콩 CNY 결제 (기본 HKD)
kis.orders.cancel(rep.client_order_id)                        # 아시아 예약 취소
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
a = kis.account.overseas

a.positions(market=None)                             # None = 전체 시장 합산
a.balance(market="US")                               # 통화별 요약 (시장: US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)
a.present_balance()                                  # 체결기준 잔고 (오늘 체결분 포함)
a.settlement_balance(basis_date="20240630")          # 결제기준 잔고 (결제일 기준)
a.buyable(symbol="AAPL", exchange="NAS", price=150)  # 매수가능 수량·금액 (해당 단가 기준)
a.period_profit(start="20240101", end="20240630")    # 기간 실현손익 (매도청산 종목별)
a.transactions(start="20240101", end="20240630")     # 거래내역
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
r.by_change(exchange="NAS")  # 모든 순위가 거래소(exchange)를 받습니다
r.by_volume(exchange="NAS")
r.by_amount(exchange="NAS")
r.by_market_cap(exchange="NAS")
r.by_turnover(exchange="NAS")           # 거래회전율
r.by_buy_strength(exchange="NAS")       # 매수 체결강도
r.by_trade_growth(exchange="NAS")       # 거래증가율
r.by_volume_surge(exchange="NAS")       # 거래량 급증
r.by_new_highlow(exchange="NAS")        # 신고가/신저가
r.by_price_fluctuation(exchange="NAS")  # 급등/급락

kis.overseas.search_stocks("NAS", price=(10, 500), change_percent=(5, 30))  # 거래소 + 범위 조건
kis.overseas.news(…)  # 뉴스 헤드라인
kis.overseas.industries("NAS")  # 업종
```

## 종목정보·기업행위·심화

```python
kis.overseas.product_info("NAS", "AAPL")               # 상품기본정보(통화·상장주식수·SEDOL…)
kis.overseas.industry_stocks("NAS", "010")             # 업종별 종목 시세
kis.overseas.breaking_news()                           # 해외속보
kis.overseas.corporate_actions("US", "AAPL")           # 권리·기업행사 일정
kis.overseas.rights(start="20240101", end="20240630")  # 배당·증자·합병 권리
kis.overseas.collateral_stocks("AAPL", "US")           # 담보대출 가능종목
kis.overseas.settlement_dates()                        # 시장별 결제일자
```

미국 종목 오버나이트 매도: `kis.overseas.stock("AAPL").overnight_sell(quantity=1, limit_price=150)`.

## 해외파생(선물옵션) 계좌

해외파생(08) 세션에서 `kis.account` 는 해외선물옵션 계좌 뷰를 돌려줍니다. 해외파생 계좌는 통화별로 조회하며, **금액·수량은 조회 통화(`currency`) 기준**(원화 아님)입니다. 모두 **실전투자 전용**입니다.

```python
a = kis.account            # 08 세션

d = a.deposit(currency="USD")      # 예수금현황
print(d.cash_balance, d.orderable_amount, d.unrealized_pnl)

for p in a.positions():            # 미결제(보유) 현황 (fuop="00" 전체)
    print(f"{p.symbol:10s} {p.side} {p.quantity}  평가손익 {p.unrealized_pnl}")
```

`deposit()` 의 `OverseasDerivativeDeposit` 주요 필드:

| 필드 | 뜻 |
|---|---|
| `currency` | 통화코드 |
| `cash_balance` | 예수금잔액 |
| `total_asset` | 총자산평가금액 |
| `unrealized_pnl` · `realized_pnl` | 선물옵션평가손익 · 청산손익 |
| `brokerage_margin` · `maintenance_margin` | 위탁증거금 · 유지증거금 |
| `orderable_amount` · `withdrawable_amount` | 주문가능금액 · 출금가능금액 |
| `risk_rate` | 위험율 |

`positions()` 의 `OverseasDerivativePosition`:

| 필드 | 뜻 |
|---|---|
| `symbol` · `product_type` | 해외선물FX상품번호 · 상품유형코드 |
| `side` · `quantity` | 매수/매도 · 미결제수량 |
| `average_price` · `current_price` | 체결평균가격 · 현재가격 |
| `unrealized_pnl` | 평가손익금액 |
| `liquidatable_quantity` | 청산가능수량 |

주문가능·증거금상세·당일주문:

```python
a.orderable("ESU24", "buy", price=5300)  # 계약 주문가능수량 (price 없으면 시장가 기준)
a.margin_detail(currency="USD")           # 증거금상세 (주문가능·위탁/유지 증거금)
a.today_orders()                          # 당일 주문 (체결/미체결)
```

- `orderable()` → `OverseasDerivativeOrderable`: `new_orderable_quantity`(신규주문가능), `total_orderable_quantity`(총주문가능), `market_orderable_quantity`(시장가총주문가능), `liquidatable_quantity`(청산가능).
- `margin_detail()` → `OverseasDerivativeMargin`: `orderable_amount`, `brokerage_margin`, `maintenance_margin`, `order_margin`, `additional_margin`.
- `today_orders()` → `OverseasDerivativeOrder` 목록: `order_id`·`status`·`order_quantity`·`filled_quantity`·`remaining_quantity`.

기간 조회(체결·주문·손익·입출금):

```python
a.daily_fills(start="20240101", end="20240630")   # 일별 체결내역 + 기간 합계
a.daily_orders(start="20240101", end="20240630")  # 일별 주문내역
a.period_pnl(start="20240101", end="20240630")    # 기간 손익 (통화별 + 종목별)
a.transactions(start="20240101", end="20240630")  # 기간 입출금(원장)
```

- `daily_fills()` → `OverseasDerivativeFillHistory`: `fills`(`OverseasDerivativeFill`)와 합계(`total_filled_quantity`·`total_fee`).
- `daily_orders()` → `OverseasDerivativeDailyOrder` 목록.
- `period_pnl()` → `OverseasDerivativePNLHistory`: `by_currency`(통화별) · `by_symbol`(종목별) 두 벌, 각 행은 `realized_pnl`·`net_pnl`·`fee`·`unrealized_pnl`.
- `transactions()` → `OverseasDerivativeTransaction` 목록: `base_date`·`transaction_type`·`amount`·`deposit`.

타입화하지 않은 벤더 필드는 각 결과의 `_raw` 로 접근합니다.

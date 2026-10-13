# 해외주식

해외는 `kis.overseas.*`. 심볼만 주면 **거래소는 자동으로 판별**됩니다.

## 시세

```python
stock = kis.overseas.stock("AAPL")  # 거래소 자동 (NAS)

stock.quote()                       # 현재가
stock.current_price()               # 현재가(간단)
stock.bars("1d", start="20240101")  # 일봉 (기간봉은 start 필요)
stock.order_book()                  # 호가
stock.trades()                      # 체결
```

## 주문

```python
stock.buy(quantity=1, limit_price=150)
stock.sell(quantity=1, limit_price=160)

stock.buy(quantity=1, limit_price=150)            # 미국 정규장 (한국 시간 밤~새벽)
stock.overnight_buy(quantity=1, limit_price=150)  # 미국 오버나이트 세션 (한국 낮)
stock.reserve_buy(quantity=1, limit_price=150)    # 미국 예약 (장 열리기 전 미리)

stock.buy(quantity=10, limit_price=150, algo="twap")  # 미국 서버 알고 분할(twap/vwap; 실전·최소 10주). 자세히는 [주문](orders.md)
```

아시아(홍콩·중국·일본·베트남) 예약도 같은 `reserve_buy`/`reserve_sell` 이며, 거래소는 자동 판별됩니다.
취소는 `kis.orders.cancel(...)` 로 합니다(미국 예약은 예약번호로 취소).

```python
hk_stock = kis.overseas.stock("00700")                              # 홍콩 (자동 판별)
report = hk_stock.reserve_buy(quantity=100, limit_price=350)        # 홍콩 예약
hk_stock.reserve_buy(quantity=100, limit_price=350, currency="CNY") # 홍콩 CNY 결제 (기본 HKD)
kis.orders.cancel(report.client_order_id)                           # 아시아 예약 취소
```

::: {.callout-note}
## 오버나이트(overnight) 세션이란
미국 정규장(9:30–16:00 ET)은 **한국 시간으로 밤 11:30~새벽 6시**입니다. 그래서:

- **`buy` / `sell`** — 미국 **정규장**에서 체결 (한국 기준 밤~새벽).
- **`overnight_buy` / `overnight_sell`** — 미국 **오버나이트 세션**(정규장 밖 장외)에서 거래.
  이 시간대가 **한국 낮**이라, 밤새 안 깨어 있어도 낮에 미국주식을 매매할 수 있습니다.

미국 현지 기준으로 "오버나이트"이고(그래서 이름도 overnight), 한국 사용자에겐 낮 시간대인 셈입니다.
:::

주문 안전장치는 국내와 동일합니다 → [주문](orders.md). 전송 전 리스크 한도(`RiskLimits`)도 해외주식에
적용되며, 금액 한도는 거래소 그룹으로 고정된 **시장별 검사 통화별**(`max_order_notional_by_currency`,
예 `{"USD": 10000}`)로 둡니다 -- 그 통화의 한도가 없으면 거부합니다(fail-closed). 수량·괴리율 한도는
국내와 같은 방식입니다. 거래소별 검사 통화:

| 거래소(코드) | 검사 통화 |
|---|---|
| 미국 -- 나스닥·뉴욕·아멕스(NAS/NYS/AMS) | USD |
| 홍콩(HKS) | HKD |
| 상해·심천(SHS/SZS) | CNY |
| 일본(TSE) | JPY |
| 베트남 -- 호치민·하노이(HSX/HNX) | VND |

::: {.callout-note}
검사 통화는 거래소 그룹으로 고정돼 **주문의 실제 결제통화와 항상 일치하지는 않습니다.** 홍콩(HKS)
거래소의 USD 결제 종목은 가격 숫자가 환산 없이 HKD 한도와 직접 비교되어 실효 한도가 약 7.8배 느슨해질
수 있습니다(HKD 는 USD 에 7.75~7.85 밴드로 고정). 상해 B(USD)·심천 B(HKD) 종목도 같은 구조적 한계가
있습니다. 보호는 항상 '느슨한' 방향(과소보호)이며, 정상 주문을 과도하게 거부하지는 않습니다. 종목별
실제 결제통화는 상세호가의 `curr` 값으로 알 수 있으나, 정확한 교정은 모든 해외 지정가 주문마다 추가
조회(throttle 대상)를 강제하게 되어 현재는 적용하지 않습니다 -- 문서화된, 추적 중인 한계입니다.
:::

## 계좌

```python
account = kis.account.overseas

account.positions(market=None)                             # None = 전체 시장 합산
account.balance(market="US")                               # 시장 하나의 통화별 요약 (US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)
account.balance()                                          # None = 전체 시장을 순회한 시장별 요약 리스트
account.present_balance()                                  # 체결기준 잔고 (오늘 체결분 포함)
account.settlement_balance(basis_date="20240630")          # 결제기준 잔고 (결제일 기준)
account.buyable(symbol="AAPL", exchange="NAS", price=150)  # 매수가능 수량·금액 (해당 단가 기준)
account.period_profit(start="20240101", end="20240630")    # 기간 실현손익 (매도청산 종목별)
account.transactions(start="20240101", end="20240630")     # 거래내역
account.foreign_margin()                                   # 통화별 외화 증거금
account.algo_orders()                                                # 미국 알고(twap/vwap) 주문 목록
account.algo_executions(order_id, order_date="20240102")             # 한 알고주문의 체결내역
```

이 중 `settlement_balance`·`period_profit`·`transactions`·`foreign_margin`·`algo_orders`·`algo_executions` 는 **실전 전용**(모의투자 미지원)이라 `paper` 세션에서 부르면 `KISUsageError` 입니다. `positions`·`balance`·`present_balance`·`buyable` 은 모의도 지원합니다.

::: {.callout-note}
## 체결기준 vs 결제기준
주식은 **T+2 결제**라(체결 후 2영업일 뒤 실제 결제), "지금 잔고"가 두 가지입니다.

- **`present_balance` (체결기준)** — 오늘 매매까지 반영. "내가 지금 들고 있는 것"에 가깝습니다.
- **`settlement_balance` (결제기준)** — 결제 완료된 것만. 정산·인출가능 금액 확인용.

오늘 산 종목은 `present_balance` 엔 바로 잡히고, `settlement_balance` 엔 결제일(D+2) 전까진 안 잡힙니다.
:::

## 순위·검색

```python
ranking = kis.overseas.ranking
ranking.by_change(exchange="NAS")  # 모든 순위가 거래소(exchange)를 받습니다
ranking.by_volume(exchange="NAS")
ranking.by_amount(exchange="NAS")
ranking.by_market_cap(exchange="NAS")
ranking.by_turnover(exchange="NAS")           # 거래회전율
ranking.by_buy_strength(exchange="NAS")       # 매수 체결강도
ranking.by_trade_growth(exchange="NAS")       # 거래증가율
ranking.by_volume_surge(exchange="NAS")       # 거래량 급증
ranking.by_new_highlow(exchange="NAS")        # 신고가/신저가
ranking.by_price_fluctuation(exchange="NAS")  # 급등/급락

kis.overseas.search_stocks("NAS", price=(10, 500), change_percent=(5, 30))  # 거래소 + 범위 조건
kis.overseas.search_stocks("US", price=(10, 500))   # NAS/NYS/AMS 를 각각 조회해 합침
kis.overseas.news(country="US")  # 미국 뉴스 헤드라인 (전체 피드는 kis.overseas.news())
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

해외파생(상품코드 08) 세션에서 `kis.account` 는 해외선물옵션 계좌 뷰를 돌려줍니다. 해외파생 계좌는 통화별로 조회하며, **금액·가격은 조회 통화(`currency`)의 Decimal**(원화 아님)이고 **수량은 계약수**(Decimal)입니다. 모두 **실전투자 전용**입니다.

```python
account = kis.account                      # 상품코드 08 세션

deposit = account.deposit(currency="USD")  # 예수금현황
print(deposit.cash_balance, deposit.orderable_amount, deposit.unrealized_pnl)

for position in account.positions():       # 미결제(보유) 현황 (product="all" 전체)
    print(f"{position.symbol:10s} {position.side} {position.quantity}  평가손익 {position.unrealized_pnl}")
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
account.orderable("ESU24", "buy", price=5300)  # 계약 주문가능수량 (price 없으면 시장가 기준)
account.margin_detail(currency="USD")           # 증거금상세 (주문가능·위탁/유지 증거금)
account.today_orders()                          # 당일 주문 (체결/미체결)
```

- `orderable()` → `OverseasDerivativeOrderable`: `new_orderable_quantity`(신규주문가능), `total_orderable_quantity`(총주문가능), `market_orderable_quantity`(시장가총주문가능), `liquidatable_quantity`(청산가능).
- `margin_detail()` → `OverseasDerivativeMargin`: `orderable_amount`, `brokerage_margin`, `maintenance_margin`, `order_margin`, `additional_margin`.
- `today_orders()` → `OverseasDerivativeOrder` 목록: `order_id`·`status`·`order_quantity`·`filled_quantity`·`remaining_quantity`.

기간 조회(체결·주문·손익·입출금):

```python
account.daily_fills(start="20240101", end="20240630")   # 일별 체결내역 + 기간 합계
account.daily_orders(start="20240101", end="20240630")  # 일별 주문내역
account.period_pnl(start="20240101", end="20240630")    # 기간 손익 (통화별 + 종목별)
account.transactions(start="20240101", end="20240630")  # 기간 입출금(원장)
```

- `daily_fills()` → `OverseasDerivativeFillHistory`: `fills`(`OverseasDerivativeFill`)와 합계(`total_filled_quantity`·`total_fee`).
- `daily_orders()` → `OverseasDerivativeDailyOrder` 목록.
- `period_pnl()` → `OverseasDerivativePNLHistory`: `by_currency`(통화별) · `by_symbol`(종목별) 두 벌, 각 행은 `realized_pnl`·`net_pnl`·`fee`·`unrealized_pnl`.
- `transactions()` → `OverseasDerivativeTransaction` 목록: `base_date`·`transaction_type_name`·`amount`·`deposit`.

타입화하지 않은 벤더 필드는 각 결과의 `_raw` 로 접근합니다.

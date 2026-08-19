# 선물·옵션

국내 지수선물·옵션과 해외 파생. 계약 핸들은 코드로 만듭니다.

## 국내

```python
futures = kis.domestic.futures("101W09")  # 지수선물 계약 핸들

futures.quote()                           # 현재가
futures.underlying_quote()                # 선물 + 기초지수 나란히 (베이시스 판단)
futures.expected_execution_trend()        # 예상체결 요약·추이
futures.order_book()                      # 호가

option = kis.domestic.option("201W09")   # 지수옵션 계약 핸들
option.quote()
```

전광판·만기:

```python
kis.domestic.option_expiries()       # 상장된 옵션 만기월 목록
kis.domestic.option_board("202409")  # 한 만기의 콜/풋 전광판 (행사가별 시세·그릭스)
kis.domestic.option_board_futures()  # 전광판 하단 선물 계약별 시세
```

## 해외

```python
futures = kis.overseas.futures("ESZ25")                          # 해외 선물 (E-mini S&P 2025.12 = 시리즈코드)

futures.quote()                                                  # 현재가
futures.detail()                                                 # 계약 명세

kis.overseas.futures_details(["ESZ25", "NQZ25"])            # 여러 계약 명세 (최대 32)
kis.overseas.futures_open_interest("ES", as_of="20240628")  # CFTC 미결제약정
kis.overseas.derivatives_market_hours()                     # 상품군별 장운영시간
```

## 계좌·잔고 (파생)

국내파생(03) 세션에서 `kis.account` 는 파생 계좌 뷰를 돌려줍니다. 금액·수량은 원화입니다.

```python
account = kis.account            # 03 세션

balance = account.balance()            # 잔고 (보유내역 + 예수금·증거금·손익 요약, 모의 지원)
print(balance.total_deposit, balance.orderable_cash, balance.total_unrealized_pnl)

for position in balance.positions:
    print(f"{position.name:12s} {position.side} {position.quantity}  평가손익 {position.unrealized_pnl:>12,}")
```

`balance()` 의 `DerivativeBalance` 주요 필드:

| 필드 | 뜻 |
|---|---|
| `total_deposit` | 총예수금액 |
| `deposit_cash` | 예수금현금 |
| `total_margin` | 증거금총액 |
| `orderable_cash` · `orderable_total` | 주문가능현금 · 주문가능총액 |
| `total_unrealized_pnl` · `total_realized_pnl` | 평가손익합계 · 매매손익합계 |
| `futures_unrealized_pnl` · `options_unrealized_pnl` | 선물 · 옵션 평가손익 |
| `account_value` | 추정예탁자산금액 |
| `positions` | 보유내역(`DerivativePosition` 목록) |

`DerivativePosition` 은 종목 식별과 종목별 손익을 담습니다:

| 필드 | 뜻 |
|---|---|
| `symbol` · `isin` | 단축상품번호 · 표준상품번호 |
| `name` · `side` | 상품명 · 매도매수구분 |
| `quantity` · `liquidatable_quantity` | 잔고수량 · 청산가능수량 |
| `average_price` · `settlement_price` | 체결평균단가 · 정산단가 |
| `market_value` · `unrealized_pnl` | 평가금액 · 평가손익 |

나머지 조회는 **실전투자 전용**입니다.

```python
account.deposit()                      # 총자산현황 (DerivativeDeposit)
account.valuation_pl()                 # 잔고평가손익 (종목별 평가/매매 손익)
account.settlement_pl(base_date="20240628")   # 잔고정산손익
account.base_date_fills(order_date="20240628")  # 기준일 체결내역 (start_time/end_time 로 시각 구간)
account.commissions(start="20240101", end="20240630")  # 기간약정수수료 일별
```

주문가능수량은 계약 핸들에서 봅니다. `side` 는 `"buy"`/`"sell"`, `limit_price` 를 주면 지정가 기준·없으면 시장가 기준입니다.

```python
futures = kis.domestic.futures("101W09")

futures.orderable("buy", limit_price=340.0)   # 주간 (모의 지원)
futures.night_orderable("buy")                # 야간장(EUREX 연계, 실전투자 전용)
```

`orderable()` 의 `DerivativeOrderable`: `orderable_quantity`(주문가능수량), `total_quantity`(총가능수량), `liquidatable_quantity`(청산가능수량), `base_index`(기준지수).

증거금율 표는 계약이 아니라 시장 값이라 네임스페이스에 있습니다(**실전투자 전용**).

```python
kis.domestic.derivative_margin_rates("20240628")                  # 전체 기초자산
kis.domestic.derivative_margin_rates("20240628", underlying_id="101")  # 한 기초자산
```

`DerivativeMarginRate`: `underlying_id`·`underlying_name`·`underlying_price`(기초자산), `brokerage_margin_rate`·`trading_margin_rate`(위탁·거래 증거금율), `trading_multiplier`(거래승수), `futures_margin_per_contract`(계약당 선물증거금).

타입화하지 않은 벤더 필드는 각 결과의 `_raw` 로 접근합니다.

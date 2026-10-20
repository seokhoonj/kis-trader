# 계좌·잔고·손익

계좌 조회는 `kis.account` 에 모여 있으며, 세션이 연 계좌를 그대로 가리킵니다. `kis.account` 가 돌려주는 뷰는 세션 상품코드에 따라 달라집니다.

| 상품 | `kis.account` 뷰 |
|---|---|
| 주식(01, 위탁) | 국내 `kis.account.domestic.*` · 해외 `kis.account.overseas.*` |
| 연금저축(22) | 위탁과 같은 국내주식 계좌 뷰(주문 가능) |
| IRP(29) | 위탁과 같은 국내주식 계좌 뷰 + 퇴직연금 조회 `kis.account.pension.*`(조회전용) |
| 국내파생(03) | 파생 계좌 뷰 → [선물·옵션](derivatives.md) |
| 해외파생(08) | 해외파생 계좌 뷰 → [해외주식](overseas.md) |

주식 세션은 국내·해외를 한 계좌에서 다루므로 시장별 뷰를 갖습니다 -- 국내는 `kis.account.domestic`, 해외는 `kis.account.overseas`.

```python
account = kis.account.domestic
```

## 예수금·자산 요약

```python
balance = account.balance()
print(balance.deposit, balance.total_evaluation, balance.unrealized_pnl)
```

`balance()` 가 돌려주는 `DomesticBalance` 의 주요 필드:

| 필드 | 뜻 |
|---|---|
| `deposit` | 예수금 |
| `settlement_cash_d1` · `settlement_cash_d2` | D+1 · D+2 정산 예정 현금 |
| `total_evaluation` | 총 평가금액 |
| `net_asset` | 순자산 |
| `purchase_amount` | 매입금액 |
| `market_value` | 평가금액 |
| `unrealized_pnl` | 평가손익 |

`account.assets()` 는 대출·외화까지 포함한 계좌 자산현황.

## 보유 종목

```python
for position in account.positions():
    print(f"{position.name:10s} {position.quantity}주  "
          f"평가손익 {position.unrealized_pnl:>12,}  ({position.unrealized_pnl_percent}%)")
```

`DomesticPosition` 필드:

| 필드 | 뜻 |
|---|---|
| `symbol` · `name` | 종목코드 · 종목명 |
| `quantity` · `sellable_quantity` | 보유 · 매도가능 수량 |
| `average_purchase_price` | 평균 매입가 |
| `purchase_amount` | 매입금액 |
| `current_price` | 현재가 |
| `market_value` | 평가금액 |
| `unrealized_pnl` | 평가손익 |
| `unrealized_pnl_percent` | 수익률(%) |

`sellable_quantity`(매도가능)는 담보·대주 등으로 `quantity`(보유)보다 적을 수 있습니다.

보유종목 + 요약을 한 번에: `account.portfolio()` → `.balance`, `.positions`.

## 실현손익

```python
account.trade_profits(start="20240101", end="20240630")  # 종목별 실현손익
account.daily_profits(start="20240101", end="20240630")  # 일별 실현손익
account.realized_profit_balance()                        # 실현손익 포함 잔고
```

::: {.callout-note}
## 평가손익 vs 실현손익
- **평가손익 (미실현, `unrealized_pnl`)** — 보유 중인 자산을 평가한 손익으로, 평가가격에 따라 달라집니다.
- **실현손익 (`trade_profits` / `daily_profits`)** — 매매로 발생한 종목별·일별 손익을 조회합니다.

수수료·세금·환율 등의 반영 기준과 실제 정산·출금 가능 시점은 해당 조회의 응답 정의와 브로커 안내를 확인하세요.
:::

## 미체결·주문가능

```python
account.open_orders()                                    # 미체결 / 정정취소 가능 주문
history = account.combined_order_history(start="20240101", end="20240229")
history.orders                         # 최근·과거 조회를 결합한 주문·체결 행
history.recent.average_purchase_price  # 최근 조회의 원본 요약
history.historical.estimated_expenses  # 과거 조회의 원본 요약
account.order_history(start="20240101", end="20240229", older_than_three_months=True)  # 과거 조회만
stock = kis.domestic.stock("005930")
stock.buyable()                       # 매수 가능 수량·금액
stock.sellable()                      # 매도 가능 수량
```

`combined_order_history()` 는 지정한 기간을 최근·과거 조회 양쪽에 보내고, 각각의 연속조회를 끝까지
수행합니다. `StockOrderHistoryCollection` 의 `orders` 는 결합한 행이며, `recent` 와 `historical` 은
각 조회의 `StockOrderHistory` 를 그대로 보존합니다. 평균 매입가격·추정 비용은 합산하지 않습니다.
한쪽 조회가 실패하면 부분 결과를 성공으로 반환하지 않습니다. 날짜·주문번호·지점·조직 정보가 갖춰진
동일 주문의 관측 내용이 두 출처에서 다르면 결합 오류로 알립니다. 조회 사이의 정상적인 상태 변화일 수도
있으므로, 이 오류 자체가 증권사 데이터 손상을 뜻하지는 않습니다. 식별 정보가 부족한 행은 임의로 합쳐
없애지 않으며, 양쪽 조회에 모두 있으면 결합 목록에 함께 남을 수 있습니다.

기존 `order_history()` 는 기본으로 최근 조회만 하며, `older_than_three_months=True` 이면 과거 조회만
합니다. 두 방식 모두 증권사의 조회 가능 기간·보관 범위 안에서 동작하며, 다년간의 완전한 보관을 보장하지
않습니다. 미체결·거부 주문도 포함하므로 체결된 주문만의 목록으로 해석하지 마세요.

각 `StockOrder` 는 주문·체결 상태(수량·단가·평균가·거래소)를 담고, 스톱지정가 체결이면
`.stop` 에 `StopTrigger`(조건가격·효력발생시각)를 싣습니다.

## 권리·증거금

```python
account.rights(start="20240101", end="20240630")  # 배정/신청/환불된 권리
account.integrated_margin()                       # 통합증거금
```

## 해외 계좌

해외는 통화·시장이 얽혀 있어 살짝 다릅니다.

```python
account = kis.account.overseas
account.positions(market=None)                           # None = 전체 시장 합산
account.balance(market="US")                             # 통화별 요약 (시장: US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)
account.present_balance()                                # 체결기준 현재잔고
account.trade_profits(start="20240101", end="20240630")  # 기간 실현손익
account.transactions(start="20240101", end="20240630")   # 거래내역
account.foreign_margin()                                 # 통화별 외화 증거금
account.algo_orders()                                    # 미국 알고(twap/vwap) 주문 목록
account.algo_executions(order_id, order_date="20240102") # 한 알고주문의 체결내역
```

## 채권 계좌

장내채권은 주식과 같은 위탁(01) 계좌를 쓰며 `kis.account.domestic.bonds` 로 조회합니다. 보유·매수가능·미체결·체결 → [채권](bonds.md).

```python
kis.account.domestic.bonds.balance()  # 채권 보유 lot
```

## 퇴직연금 (IRP) 계좌

개인형 퇴직연금(IRP, 상품코드 29)은 일반 국내주식 잔고엔 없는 퇴직연금 전용 조회(예수금 요약·체결기준잔고·매수가능여력)를 `kis.account.pension` 렌즈로 줍니다. IRP는 조회전용이라 발주 메서드는 없습니다. 상세 → [퇴직연금](pension.md).

```python
kis.account.pension.deposit()          # 예수금 요약 (IRP 29 전용)
kis.account.pension.present_balance()  # 체결기준 잔고 + 손익
```

연금저축(22)은 별개 사적연금이라 주문 가능한 일반 주식계좌로 다루며 이 렌즈 대상이 아닙니다. 상품코드가 29가 아니면 `.pension` 접근은 오류입니다.

계좌 종류로 분기해야 하면 `kis.account.product_code` 로 상품코드(`"29"`/`"01"` 등)를 직접 읽습니다 -- `.pension` 을 예외로 떠보지 않고 IRP(29)와 일반(01)을 가를 수 있습니다. 파생 계좌 뷰(`03`/`08`)에도 같은 접근자가 있습니다.

```python
if kis.account.product_code == "29":
    deposit = kis.account.pension.deposit()
```

## 통합잔고

`kis.account.balance()` 는 국내주식·채권·해외주식 잔고를 한 뷰로 합쳐 `Balance` 로 돌려줍니다. **실전투자 전용**입니다.

```python
balance = kis.account.balance()
for deposit in balance.deposits:
    print(f"{deposit.currency}  예수금 {deposit.cash:>15,}  (환율 {deposit.exchange_rate})")
print("원화 총평가", balance.total_evaluation, "  평가손익", balance.total_unrealized_pnl)
```

`Balance` 의 주요 필드:

| 필드 | 뜻 |
|---|---|
| `base_currency` | 기준통화("KRW") |
| `deposits` | 통화별 예수금(`CurrencyDeposit` 목록) |
| `domestic` | 국내주식 잔고 서브(`DomesticBalance`) |
| `bonds` | 채권 보유(`BondPosition` 목록, 매입금액 기준) |
| `overseas` | 해외 체결기준 현재잔고 |
| `total_evaluation` | 원화 총평가(국내·해외 보유 평가의 합) |
| `total_unrealized_pnl` | 원화 총평가손익 |

`CurrencyDeposit` 은 `currency`(통화), `cash`(예수금), `exchange_rate`(참고용 원화 환율) 세 값입니다.

::: {.callout-note}
## 왜 단일 총자산 하나로 안 합치나
이 객체의 `total_evaluation` 은 `domestic.market_value + overseas.total_evaluation_amount`,
`total_unrealized_pnl` 은 `domestic.unrealized_pnl + overseas.total_unrealized_pnl` 로 계산합니다.
통화별 예수금은 `deposits`, 채권 lot 은 `bonds` 로 따로 제공합니다. 합계의 해석은 각 API가 제공하는
평가 기준에 의존하므로, 이를 현금·채권까지 포함한 단일 총자산으로 사용하려면 브로커 응답 기준을 먼저 확인하세요.
:::

해외 `present_balance()` 는 야간에 실시간으로 반영되지 않을 수 있습니다. 오늘 체결분이 즉시
나타난다고 가정하지 말고 수량은 보유 조회와 함께 확인하세요
([KIS 공식 FAQ](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000002/post/1f4b60d6-017d-4b85-8ce3-d92efd8fc39a)).

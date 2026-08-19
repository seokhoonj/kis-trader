# 계좌·잔고·손익

계좌 조회는 `kis.account` 에 모여 있으며, 세션이 연 계좌를 그대로 가리킵니다. `kis.account` 가 돌려주는 뷰는 세션 상품코드에 따라 달라집니다.

| 상품 | `kis.account` 뷰 |
|---|---|
| 주식(01, 위탁) | 국내 `kis.account.domestic.*` · 해외 `kis.account.overseas.*` |
| 국내파생(03) | 파생 계좌 뷰 → [선물·옵션](derivatives.md) |
| 해외파생(08) | 해외파생 계좌 뷰 → [해외주식](overseas.md) |

주식 세션은 국내·해외를 한 계좌에서 다루므로 시장별 뷰를 갖습니다. 세션을 `account=` 로 열어야 합니다.

```python
account = kis.account.domestic
```

## 예수금·자산 요약

```python
balance = account.balance()
print(balance.deposit, balance.total_evaluation, balance.unrealized_pnl)
```

`balance()` 가 돌려주는 `Balance` 의 주요 필드:

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
    print(f"{position.security_name:10s} {position.quantity}주  "
          f"평가손익 {position.unrealized_pnl:>12,}  ({position.unrealized_pnl_percent}%)")
```

`Position` 필드:

| 필드 | 뜻 |
|---|---|
| `symbol` · `security_name` | 종목코드 · 종목명 |
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
- **평가손익 (미실현, `unrealized_pnl`)** — 아직 **안 판** 보유종목의 장부상 손익. 현재가로
  계산돼 계속 바뀌고, 팔기 전엔 확정이 아닙니다.
- **실현손익 (`trade_profits` / `daily_profits`)** — 실제로 **팔아서 확정된** 손익.

"평가손익 +100만"이라도 팔기 전엔 내 돈이 아닙니다. 판 순간 실현손익으로 고정됩니다.
:::

## 미체결·주문가능

```python
account.open_orders()                 # 미체결 / 정정취소 가능 주문
stock = kis.domestic.stock("005930")
stock.buyable()                       # 매수 가능 수량·금액
stock.sellable()                      # 매도 가능 수량
```

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
account.period_profit(start="20240101", end="20240630")  # 기간 실현손익
account.transactions(start="20240101", end="20240630")   # 거래내역
account.foreign_margin()                                 # 통화별 외화 증거금
```

## 채권 계좌

장내채권은 주식과 같은 위탁(01) 계좌를 쓰며 `kis.account.domestic.bonds` 로 조회합니다. 보유·매수가능·미체결·체결 → [채권](bonds.md).

```python
kis.account.domestic.bonds.balance()  # 채권 보유 lot
```

## 통합잔고

`kis.account.balance()` 는 국내주식·채권·해외주식 잔고를 한 뷰로 합쳐 `IntegratedBalance` 로 돌려줍니다. **실전투자 전용**입니다.

```python
balance = kis.account.balance()
for deposit in balance.deposits:
    print(f"{deposit.currency}  예수금 {deposit.cash:>15,}  (환율 {deposit.exchange_rate})")
print("원화 총평가", balance.total_evaluation, "  평가손익", balance.total_unrealized_pnl)
```

`IntegratedBalance` 의 주요 필드:

| 필드 | 뜻 |
|---|---|
| `base_currency` | 기준통화("KRW") |
| `deposits` | 통화별 예수금(`CurrencyDeposit` 목록) |
| `domestic` | 국내주식 잔고 서브(`Balance`) |
| `bonds` | 채권 보유(`BondPosition` 목록, 매입금액 기준) |
| `overseas` | 해외 체결기준 현재잔고 |
| `total_evaluation` | 원화 총평가(국내·해외 보유 평가의 합) |
| `total_unrealized_pnl` | 원화 총평가손익 |

`CurrencyDeposit` 은 `currency`(통화), `cash`(예수금), `exchange_rate`(참고용 원화 환율) 세 값입니다.

::: {.callout-note}
## 왜 단일 총자산 하나로 안 합치나
통화별 예수금(`deposits`)이 진실의 원천이고, `total_evaluation` 은 서로 겹치지 않는 국내·해외 **보유 평가**만 더한 값입니다. 현금까지 더한 단일 총자산은 두지 않습니다 — 국내 순자산과 해외 총자산이 같은 위탁계좌의 원화 예수금을 공유(이중계상)하고 net/gross 기준이 달라 신뢰 있게 합칠 수 없기 때문입니다. 채권은 시장가가 없어 매입금액 기준이라 평가 합계에 넣지 않습니다(`bonds` 로 따로 봅니다).
:::

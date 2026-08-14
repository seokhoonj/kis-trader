# 계좌·잔고·손익

계좌 조회는 `kis.domestic.account.*` 에 모여 있습니다. 세션을 `account=` 로 열어야 합니다.

```python
a = kis.domestic.account
```

## 예수금·자산 요약

```python
b = a.balance()
print(b.deposit, b.total_evaluation, b.unrealized_pnl)
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

`a.assets()` 는 대출·외화까지 포함한 계좌 자산현황.

## 보유 종목

```python
for p in a.positions():
    print(f"{p.security_name:10s} {p.quantity}주  "
          f"평가손익 {p.unrealized_pnl:>12,}  ({p.unrealized_pnl_percent}%)")
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

보유종목 + 요약을 한 번에: `a.portfolio()` → `.positions`, `.summary`.

## 실현손익

```python
a.trade_profits(start="20240101", end="20240630")  # 종목별 실현손익
a.daily_profits(start="20240101", end="20240630")  # 일별 실현손익
a.realized_profit_balance()                        # 실현손익 포함 잔고
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
a.open_orders()  # 미체결 / 정정취소 가능 주문
s = kis.domestic.stock("005930")
s.buyable()   # 매수 가능 수량·금액
s.sellable()  # 매도 가능 수량
```

## 권리·증거금

```python
a.rights(start="20240101", end="20240630")  # 배정/신청/환불된 권리
a.integrated_margin()                       # 통합증거금
```

## 해외 계좌

해외는 통화·시장이 얽혀 있어 살짝 다릅니다.

```python
oa = kis.overseas.account
oa.positions(market=None)                           # None = 전체 시장 합산
oa.balance(market="US")                             # 통화별 요약 (시장: US/HK/CN_SH/CN_SZ/JP/VN_HN/VN_HCM)
oa.present_balance()                                # 체결기준 현재잔고
oa.period_profit(start="20240101", end="20240630")  # 기간 실현손익
oa.transactions(start="20240101", end="20240630")   # 거래내역
oa.foreign_margin()                                 # 통화별 외화 증거금
```

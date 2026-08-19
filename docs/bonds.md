# 채권

채권 핸들은 `kis.domestic.bond(표준코드)`.

```python
bond = kis.domestic.bond("KR6095572D97")

bond.profile()                                     # 기본/발행 정보 (발행일·만기·표면금리·만기수익률·통화)
bond.issuance()                                    # 상세 발행조건·발행기관·신용등급·거래상태
bond.daily_prices()                                # 날짜별 현재가·등락·OHLCV (과거→현재)

bond.valuations(start="20240101", end="20240630")  # 평가기관별 단가·수익률 일별 시계열
```

## 채권 계좌·잔고

장내채권은 주식과 같은 위탁(01) 계좌를 쓰며, 계좌 관점 조회는 `kis.account.domestic.bonds` 에 있습니다. 모두 **실전투자 전용**입니다.

```python
bonds = kis.account.domestic.bonds

for position in bonds.balance():
    print(f"{position.name:16s} {position.quantity}  매수단가 {position.buy_price}  수익률 {position.buy_yield}%")
```

채권 잔고는 종목이 아니라 매수 단위(`buy_date` + `buy_sequence`)로 쪼개져 오므로 같은 종목이 여러 lot 으로 나뉠 수 있습니다. `BondPosition` 주요 필드:

| 필드 | 뜻 |
|---|---|
| `symbol` · `name` | 상품번호 · 상품명 |
| `buy_date` · `buy_sequence` | 매수일자 · 매수순번(lot 식별) |
| `quantity` · `orderable_quantity` | 잔고수량 · 주문가능수량 |
| `buy_price` · `buy_amount` | 매수단가 · 매수금액 |
| `buy_yield` | 매수수익률 |
| `maturity_date` | 만기일자 |

지정 단가로 살 때의 매수가능 여력·미체결·체결:

```python
bonds.buyable("KR6095572D97", price=10500)   # 매수가능 금액·수량
bonds.open_orders(order_date="20240628")      # 정정·취소 가능한 미체결 주문
bonds.fills(start="20240101", end="20240630") # 일별 주문·체결 (side/symbol/unfilled_only 필터)
```

- `buyable()` → `BondBuyable`: `orderable_cash`(주문가능현금), `buyable_amount`·`buyable_quantity`(매수가능 금액·수량).
- `open_orders()` → `BondOpenOrder` 목록: `order_id`(주문번호, 정정·취소로 지목), `cancelable_quantity`(정정·취소 가능 수량), `side`.
- `fills(start, end, *, side="all", symbol=None, unfilled_only=False)` → `BondFillHistory`: 개별 행 `fills`(`BondFill`)와 기간 합계(`total_filled_quantity`·`total_filled_amount`·`average_price`).

타입화하지 않은 벤더 필드는 각 결과의 `_raw` 로 접근합니다.

# 퇴직연금 — `kis.pension`

퇴직연금 계좌 전용 조회. 모두 **모의투자 미지원**(실전만).

```python
p = kis.pension
p.deposit()                        # 예수금총액 / 정산·결제 예정
p.buyable(symbol="005930", limit_price=70000)  # 주문가능현금·최대매수
p.balance()                        # 보유종목 + 예수금 요약
p.present_balance()                # 체결기준 잔고 + 손익요약
p.orders(only_unfilled=True)       # 당일 체결/미체결
```

주문(매수/매도)은 국내 주식과 같은 안전 커널을 쓴다. 계좌가 주문 불가 유형(IRP 29, DC 55)이면 `orderable` 가드가 자동으로 막는다 → [주문·안전](orders-and-safety.md).

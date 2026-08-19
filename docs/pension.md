# 퇴직연금

퇴직연금 계좌 전용 조회. 모두 **모의투자 미지원**(실전만).

```python
pension = kis.pension

pension.deposit()                                    # 예수금총액·정산예정
pension.balance()                                    # 보유종목 + 예수금 요약
pension.present_balance()                            # 체결기준 잔고 + 손익
pension.orders(only_unfilled=True)                   # 당일 체결/미체결
pension.buyable(symbol="005930", limit_price=70000)  # 주문가능·최대매수
```

주문(매수/매도)은 국내 주식과 같은 방식·안전장치를 씁니다 → [주문](orders.md). 계좌가 주문
불가 유형(IRP 등)이면 자동으로 막힙니다.

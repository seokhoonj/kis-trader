# 퇴직연금 (IRP)

개인형 퇴직연금(IRP, 상품코드 29) 계좌의 퇴직연금 전용 조회. `kis.account` 파사드의
`.pension` 렌즈로 접근합니다. 모두 **모의투자 미지원**(실전만).

```python
pension = kis.account.pension              # IRP(29) 전용 조회 렌즈

pension.deposit()                                    # 예수금총액·정산예정
pension.balance()                                    # 보유종목 + 예수금 요약
pension.present_balance()                            # 체결기준 잔고 + 손익
pension.orders(only_unfilled=True)                   # 당일 체결/미체결
pension.buyable(symbol="005930", limit_price=70000)  # 주문가능·최대매수
```

IRP(29)는 조회전용입니다 — KIS(APBK1744)가 주문을 거부하므로 이 렌즈에도 발주 메서드는
없습니다. 일반 국내주식 잔고([계좌](account.md)의 `.domestic`)엔 없는 퇴직연금 전용 필드
(예수금 요약·체결기준잔고·매수가능여력)를 줍니다.

연금저축(22)은 법적으로 퇴직연금이 아닌 별개 사적연금이라 주문 가능한 일반 주식계좌로
다룹니다 → [계좌](account.md). 상품코드가 29가 아니면 `.pension` 접근은 오류입니다.
DC가입자(55)는 API 세션 자체가 불가합니다.

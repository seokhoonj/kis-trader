# 퇴직연금 (IRP)

개인형 퇴직연금(IRP, 상품코드 29) 계좌의 퇴직연금 전용 조회. `kis.account` 파사드의
`.pension` 렌즈로 접근합니다. 모두 **모의투자 미지원**(실전만).

```python
pension = kis.account.pension              # IRP(29) 전용 조회 렌즈

pension.deposit()                                    # 예수금총액·정산예정
pension.balance()                                    # 보유종목 + 예수금 요약
pension.present_balance()                            # 체결기준 잔고 + 손익
pension.orders()                                     # 당일 주문내역(체결+미체결); unfilled_only=True 면 미체결만
pension.buyable(symbol="005930", limit_price=70000)  # 주문가능·최대매수
```

CLI로는 상품코드 **29인 실전 슬롯**을 저장한 `irp` 프로필을 선택해
`kis --profile irp --environment real account buyable 005930 --limit-price 70000` 을 요청할 수 있습니다.
현재 이 CLI 명령은 일반 주식 매수가능 조회 경로를 사용하므로 위 IRP 전용 `pension.buyable()` 과
API가 다릅니다. IRP 전용 조회에는 위 Python 예제를 사용하고, CLI의 지원 여부와 결과 항목은
해당 계좌의 브로커 응답으로 확인해야 합니다.

IRP(29)는 조회전용입니다 — KIS(APBK1744)가 주문을 거부하므로 이 렌즈에도 발주 메서드는
없습니다. 일반 국내주식 잔고([계좌](account.md)의 `.domestic`)엔 없는 퇴직연금 전용 필드
(예수금 요약·체결기준잔고·매수가능여력)를 줍니다.

연금저축(22)은 법적으로 퇴직연금이 아닌 별개 사적연금이라 주문 가능한 일반 주식계좌로
다룹니다 → [계좌](account.md). 상품코드가 29가 아니면 `.pension` 접근은 오류입니다.
DC가입자(55)는 API 세션 자체가 불가합니다.

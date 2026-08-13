# `kis.pension` — 퇴직연금

퇴직연금 계좌(상품계좌종류 22 등) 전용 조회. 전용 URL 세그먼트(`/trading/pension/...`)를
쓰는 니치 영역이라 별도 네임스페이스로 둔다. 모두 **모의투자 미지원**(실전만).

```python
kis.pension.deposit()                        # 예수금총액 / 정산·결제 예정
kis.pension.buyable(symbol, limit_price=…)   # 주문가능현금 / 최대매수수량
kis.pension.balance()                        # 보유종목 + 예수금기준 요약
kis.pension.present_balance()                # 체결기준 잔고 + 손익요약
kis.pension.orders(only_unfilled=…)          # 당일 체결/미체결
```

보유 종목행은 국내 계좌의 `Position` 과 같은 형태를 재사용한다(잔고 vs 체결기준의 필드
매핑 차이만 있다 — docstring 참조).

주문 자체(매수/매도)는 국내 주식과 같은 안전 커널을 공유한다. 계좌가 주문 불가 유형이면
(예: IRP 29, DC 55) `KISClient(orderable=…)` 가드가 자동으로 막는다 —
[orders-and-safety.md](orders-and-safety.md) 참조.

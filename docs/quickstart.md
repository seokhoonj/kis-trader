# 빠른 시작

## 세션

```python
from kis_openapi import KISClient

kis = KISClient(
    app_key="앱키",
    app_secret="앱시크릿",
    account="12345678-01",   # 계좌번호 8-2
    environment="real",       # "real" 실전 / "demo" 모의
)
```

주요 옵션(안전 스위치):

```python
KISClient(
    …,
    orderable=True,      # 주문 가능 여부 (계좌 유형에서 자동 판단)
    allow_credit=False,  # 신용주문은 명시적 허용 필요
    throttle=True,       # 초당 유량제한 준수 (실전 15 / 모의 1)
    risk=None,           # 사전 리스크 한도(RiskLimits)
)
```

## 시세

```python
s = kis.domestic.stock("005930")
s.quote()                       # 현재가
s.bars("1d", start="20240101")  # 일봉 (1m/1d/1wk/1mo)
s.order_book()                  # 호가
s.trades()                      # 체결

kis.overseas.stock("AAPL").quote()          # 거래소 자동(NAS)
kis.overseas.stock("AAPL").current_price()
```

## 계좌

```python
kis.domestic.account.balance()      # 예수금/평가 요약
kis.domestic.account.positions()    # 보유 종목
kis.overseas.account.positions()    # 해외 보유 (전체 시장 합산)
```

## 주문

```python
r = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)
r.client_order_id, r.order_id, r.status

kis.orders.reconcile(r.client_order_id)   # 브로커와 대조해 확정
kis.orders.cancel(r.client_order_id)       # 취소
```

자세히 → [주문·안전](orders-and-safety.md)

## 결과 객체

```python
q = kis.domestic.stock("005930").quote()
q.current_price     # 매핑 필드 (Decimal)
q._raw              # 원본 KIS 응답 (읽기전용)
help(type(q))       # 필드 설명(한국어) + KIS URL/TR-id
```

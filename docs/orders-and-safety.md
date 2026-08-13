# 주문·안전

주문은 비가역·비멱등이라, 클라이언트측 안전 커널을 거친다.

## 주문

```python
s = kis.domestic.stock("005930")

s.buy(quantity=10, limit_price=70000)   # 지정가
s.buy(quantity=10)                       # 시장가 (limit_price 생략)
s.sell(quantity=10, limit_price=71000)
```

`buy`/`sell` 은 **keyword-only** — 수량/가격이 순서 실수로 뒤바뀌지 않는다.

### 종류

```python
s.buy(quantity=10, limit_price=70000, division="immediate_limit")  # 최유리
s.buy(quantity=10, limit_price=70000, division="priority_limit")   # 최우선
s.buy(quantity=10, limit_price=70000, division="conditional_limit")# 조건부
s.buy(quantity=10, limit_price=70000, time_in_force="ioc")         # IOC/fok
s.credit_buy(quantity=10, credit_type="…", limit_price=70000)      # 신용(opt-in)
s.reserve_buy(quantity=10, limit_price=70000)                       # 예약
```

## 확정·취소·정정 — `kis.orders`

```python
r = s.buy(quantity=10, limit_price=70000)

kis.orders.reconcile(r.client_order_id)                    # 브로커 대조 → 확정
kis.orders.modify(r.client_order_id, limit_price=70500)    # 정정
kis.orders.cancel(r.client_order_id)                       # 취소
```

## 세 가지 불변식

커널이 절대 깨지 않는 것:

- **오확정 금지** — 접수 확실치 않으면 in-flight로 남긴다 (확정으로 뭉개지 않음)
- **write 무재시도** — 타임아웃 난 주문은 재전송하지 않는다 (재시도 = 이중주문)
- **보수적 reconcile** — 응답이 모호하거나(행 2건+) 비정상 수치면 확정 안 함

`client_order_id` + 지문을 디스크에 영속 → 재시작 후에도 중복방지. claim은 원자적이고 **와이어 접촉 전**.

## 사전 리스크 한도

```python
from kis_openapi import RiskLimits
from decimal import Decimal

kis = KISClient(…, risk=RiskLimits(
    max_order_quantity=1000,                # 수량 상한
    max_order_notional=Decimal("50000000"), # 금액 상한(원)
    price_collar_percent=Decimal("5"),      # 현재가 ±5% 이탈 거부
    enforce_tick_size=True,                 # KRX 호가단위 강제
))
```

와이어 전(claim 전)에 거부. 현재가 조회 실패/0 이면 fail-closed.

## 기본 차단

```python
KISClient(orderable=True)     # 주문 게이트 (계좌 유형 자동: IRP off, DC 거부)
KISClient(allow_credit=False) # 신용주문은 명시적 opt-in 없으면 거부
```

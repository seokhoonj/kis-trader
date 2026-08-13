# 주문

주문은 실수하면 돈이 나가는 일이라, 이 라이브러리는 **안전 우선**으로 설계됐다. 먼저 어떻게
내는지 보고, 뒤에서 안전장치를 설명한다.

## 한눈에 — 매수부터 취소까지

```python
s = kis.domestic.stock("005930")

r = s.buy(quantity=10, limit_price=70000)  # 1) 지정가 매수
print(r.client_order_id, r.status)  #    -> 주문 키, 상태

kis.orders.reconcile(r.client_order_id)  # 2) 실제 접수됐는지 확인
kis.orders.modify(r.client_order_id, limit_price=70500)  # 3) 가격 정정
kis.orders.cancel(r.client_order_id)  # 4) 취소
```

## 매수·매도

```python
s.buy(quantity=10, limit_price=70000)  # 지정가 매수
s.buy(quantity=10)  # 시장가 매수 (가격 생략)
s.sell(quantity=10, limit_price=71000)  # 지정가 매도
```

반환값 `ExecutionReport` 의 필드:

| 필드 | 뜻 |
|---|---|
| `client_order_id` | 이 주문의 고유 키 (취소/정정할 때 지목) |
| `order_id` | 증권사 주문번호(ODNO), 접수돼야 채워짐 |
| `side` | 매수/매도 |
| `status` | 주문 상태 (`PENDING_NEW`, `FILLED`, `CANCELED` …) |
| `filled_quantity` | 체결 수량 |
| `average_price` | 평균 체결가 (체결 전이면 `None`) |

::: {.callout-important}
`buy`/`sell` 은 **반드시 이름 붙여서**(`quantity=`, `limit_price=`) 호출한다. 순서 실수로
수량과 가격이 뒤바뀌는 사고를 막기 위해서다.
:::

## 주문 종류

```python
s.buy(quantity=10, limit_price=70000, division="immediate_limit")  # 최유리 지정가
s.buy(quantity=10, limit_price=70000, division="priority_limit")  # 최우선 지정가
s.buy(quantity=10, limit_price=70000, division="conditional_limit")# 조건부 지정가
s.buy(quantity=10, limit_price=70000, time_in_force="ioc")  # IOC (즉시체결·잔량취소)
s.buy(quantity=10, limit_price=70000, time_in_force="fok")  # FOK (전량아니면 취소)
```

::: {.callout-note}
## 주문 구분·조건이 뭔가
- **지정가** — 원하는 가격 지정(`limit_price`). **시장가** — 가격 생략, 지금 시세로 즉시 체결.
- **최유리 지정가** — 주문 순간 **상대편 최우선 호가**로. **최우선 지정가** — **내 방향 최우선 호가**로.
- **조건부 지정가** — 장중엔 지정가, 미체결이면 장마감 동시호가에서 시장가로 전환.
- **IOC** — 지금 체결되는 만큼만 체결하고 잔량 취소. **FOK** — 전량 즉시 체결 안 되면 전량 취소.
:::

## 정정·취소·확인

취소/정정은 `client_order_id` 로 지목한다.

```python
kis.orders.modify(r.client_order_id, limit_price=70500)  # 가격 정정
kis.orders.modify(r.client_order_id, quantity=5)  # 수량 정정
kis.orders.cancel(r.client_order_id)  # 취소
```

**주문이 진짜 들어갔는지 확인** — `reconcile`:

```python
kis.orders.reconcile(r.client_order_id)
```

주문을 보냈는데 응답이 불확실할 때(타임아웃 등), 라이브러리는 **재전송하지 않고** 그 주문을
"상태 불명"으로 남긴다(재전송 = 이중주문이니까). `reconcile` 은 그럴 때 **KIS 서버에 실제로
조회**해서 접수/체결됐는지 상태를 확정한다.

## 예약·주간거래

```python
s.reserve_buy(quantity=10, limit_price=70000)  # 국내 예약(다음 영업일)
kis.overseas.stock("AAPL").overnight_buy(quantity=1, limit_price=150)  # 미국 오버나이트(한국 낮)
kis.overseas.stock("AAPL").reserve_buy(quantity=1, limit_price=150)  # 미국 예약
```

## 신용주문

기본으로 **막혀 있다**. 쓰려면 세션에서 명시적으로 켠다.

```python
kis = KISClient(…, allow_credit=True)
kis.domestic.stock("005930").credit_buy(quantity=10, credit_type="…", limit_price=70000)
```

---

## 안전장치 (왜 이렇게 만들었나)

주문은 되돌릴 수 없고, "다시 보냈더니 두 번 주문됐다"가 실제로 일어난다. 그래서 커널이
지키는 세 가지:

- **오확정 금지** — 접수됐는지 확실치 않으면 확정 안 하고 "불명"으로 남긴다
- **재시도 금지** — 타임아웃 난 주문은 재전송하지 않는다 (재시도 = 이중주문)
- **보수적 확인** — 조회 결과가 애매하면(같은 조건 주문이 여러 건 등) 확정하지 않는다

같은 주문을 실수로 두 번 내도 **지문으로 중복 감지**해 막고, 이 기록은 디스크에 남아
프로그램을 재시작해도 유지된다.

## 사전 리스크 한도

주문 나가기 전에 미리 걸러주는 안전망. 옵션이다.

```python
from kis_openapi import RiskLimits
from decimal import Decimal

kis = KISClient(…, risk=RiskLimits(
    max_order_quantity=1000,  # 한 주문 최대 수량
    max_order_notional=Decimal("50000000"),  # 한 주문 최대 금액(원)
    price_collar_percent=Decimal("5"),  # 현재가 대비 ±5% 벗어나면 거부
    enforce_tick_size=True,  # 호가단위 안 맞으면 거부
))
```

현재가 대비 말도 안 되는 가격(손가락 실수)을 미리 막는 용도다.

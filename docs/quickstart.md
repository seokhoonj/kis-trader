# 빠른 시작

`kis-openapi`는 한국투자증권(KIS) Open API를 위한 파이썬 클라이언트다. 모든 것은 하나의
세션 객체 `KISClient` 에서 출발한다.

## 1. 자격증명

KIS 개발자센터에서 발급한 앱키/앱시크릿과 계좌번호(8-2)를 준비한다.

```python
from kis_openapi import KISClient

kis = KISClient(
    app_key="발급받은_앱키",
    app_secret="발급받은_앱시크릿",
    account="12345678-01",       # 계좌번호 (8자리-2자리)
    environment="real",          # "real"(실전) / "demo"(모의)
)
```

`environment="demo"` 면 모의투자 서버로 나간다(TR-id가 자동으로 모의용으로 바뀐다).

### 생성자 옵션 (안전 스위치)

| 인자 | 기본 | 뜻 |
|---|---|---|
| `orderable` | `True` | 이 계좌로 주문을 낼 수 있는지. 상품계좌종류(IRP/DC 등)에서 자동으로 꺼진다 |
| `allow_credit` | `False` | 신용주문 허용 여부 — 고위험이라 명시적 opt-in |
| `throttle` | `True` | 초당 유량제한(실전 15/모의 1)을 클라이언트에서 준수 |
| `risk` | `None` | 사전 리스크 한도(`RiskLimits`) — 수량/금액/가격 collar |
| `store` | `None` | 주문 중복방지 저장소(`OrderStore`); 미지정이면 표준 위치에 자동 |

## 2. 시세 조회

종목 핸들에서 바로 조회한다.

```python
stock = kis.domestic.stock("005930")     # 삼성전자
stock.quote()                            # 현재가 스냅샷
stock.bars("1d", start="20240101")       # 일봉 (start~end)
stock.order_book()                       # 호가창
stock.trades()                           # 체결 내역

aapl = kis.overseas.stock("AAPL")        # 거래소는 종목마스터로 자동 해석(NAS)
aapl.quote()
aapl.current_price()
```

## 3. 계좌 조회

```python
kis.domestic.account.balance()           # 예수금/평가 요약
kis.domestic.account.positions()         # 보유 종목
kis.overseas.account.positions()         # 해외 보유 (market=None 이면 전체 시장 합산)
```

## 4. 주문

주문은 안전 커널을 거친다. 자세한 계약은 [orders-and-safety.md](orders-and-safety.md).

```python
report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)
print(report.client_order_id, report.order_id, report.status)

kis.orders.reconcile(report.client_order_id)   # 브로커와 대조해 확정
kis.orders.cancel(report.client_order_id)       # 취소
```

## 5. 결과 객체

모든 result 타입은 **frozen(불변)** 이고, 매핑된 필드 + 원본 벤더 페이로드(`_raw`)를 함께
가진다. 각 필드의 KIS 와이어 키와 단위(원/주/%)는 docstring에 있다.

```python
q = kis.domestic.stock("005930").quote()
q.current_price        # 매핑된 현재가 (Decimal)
q._raw                 # 원본 KIS 응답 (읽기전용 MappingProxy)
help(type(q))          # 필드별 설명(한국어) + KIS URL/TR-id
```

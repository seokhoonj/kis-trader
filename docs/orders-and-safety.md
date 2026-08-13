# 주문과 안전 모델

증권 주문은 **비가역·비멱등 부작용**이다. 한 번 접수되면 되돌릴 수 없고, "다시 보냈더니
두 번 주문됐다"가 실제로 일어난다. 이 패키지의 주문 경로는 그 사실을 전제로 설계된
클라이언트측 안전 커널을 거친다.

## 주문 내기

```python
stock = kis.domestic.stock("005930")

report = stock.buy(quantity=10, limit_price=70000)   # 지정가
report = stock.buy(quantity=10)                       # 시장가 (limit_price 생략)
report = stock.sell(quantity=10, limit_price=71000)
```

반환값은 `ExecutionReport`:

```python
report.client_order_id   # 클라이언트 멱등키 (생략 시 자동 발행)
report.order_id          # 브로커 주문번호(ODNO) — 접수돼야 채워짐
report.status            # PENDING_NEW / ... (주문 상태)
report.filled_quantity
```

### 주문 종류

| 무엇 | 어떻게 |
|---|---|
| 지정가 / 시장가 | `limit_price=` 주면 지정가, 생략하면 시장가 |
| 최유리 / 최우선 / 조건부 지정가 | `division="immediate_limit" / "priority_limit" / "conditional_limit"` (국내 전용) |
| IOC / FOK | `time_in_force="ioc" / "fok"` |
| 신용주문 | `credit_buy(...)` / `credit_sell(...)` — `KISClient(allow_credit=True)` 필요 |
| 예약주문 | `reserve_buy(...)` / `reserve_sell(...)` (국내=다음영업일, 미국=예약) |
| 미국 주간거래 | `overseas.stock(...).daytime_buy(...)` / `daytime_sell(...)` |

`buy`/`sell` 은 **keyword-only** 다(`quantity=`, `limit_price=` …) — 인자 순서 실수로
수량/가격이 뒤바뀌는 사고를 원천 차단한다.

## 세 가지 안전 불변식

주문 커널이 **절대** 깨지 않는 세 가지:

1. **오확정 금지 (I1)** — 불확실하면 주문을 *in-flight* 로 남긴다. 브로커가 접수했는지
   확실치 않으면 절대 "확정"으로 뭉개지 않는다. 잘못된 확정은 실제 주문을 잃거나 이중전송을
   부른다.
2. **write 무재시도 (I2)** — 타임아웃 난 POST(주문)는 **재전송하지 않는다**. 재시도는 곧
   이중주문이므로, in-flight 로 남기고 사람이 확인하게 한다. (읽기 GET만 재시도 대상.)
3. **보수적 reconcile (I3)** — 브로커 응답이 모호하거나(같은 종목·수량·가격의 행이 2건 이상)
   비정상 수치(NaN 등)면 확정하지 않는다.

## 중복방지 — `OrderStore`

각 주문은 `client_order_id` 와 **지문(fingerprint)** 을 가진다. 같은 논리적 주문을 다시
내면 지문으로 중복이 감지돼, 재시도가 두 번째 주문이 되는 일이 없다.

- 지문은 디스크에 **영속**된다(프로세스 재시작 후에도 장벽 유지). 스키마는 하위호환으로
  로드된다(구 저장소 재작성 불필요).
- **계좌당 단일 라이터 락**으로 같은 저장소를 두 프로세스가 동시에 쓰는 것을 막는다.
- claim(확보)은 원자적이고 **와이어 접촉 전**에 일어난다 — 확보에 실패하면 주문번호를
  소비하지 않고 와이어도 건드리지 않는다.

저장소 위치는 `KISClient(store=…)` 로 주거나, 미지정 시 플랫폼 표준 상태 디렉터리에 자동
배치된다.

## 대조 / 취소 / 정정 — `kis.orders`

```python
kis.orders.reconcile(client_order_id)   # 브로커 일별체결과 대조해 상태 확정(불확실→in-flight)
kis.orders.cancel(client_order_id)      # 취소
kis.orders.modify(client_order_id, quantity=…, limit_price=…)   # 정정
```

- `reconcile` 는 지문으로 브로커 행을 매칭한다. 매칭 후보가 2건 이상이거나 모호하면 확정하지
   않고 in-flight 로 남긴다(I3).
- `modify` 는 브로커가 정정 시 새 주문번호(ODNO)를 부여하는 것을 반영해, 원 `client_order_id`
   를 살아있는 주문번호로 **재바인딩**한다 — 이후 `cancel`/`modify` 가 낡은 번호를 지목하지 않는다.

## 사전 리스크 한도 — `RiskLimits`

opt-in. `KISClient(risk=…)` 로 주면 **와이어 전(claim 전)** 에 거부한다.

```python
from kis_openapi import RiskLimits
from decimal import Decimal

kis = KISClient(
    …,
    risk=RiskLimits(
        max_order_quantity=1000,               # 수량 상한
        max_order_notional=Decimal("50000000"),# 주문금액 상한(원)
        price_collar_percent=Decimal("5"),     # 현재가 대비 ±5% 이탈 거부
        enforce_tick_size=True,                 # KRX 호가단위 강제
    ),
)
```

price collar / 시장가 notional 은 현재가를 1회 조회해 판정하며, 조회 실패/0 이면
fail-closed(거부)다. KIS 서버측 검사(상하한가/주문가능/매도가능)는 재구현하지 않는다 —
경합과 오확신을 피하려 서버를 정본으로 둔다.

## 고위험은 기본 차단

| 스위치 | 기본 | 효과 |
|---|---|---|
| `orderable` | `True` | 주문 자체의 게이트. 상품계좌종류로 자동 유도 — IRP(29)는 자동 off, DC(55)는 생성 거부, 연금저축(22)은 유지 |
| `allow_credit` | `False` | 신용주문은 명시적 opt-in 없으면 와이어 전 거부(읽기 `credit_buyable` 은 비게이트) |
| `throttle` | `True` | 초당 유량제한(실전 15 / 모의 1) 준수. 유량은 **앱키 단위 합산**이라, 여러 프로세스가 같은 키를 쓰면 조율 불가 → 앱별 별도 키 권장 |

## 정리

읽기는 자유롭게, 주문은 보수적으로. 커널은 "모르면 in-flight, write는 재시도 안 함,
모호하면 확정 안 함"을 지키고, 나머지 안전은 opt-in(RiskLimits)과 기본 차단(orderable/
allow_credit)으로 쌓는다.

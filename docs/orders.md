# 주문

주문은 실수하면 돈이 나가는 일이라, 이 라이브러리는 **안전 우선**으로 설계됐습니다. 먼저 어떻게
내는지 보고, 뒤에서 안전장치를 설명합니다.

## 한눈에 — 매수부터 취소까지

```python
stock = kis.domestic.stock("005930")

report = stock.buy(quantity=10, limit_price=70000)            # 1) 지정가 매수
print(report.client_order_id, report.status)                  #    -> 주문 키, 상태

kis.orders.reconcile(report.client_order_id)                  # 2) 실제 접수됐는지 확인
kis.orders.modify(report.client_order_id, limit_price=70500)  # 3) 가격 정정
kis.orders.cancel(report.client_order_id)                     # 4) 취소
```

## 매수·매도

```python
stock.buy(quantity=10, limit_price=70000)   # 지정가 매수
stock.buy(quantity=10)                      # 시장가 매수 (가격 생략)
stock.sell(quantity=10, limit_price=71000)  # 지정가 매도
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
`buy`/`sell` 은 **반드시 이름 붙여서**(`quantity=`, `limit_price=`) 호출합니다. 순서 실수로
수량과 가격이 뒤바뀌는 사고를 막기 위해서입니다.
:::

## 주문 종류

```python
stock.buy(quantity=10, limit_price=70000)                                # 지정가
stock.buy(quantity=10)                                                   # 시장가 (가격 생략)
stock.buy(quantity=10, division="immediate_limit")                       # 최유리 지정가 (상대편 최우선호가로)
stock.buy(quantity=10, division="priority_limit")                        # 최우선 지정가 (내 방향 최우선호가로)
stock.buy(quantity=10, limit_price=70000, division="conditional_limit")  # 조건부 지정가 (limit_price 필요)
stock.buy(quantity=10, limit_price=70000, time_in_force="ioc")           # IOC (즉시체결·잔량취소)
stock.buy(quantity=10, limit_price=70000, time_in_force="fok")           # FOK (전량아니면 취소)
```

### 주문 구분·조건 한눈에

**기본 종류** — `limit_price` 유무로 정해집니다.

| 함수 사용 | 한글 | English | 설명 |
|---|---|---|---|
| `limit_price=70000` | 지정가 | Limit | 지정한 가격으로 |
| `limit_price` 생략 | 시장가 | Market | 지금 시세로 즉시 (얕은 호가면 슬리피지 주의) |

**`division=` (KRX 주문구분, 국내 현금 전용)** — CLI 는 `--division`.

| 값 (`division=`) | 한글 | English | `limit_price` |
|---|---|---|---|
| `"conditional_limit"` | 조건부지정가 | Conditional limit (falls to market at close) | 필요 |
| `"immediate_limit"` | 최유리지정가 | Best-opposite-quote limit | 주지 않음 (상대편 최우선호가) |
| `"priority_limit"` | 최우선지정가 | Best-same-quote limit | 주지 않음 (내 방향 최우선호가) |

**`time_in_force=` (체결·유효조건)** — 지정가/시장가/최유리와 조합.

| 값 (`time_in_force=`) | 한글 | English | 설명 |
|---|---|---|---|
| `"day"` | 당일 | Day (default) | 당일 유효 (기본값) |
| `"gtc"` | 취소전유효 | Good-til-canceled | 취소 전까지 유효 |
| `"ioc"` | 즉시체결·잔량취소 | Immediate-or-cancel | 지금 체결되는 만큼만, 잔량 취소 |
| `"fok"` | 전량체결·아니면취소 | Fill-or-kill | 전량 즉시 아니면 전량 취소 |

최유리/최우선 지정가는 시장이 가격을 정하므로 `limit_price` 를 주면 오류입니다. 최유리 지정가는
시장가의 슬리피지 없이 즉시 체결하려는 안전한 대안입니다(얕은 호가에서 시장가는 나쁜 가격까지
쓸어담을 수 있습니다).

## 정정·취소·확인

취소/정정은 `client_order_id` 로 지목합니다.

```python
kis.orders.modify(report.client_order_id, limit_price=70500)  # 가격 정정
kis.orders.modify(report.client_order_id, quantity=5)         # 수량 정정
kis.orders.cancel(report.client_order_id)                     # 취소
```

**주문이 진짜 들어갔는지 확인** — `reconcile`:

```python
kis.orders.reconcile(report.client_order_id)
```

주문을 보냈는데 응답이 불확실할 때(타임아웃 등), 라이브러리는 **재전송하지 않고** 그 주문을
"상태 불명"으로 남깁니다(재전송 = 이중주문이니까). `reconcile` 은 그럴 때 **KIS 서버에 실제로
조회**해서 접수/체결됐는지 상태를 확정합니다.

## 예약·주간거래

```python
stock.reserve_buy(quantity=10, limit_price=70000)                      # 국내 예약(다음 영업일)
kis.overseas.stock("AAPL").overnight_buy(quantity=1, limit_price=150)  # 미국 오버나이트(한국 낮)
kis.overseas.stock("AAPL").reserve_buy(quantity=1, limit_price=150)    # 미국 예약
kis.overseas.stock("00700").reserve_buy(quantity=100, limit_price=350) # 홍콩 예약(거래소 자동판별)
```

해외 예약은 **거래소로 자동 라우팅**됩니다 — `stock(symbol)` 이 종목 마스터로 거래소를 찾아
미국(NAS/NYS/AMS)과 아시아(홍콩·중국·일본·베트남)를 알아서 가릅니다. 같은 코드가 여러 거래소에 있으면
`exchange=` 를 명시합니다. 홍콩은 결제통화(상품유형)를 `currency="CNY"`/`"USD"` 로 바꿀 수
있습니다(생략 시 HKD). 통화 선택은 홍콩 전용이라 다른 거래소에 `currency` 를 주면 오류입니다.

국내 예약주문 조회·정정·취소(순번 `sequence` 로 지목):

```python
account = kis.account.domestic

account.reserved_orders(start="20240101", end="20240131")  # 예약주문 목록
account.modify_reserved_order("0001", symbol="005930", side="buy", quantity=5, limit_price=71000)
account.cancel_reserved_order("0001")                      # 취소
```

해외 예약주문 조회·취소:

```python
account = kis.account.overseas
account.reserved_orders(start="20240101", end="20240131")  # 미국+아시아 예약 목록(실전 전용)

report = kis.overseas.stock("00700").reserve_buy(quantity=100, limit_price=350)
kis.orders.cancel(report.client_order_id)                 # 아시아 취소 = client_order_id 로

report = kis.overseas.stock("AAPL").reserve_buy(quantity=1, limit_price=150)
account.cancel_reserved_order(report.order_id, receipt_date="20240102")  # 미국 취소 = 예약번호+접수일자
```

## 선물·옵션 주문

국내 선물·옵션은 계약 핸들에서 바로 매매합니다 — 선물은 `kis.domestic.futures(code)`, 옵션은
`kis.domestic.option(code, right="call")`. 옵션 발주에는 콜/풋(`right`)을 지정해야 합니다(조회는 생략 가능).
계약코드는 전광판(`kis.domestic.option_board_futures()`)이나 만기(`kis.domestic.option_expiries()`)로 얻습니다.

```python
futures = kis.domestic.futures("101W09")            # 지수선물 계약
futures.buy(quantity=1, limit_price=350.0)          # 지정가 매수
futures.sell(quantity=1)                            # 시장가 매도 (limit_price 생략)

option = kis.domestic.option("201W09350", right="call")   # 콜옵션 (발주엔 right 필수)
option.buy(quantity=1, limit_price=2.5)
```

주문 구분(`division`)·유효기간(`time_in_force`)·야간장(`night`):

```python
futures.buy(quantity=1, limit_price=350.0, division="immediate_limit")  # 최유리지정가(파생엔 최우선 없음)
futures.buy(quantity=1, limit_price=350.0, time_in_force="ioc")         # day/ioc/fok
futures.buy(quantity=1, limit_price=350.0, night=True)                  # KRX 파생 야간장(STTN, 실전 전용)
```

정정·취소·재조회는 현물과 같은 `kis.orders.*` 로 `client_order_id` 를 지목합니다:

```python
report = futures.buy(quantity=1, limit_price=350.0)
kis.orders.modify(report.client_order_id, limit_price=351.0)   # 가격 정정
kis.orders.cancel(report.client_order_id)                     # 취소
```

::: {.callout-note}
파생 야간장(`night=True`, KRX STTN)은 **모의투자 미지원**이라 실전 세션에서만 나갑니다. 해외 선물·옵션은
아직 **시세·차트·호가 조회만** 지원합니다([한계·미구현](limits.md) 참고).
:::

## 신용주문

디폴트는 **사용 불가**입니다. 사용하려면 세션에서 명시적으로 켭니다.

```python
kis = KISClient(…, allow_credit=True)

stock = kis.domestic.stock("005930")
stock.credit_buyable(credit_type="21")                               # 신용 매수가능 여력
stock.credit_buy(quantity=10, credit_type="21", limit_price=70000)   # 신용 매수(융자신규)
stock.credit_sell(quantity=10, credit_type="25", limit_price=71000)  # 신용 매도(융자상환)
```

---

## 안전장치 (왜 이렇게 만들었나)

주문은 되돌릴 수 없고, "다시 보냈더니 두 번 주문됐다"가 실제로 일어납니다. 그래서 커널이
지키는 세 가지:

- **오확정 금지** — 접수됐는지 확실치 않으면 확정 안 하고 "불명"으로 남깁니다
- **재시도 금지** — 타임아웃 난 주문은 재전송하지 않습니다 (재시도 = 이중주문)
- **보수적 확인** — 조회 결과가 애매하면(같은 조건 주문이 여러 건 등) 확정하지 않습니다

같은 주문을 실수로 두 번 내도 **지문으로 중복 감지**해 막습니다. 다만 이 중복 방지 기록은 기본적으로
**프로세스 메모리 안에만** 있어, 프로그램을 재시작하면 사라집니다. 재시작 뒤에도 중복 방지를 유지하려면
영속 저장소를 주입하세요:

```python
from kis_trader import OrderStore

kis = KISClient(…, store=OrderStore(path="orders.db"))  # 지문 dedup 을 재시작에도 유지
```

## 사전 리스크 한도

주문 나가기 전에 미리 걸러주는 안전망. 옵션입니다.

```python
from kis_trader import RiskLimits
from decimal import Decimal

kis = KISClient(…, risk=RiskLimits(
    max_order_quantity=1000,                 # 한 주문 최대 수량
    max_order_notional=Decimal("50000000"),  # 한 주문 최대 금액(원)
    price_collar_percent=Decimal("5"),       # 현재가 대비 ±5% 벗어나면 거부
    enforce_tick_size=True,                  # 호가단위 안 맞으면 거부
))
```

말도 안 되는 가격 주문을 미리 막는 용도입니다.

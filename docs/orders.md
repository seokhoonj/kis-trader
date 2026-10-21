# 주문

주문은 실수하면 돈이 나가는 일이라, 이 라이브러리는 **안전 우선**으로 설계됐습니다. 먼저 어떻게
내는지 보고, 뒤에서 안전장치를 설명합니다.

## 한눈에 — 매수부터 취소까지

```python
stock = kis.domestic.stock("005930")

report = stock.buy(quantity=10, limit_price=70000)  # 1) 지정가 매수
print(report.client_order_id, report.status)        #    -> 주문 키, 상태

kis.orders.modify(report.client_order_id, limit_price=70500)  # 2) 가격 정정
kis.orders.cancel(report.client_order_id)                     # 3) 취소
```

전송 타임아웃이면 반환값 `report` 가 아직 없을 수 있습니다. 요청 ID를 **발주 전에** 정해 두고
그 ID로 저장된 상태를 확인합니다. 최초 응답이 없어 유효한 증권사 주문번호가 저장되지 않았다면
`reconcile` 은 경제 조건만으로 조회 행을 연결하지 않고 `None` 과 처리 중 기록을 유지합니다.
`None` 은 미접수가 아니라 아직 확인할 수 없다는 뜻입니다. 같은 주문을 다시 보내지 말고
HTS/MTS나 해당 계좌의 주문·체결 조회로 수동 확인하세요.

```python
from uuid import uuid4
from kis_trader.errors import OrderTimeoutError

request_id = str(uuid4())
try:
    report = stock.buy(quantity=10, limit_price=70000, client_order_id=request_id)
except OrderTimeoutError:
    report = kis.orders.reconcile(request_id)

if report is None:
    print("주문 상태 불명: 재전송하지 말고 확인하세요", request_id)
else:
    print(report.client_order_id, report.status)
```

기본 재조회는 저장된 리포트가 있으면 그 결과를 반환합니다. 저장된 유효한 증권사 주문번호가 있는
주문의 현재 체결 상태를 다시 조회하려면 `refresh=True` 를 사용합니다(아래 [정정·취소·확인](#정정취소확인) 참고).
앞 예제의 정정·취소는 남은 주문이 있을 때 가능하며, 그 전에 전량 체결되면 거부될 수 있습니다.

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
| `average_price` | 평균 체결가 (정보가 없으면 `None`; 체결 수량과 함께 확인) |
| `recorded_at` | 이 리포트를 기록·관측한 시각(최초 발주 시각과 다를 수 있음) |

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
stock.buy(quantity=10, division="midpoint")                              # 중간가 (호가 중간값, 수량만)
stock.buy(quantity=10, division="pre_market_close")                      # 장전 시간외 종가 (KRX 전용)
stock.buy(quantity=10, division="post_market_close")                     # 장후 시간외 종가 (KRX 전용)

# 애프터마켓 (KRX 16:00~20:00, exchange="KRX")
am = kis.domestic.stock("005930", exchange="KRX")
am.buy(quantity=10, limit_price=70000, division="after_market_limit")  # 애프터마켓 지정가 (limit_price 필요)
am.buy(quantity=10, division="after_market_immediate_limit")           # 애프터마켓 최유리지정가
am.buy(quantity=10, division="after_market_priority_limit")            # 애프터마켓 최우선지정가 (day 전용)

# NXT 프리마켓 GTP (Good Till Pre-Market, exchange="NXT")
gtp = kis.domestic.stock("005930", exchange="NXT")
gtp.buy(quantity=10, limit_price=70000, division="gtp_limit")  # GTP 지정가 (limit_price 필요, day 전용)
gtp.buy(quantity=10, division="gtp_immediate_limit")           # GTP 최유리지정가 (day 전용)

stock.buy(quantity=10, limit_price=70000, stop_price=69000)     # 스톱지정가 (트리거 도달 시 limit_price 로 접수, KRX 전용)
stock.buy(quantity=10, limit_price=70000, time_in_force="ioc")  # IOC (즉시체결·잔량취소)
stock.buy(quantity=10, limit_price=70000, time_in_force="fok")  # FOK (전량아니면 취소)
```

### 주문 구분·조건 한눈에

**기본 종류** — `limit_price` 유무로 정해집니다.

| 함수 사용 | 한글 | English | 설명 |
|---|---|---|---|
| `limit_price=70000` | 지정가 | Limit | 지정한 가격으로 |
| `limit_price` 생략 | 시장가 | Market | 지금 시세로 즉시 (얕은 호가면 슬리피지 주의) |

**`division=` (국내 주식 주문구분)** — CLI 는 `--division`.

| 값 (`division=`) | 한글 | English | `limit_price` | exchange | IOC/FOK |
|---|---|---|---|---|---|
| `"conditional_limit"` | 조건부지정가 | Conditional limit (falls to market at close) | 필요 | KRX | X |
| `"immediate_limit"` | 최유리지정가 | Best-opposite-quote limit | 주지 않음 (상대편 최우선호가) | 전 거래소 | O |
| `"priority_limit"` | 최우선지정가 | Best-same-quote limit | 주지 않음 (내 방향 최우선호가) | KRX | X |
| `"midpoint"` | 중간가 | Midpoint (best bid/ask midpoint) | 주지 않음 | 전 거래소 | O |
| `"pre_market_close"` | 장전 시간외 종가 | Pre-market close price | 주지 않음 | KRX | X |
| `"post_market_close"` | 장후 시간외 종가 | Post-market close price | 주지 않음 | KRX | X |
| `"after_market_limit"` | 애프터마켓 지정가 | After-market limit | 필요 | KRX | O |
| `"after_market_immediate_limit"` | 애프터마켓 최유리지정가 | After-market best-opposite-quote limit | 주지 않음 | KRX | O |
| `"after_market_priority_limit"` | 애프터마켓 최우선지정가 | After-market best-same-quote limit | 주지 않음 | KRX | X |
| `"gtp_limit"` | GTP 지정가 | NXT pre-market GTP limit | 필요 | NXT | X |
| `"gtp_immediate_limit"` | GTP 최유리지정가 | NXT pre-market GTP best-opposite-quote | 주지 않음 | NXT | X |
| `"gtp_priority_limit"` | GTP 최우선지정가 | NXT pre-market GTP best-same-quote | 주지 않음 | NXT | X |

**IOC/FOK** 열: O = `time_in_force="ioc"|"fok"` 지정 가능, X = 미지원.

애프터마켓은 KRX 정규장과 분리된 시장(16:00~20:00)이라 `exchange="KRX"` 에서 위 `after_market_*` 주문구분으로 접수합니다. GTP(Good Till Pre-Market)는 NXT 프리마켓 전용호가로 `exchange="NXT"` 에서 `gtp_*` 로 접수하며, 미체결분은 프리마켓 종료(08:50)에 일괄 취소됩니다. 신용주문(`credit_buy`/`credit_sell`)도 `division=` 으로 애프터마켓/GTP 주문구분을 지정할 수 있습니다.

주문 쪽 `exchange` 값은 `KRX`/`NXT`/`SOR` 입니다. `SOR`(스마트오더라우팅)은 시세 쪽 통합값 `UN`(통합호가)에 대응하며, 핸들을 `exchange="UN"` 으로 잡아도 주문을 만들 때 `SOR` 로 변환합니다(주문을 `UN` 으로 직접 구성하면 거부). CLI 는 주문 서브커맨드에서 `--exchange {KRX,NXT,SOR}`, 시세 서브커맨드에서 `{KRX,NXT,UN}` 로 나뉩니다.

**`time_in_force=` (체결·유효조건)** — 지정가/시장가/최유리/중간가와 조합.

| 값 (`time_in_force=`) | 한글 | English | 설명 |
|---|---|---|---|
| `"day"` | 당일 | Day (default) | 당일 유효 (기본값) |
| `"gtc"` | 취소전유효 | Good-til-canceled | 타입에 정의된 값이며 국내 현금주문 경로에서는 지원하지 않음 |
| `"ioc"` | 즉시체결·잔량취소 | Immediate-or-cancel | 지금 체결되는 만큼만, 잔량 취소 |
| `"fok"` | 전량체결·아니면취소 | Fill-or-kill | 전량 즉시 아니면 전량 취소 |

최유리/최우선 지정가는 시장이 가격을 정하므로 `limit_price` 를 주면 오류입니다. 최유리 지정가는
상대편 최우선호가를 기준으로 가격을 정하는 주문이며, 즉시 체결이나 가격 위험 제거를 보장하지 않습니다.
시장가는 얕은 호가에서 여러 가격 단계에 걸쳐 체결될 수 있습니다.

**스톱지정가** — `stop_price` 는 트리거(조건가격)입니다. 시장이 `stop_price` 에 닿으면 그때
`limit_price` 가격으로 지정가 주문이 접수됩니다. 국내 현금 스톱은 `limit_price` 가 반드시
필요합니다(스톱시장가는 없습니다). `division` 과는 함께 쓸 수 없고, KRX 전용이라 정규장
(09:00~15:30)에만 나갑니다 — 장 시간 밖의 접수는 서버가 거부합니다.

## 정정·취소·확인

취소/정정은 `client_order_id` 로 지목합니다.

```python
kis.orders.modify(report.client_order_id, limit_price=70500)              # 가격 정정
kis.orders.modify(report.client_order_id, limit_price=70000, quantity=5)  # 수량 정정(단가도 함께 전송)
kis.orders.cancel(report.client_order_id)                                 # 취소
```

`modify` 는 `limit_price` 가 필수입니다 — 정정은 새 단가로 재전송하는 것이라, 수량만 바꿀 때도 유지할
단가를 함께 줍니다. `limit_price` 인자를 빼면 Python 인자 오류이며, 기존 단가 유지나 시장가로 자동 전환되지 않습니다.

**저장된 주문 상태 확인과 갱신** — `reconcile`:

```python
kis.orders.reconcile(report.client_order_id)
```

주문을 보냈는데 응답이 불확실할 때(타임아웃 등), 라이브러리는 **재전송하지 않고** 그 주문을
"상태 불명"으로 남깁니다(재전송하면 이중주문이 될 수 있습니다). 최초 응답이 없어 유효한 증권사
주문번호가 저장되지 않았다면 `refresh=True` 여도 브로커 조회를 통한 최초 연결을 시도하지 않고
`None` 과 처리 중 기록을 유지합니다. 종목·방향·수량·가격이 같은 행은 그 요청의 증거가 아닙니다.
저장된 유효한 증권사 주문번호가 있는 주문은 지원 자산군에서 `refresh=True` 로 상태를 갱신할 수
있지만, 번호만으로 모든 날짜·세션의 인과관계가 증명되는 것은 아닙니다. 확인할 수 없으면 `None`
이거나 오류로 남을 수 있습니다. 장내채권과 해외선물옵션의 자동 `reconcile` 은 지원하지 않습니다.
불명 상태는 재전송하지 말고 해당 계좌의 주문·체결 조회 및 HTS/MTS에서 수동 확인하세요. 이 수동
확인은 임의의 증권사 주문번호를 기존 요청 ID에 연결하는 API를 제공한다는 뜻이 아닙니다.

기본(`refresh=False`)은 저장된 리포트가 있으면 그대로 돌려줍니다(브로커를 재조회하지 않는 기본값).
저장된 유효한 증권사 주문번호가 있는 주문의 **체결 수량을 측정**하려면(분할주문 shortfall 집계)
`reconcile(cid, refresh=True)` 로
브로커를 다시 스캔해 **현재** 체결을 받아야 합니다 — 저장된 접수 직후 스냅샷(체결 0)을 그대로 쓰면
미체결로 오인해 재주문 시 이중매수가 날 수 있습니다. CLI `kis order reconcile` 와 MCP `reconcile`
도구는 항상 `refresh=True` 로 동작합니다.

## 예약·주간거래

```python
stock.reserve_buy(quantity=10, limit_price=70000)                       # 국내 예약(다음 영업일)
kis.overseas.stock("AAPL").overnight_buy(quantity=1, limit_price=150)   # 미국 오버나이트(한국 낮)
kis.overseas.stock("AAPL").reserve_buy(quantity=1, limit_price=150)     # 미국 예약
kis.overseas.stock("00700").reserve_buy(quantity=100, limit_price=350)  # 홍콩 예약(거래소 자동판별)
```

해외 예약은 **거래소로 자동 라우팅**됩니다 — `stock(symbol)` 이 종목 마스터로 거래소를 찾아
미국(NAS/NYS/AMS)과 아시아(홍콩·중국·일본·베트남)를 알아서 가릅니다. 같은 코드가 여러 거래소에 있으면
`exchange=` 를 명시합니다. 홍콩은 결제통화(상품유형)를 `currency="CNY"`/`"USD"` 로 바꿀 수
있습니다(생략 시 HKD). 통화 선택은 홍콩 전용이라 다른 거래소에 `currency` 를 주면 오류입니다.

국내 예약주문 조회·정정·취소(예약주문순번 `reserved_order_id` 로 지목):

```python
account = kis.account.domestic

account.reserved_orders(start="20240101", end="20240131")  # 예약주문 목록
account.modify_reserved_order("0001", symbol="005930", side="buy", quantity=5, limit_price=71000)
account.cancel_reserved_order("0001")                      # 취소
```

해외 예약주문 조회·취소(조회 종료일과 시작일의 차이는 6일 이하):

```python
account = kis.account.overseas
account.reserved_orders(start="20240101", end="20240107")  # 미국+아시아 예약 목록(실전 전용)

report = kis.overseas.stock("00700").reserve_buy(quantity=100, limit_price=350)
kis.orders.cancel(report.client_order_id)  # 아시아 취소 = client_order_id 로

report = kis.overseas.stock("AAPL").reserve_buy(quantity=1, limit_price=150)
account.cancel_reserved_order(report.order_id, receipt_date="20240102")  # 미국 취소 = 예약번호+접수일자
```

## 미국주식 TWAP/VWAP 분할주문

미국(NAS/NYS/AMS) 주식은 KIS 서버가 조건에 맞춰 주문을 쪼개 집행하는 알고리즘 분할주문을
지원합니다 — `algo="twap"`(시간 기준)/`"vwap"`(체결량 기준). 이 패키지의 해당 경로는
**미국·실전 전용**입니다. 주문유형별 거래단위는
[KIS 미국시장 안내](https://securities.koreainvestment.com/main/bond/research/_static/TF03ce010000.jsp)와
브로커의 주문 조건을 확인하세요. 아래 예시는 10주를 사용합니다.

```python
aapl = kis.overseas.stock("AAPL")
aapl.buy(quantity=10, limit_price=150, algo="twap")  # 시간창 생략 = 정규장 전체(종료까지) 집행
aapl.buy(quantity=10, limit_price=150, algo="vwap")  # vwap 도 동일
# 명시 시간창을 줄 때는 KST HHMMSS, 같은 날 안(시작 < 종료). 미국 정규장은 KST 로 자정을 넘으므로
# (EDT 22:30~05:00 / EST 23:30~06:00) 한 창이 세션 전체를 덮을 수 없습니다 -- 한쪽 구간만 지정합니다.
aapl.buy(quantity=10, limit_price=150, algo="twap",
         algo_window=("223000", "235959"))  # 자정 이전 구간(EDT 저녁). 이후 구간은 ("000000","050000")
```

**시간창은 생략이 전체 세션 집행입니다.** 명시하려면 `algo_window=(시작, 종료)` 를 **KST HHMMSS** 로,
**같은 날 안에서 시작 < 종료** 로 줍니다. 미국 정규장은 KST 로 자정을 넘어가서(EDT 22:30~05:00 /
EST 23:30~06:00) **한 시간창으로 세션 전체를 덮을 수 없고**, 자정 이전(예 `("223000","235959")`)이나
이후(예 `("000000","050000")`) 한쪽만 지정합니다. 두 가지 거부가 있습니다 -- (1) 시작 >= 종료(자정 넘김
포함)면 서버로 보내기 전에 `KISUsageError` 로 **로컬 거부**, (2) 형식은 맞지만 KST 환산 세션 밖(예 미국 현지시각
`093000`~`160000` 을 그대로 쓰면 KST 낮이라 세션 밖)이면 **서버가** "시작시간이 장시간을 벗어났습니다"로
거부합니다. 예약(`reserve_buy`/`reserve_sell`)의 algo 는 정규장 종료 집행 고정이라 시간창이 없습니다.
미국 예약 접수시간은 [KIS 예약주문 명세](https://apiportal.koreainvestment.com/api/apis/public/detail?accessUrl=%2Fuapi%2Foverseas-stock%2Fv1%2Ftrading%2Forder-resv)상
한국시간 10:00~23:20, 서머타임에는 10:00~22:20이며 16:30~16:45경 시스템 정산 제한이 있습니다. 체결·미체결 진행은 `kis.account.overseas.algo_orders()`/
`.algo_executions()` 로 조회합니다. 접수된 algo 원주문의 정정·취소는 일반 해외주문과 같은
`kis.orders.modify`/`cancel` 로 원주문번호를 지목합니다(정정·취소 요청에는 분할 구분이 실리지 않습니다).

CLI(실전 프로필이 저장돼 있어야 하며, `KIS_ACCOUNT_SUFFIX` 환경변수에는 그 계좌의 마지막 4자리를 직접 설정합니다):

```bash
# 시간창 생략 = 정규장 전체 집행. 명시하려면 --algo-start/--algo-end 는 KST HHMMSS·같은 날·시작<종료
# (자정 넘김 불가). 아래는 10주 예시이며 주문유형별 거래단위는 브로커 안내 확인.
kis --environment real order buy AAPL 10 --region overseas --limit-price 150 \
  --algo twap --algo-start 223000 --algo-end 235959 --execute real --yes --confirm-account "$KIS_ACCOUNT_SUFFIX"
kis --environment real order buy AAPL 10 --region overseas --limit-price 150 --reserve --algo vwap \
  --execute real --yes --confirm-account "$KIS_ACCOUNT_SUFFIX"  # 예약 algo(정규장 종료 고정)
```

## 국내주식 TWAP 분할 실행

`stock.twap(...)` 은 국내 KRX 주문의 총 수량을 클라이언트에서
시간에 걸쳐 균등 분할해 **여러 번 시장가로** 발주합니다. 미국 algo 가 서버에 위임하는 것과 달리
이쪽은 호출한 코드가 스케줄 기간 내내 멈춰 대기하며 각 슬라이스를 직접 냅니다(**포그라운드 블로킹** -- 그 시간 동안 다음 코드로 넘어가지 못하고 기다립니다).
각 슬라이스는 고유 주문번호를 가진 일반 국내주문이라 이중체결 방지·타임아웃 재시도 금지·재조회는
기존 안전 코어가 그대로 적용됩니다.

```python
stock = kis.domestic.stock("005930")
result = stock.twap(side="buy", quantity=100, over="30m", slices=3)  # 지금부터 30분에 걸쳐 3회
# start 지정: 실제 미래 KRX 거래일의 시간대 포함 ISO 시각을 입력합니다.
from datetime import datetime
future_start = datetime.fromisoformat(input("미래 KRX 거래일 13시 (YYYY-MM-DDT13:00:00+09:00): "))
stock.twap(side="buy", quantity=100, over="1h", slices=4, start=future_start)
```

`over` 는 총 소요시간(`30m`/`1h`/`1h30m`/`90s`), `slices` 는 분할 횟수입니다. 슬라이스 간격은
`over/slices` 로 파생되고 수량은 균등 분할하되 나머지는 앞쪽 슬라이스에 1주씩 더합니다(100주·3회 =
34·33·33). **모든 슬라이스가 시작 시각을 KST로 환산한 당일의 KRX 정규장(09:00~15:30 KST) 안이어야** 하며 벗어나면(익일
이월 포함) 발주 없이 오류입니다(시간외·NXT 는 미지원). **`start` 는 시간대가 포함된 datetime이어야 하고 과거일 수
없습니다** — 과거 시작은 모든 슬라이스를 즉시 연속 발주(버스트)해 TWAP 를 무의미하게 만들므로 거부합니다
(생략하면 지금부터). 반환 `TWAPExecutionResult` 는 슬라이스별 결과와 접수·체결 수량, 미달분(`shortfall`),
체결가중 평균단가를 담습니다(집계는 발주 직후 재조회한 스냅샷 기준). `result.outcome` 은
`completed`/`incomplete`/`unknown`/`interrupted` 로 결과를 구분합니다. 중단됐어도 미확인 주문 ID가
남을 수 있으므로 함께 확인해야 합니다. 한 슬라이스가 거부·타임아웃이면 그
슬라이스만 기록하고 다음 슬라이스를 계속하며, `Ctrl-C` 는 남은 슬라이스를 멈추고 여기까지의 부분 결과를
반환합니다(이미 낸 시장가 주문은 되돌리지 않습니다). 조회전용 계좌·인증 오류 등은 전파되며, 그 전에
발주된 슬라이스는 저장소에 기록됩니다. 저장된 유효한 증권사 주문번호가 있는 슬라이스는
`kis.orders.reconcile(..., refresh=True)` 로 갱신할 수 있지만, 최초 타임아웃으로 번호가 없는
슬라이스는 불명으로 남으므로 재전송하지 말고 계좌 조회 및 HTS/MTS에서 수동 확인하세요.

CLI 는 기본이 **dry-run**(스케줄만 표시, 발주 없음)이고 `--execute` 로 블로킹 실행합니다.
다음 실행 예제는 모의 프로필을 설정하고 **KRX 거래일의 13시 이전**에 사용하세요. `--start 130000` 은
같은 날의 미래 13시를 뜻하며, 이미 13시가 지났으면 거부됩니다:

```bash
kis order twap 005930 --side buy --quantity 100 --over 30m --slices 3  # dry-run: 스케줄 미리보기
kis --environment paper order twap 005930 --side buy --quantity 100 --over 30m --slices 3 \
  --start 130000 --execute paper --yes  # 13:00 시작, 실제 실행
```

## 선물·옵션 주문

국내 선물·옵션은 계약 핸들에서 바로 매매합니다 — 선물은 `kis.domestic.futures(symbol)`, 옵션은
`kis.domestic.option(symbol, right="call")`. 옵션 발주에는 콜/풋(`right`)을 지정해야 합니다(조회는 생략 가능).
계약코드는 전광판(`kis.domestic.option_board_futures()`)이나 만기(`kis.domestic.option_expiries()`)로 얻습니다.

```python
futures = kis.domestic.futures("101W09")    # 지수선물 계약
futures.buy(quantity=1, limit_price=350.0)  # 지정가 매수
futures.sell(quantity=1)                    # 시장가 매도 (limit_price 생략)

option = kis.domestic.option("201W09350", right="call")  # 콜옵션 (발주엔 right 필수)
option.buy(quantity=1, limit_price=2.5)
```

주문 구분(`division`)·유효기간(`time_in_force`)·야간장(`night`):

```python
futures.buy(quantity=1, division="immediate_limit")              # 최유리지정가(가격 없음; 파생엔 최우선 없음)
futures.buy(quantity=1, limit_price=350.0, time_in_force="ioc")  # day/ioc/fok
futures.buy(quantity=1, limit_price=350.0, night=True)           # KRX 파생 야간장(STTN, 실전 전용)
```

정정·취소·재조회는 현물과 같은 `kis.orders.*` 로 `client_order_id` 를 지목합니다:

```python
report = futures.buy(quantity=1, limit_price=350.0)
kis.orders.modify(report.client_order_id, limit_price=351.0)  # 가격 정정
kis.orders.cancel(report.client_order_id)                     # 취소
```

::: {.callout-note}
파생 야간장(`night=True`, KRX STTN)은 **모의투자 미지원**이라 실전 세션에서만 나갑니다. 해외 선물·옵션
주문(`kis.overseas.futures(코드).buy()`/`sell()`)도 **실전 전용**입니다(KIS가 모의를 제공하지 않음 —
[한계·미구현](limits.md) 참고).
:::

## 신용주문

디폴트는 **사용 불가**입니다. 사용하려면 세션에서 명시적으로 켭니다.

```python
from kis_trader import KISClient

# general 프로필의 실전 슬롯을 먼저 설정하세요.
kis = KISClient(profile="general", environment="real", allow_credit=True)

stock = kis.domestic.stock("005930")
stock.credit_buyable(credit_type="21")                               # 신용 매수가능 여력
stock.credit_buy(quantity=10, credit_type="21", limit_price=70000)   # 신용 매수(융자신규)
stock.credit_sell(quantity=10, credit_type="25", limit_price=71000)  # 신용 매도(융자상환)
```

신용 주문·조회(`credit_buyable`/`credit_buy`/`credit_sell`)와 `sellable` 은 **실전 전용**(모의투자 미지원)이라, `paper` 세션에서 부르면 `KISUsageError` 입니다.

---

## 안전장치 (왜 이렇게 만들었나)

주문은 되돌릴 수 없고, "다시 보냈더니 두 번 주문됐다"가 실제로 일어납니다. 그래서 커널이
지키는 세 가지:

- **결과 불명 보존** — 전송 결과를 확인할 수 없으면 처리 중 기록을 유지합니다
- **재시도 금지** — 타임아웃 난 주문은 재전송하지 않습니다 (재시도 = 이중주문)
- **보수적 확인** — 저장된 유효한 증권사 주문번호가 없으면 경제 조건만으로 최초 연결하지 않습니다. 번호가 있는 조회도 모호하면 확정하지 않습니다

중복 방지는 **같은 `client_order_id` 를 같은 주문 내용으로 재사용**할 때 적용됩니다. 같은 ID의 내용을
바꾸면 충돌이며, 서로 다른 ID로 낸 경제적으로 같은 주문까지 자동으로 막지는 않습니다. SDK의 기본
저장소는 메모리입니다. 재시작 뒤에도 ID와 결과를 유지하려면 파일 기반 저장소를 주입하세요. 저장소는
한 계좌·상품코드·환경에 묶이므로 다른 범위에서 재사용할 수 없습니다. 소유 범위를 알 수 없는
기존 비어 있지 않은 저장소는 기본적으로 연결을 거부합니다. 기존 기록의 소유 계좌를 사용자가 직접
확인한 경우에만 `OrderStore.bind_scope(..., adopt_legacy=True)` 로 명시적으로 채택하세요. 파일 이름이나
프로필 이름만으로 소유 범위를 추정해서 채택하지 마세요.

다음은 **전체 기록의 소유 계좌·상품코드·환경을 사용자가 별도로 확인한 뒤에만** 실행하는 채택 절차입니다.
`cano`·`product_code`·`environment` 는 그 확인 결과로 직접 준비한 값이며, 파일 이름이나 현재 기본
프로필에서 자동 추정하지 않습니다. 확인할 수 없다면 실행하지 마세요. 이 호출은 소유권을 검증하는
것이 아니라 사용자의 확인을 저장하는 것이며, 주문을 전송하거나 기존 기록을 초기화하지 않습니다.

```python
from kis_trader import OrderStore

# cano, product_code, environment: 전체 기록의 소유를 별도로 확인해 준비한 값
with OrderStore(path="orders.db") as store:
    store.bind_scope(cano=cano, product_code=product_code,
                     environment=environment, adopt_legacy=True)
```

같은 범위로 이미 묶인 저장소에는 영향이 없으며, 다른 범위로 묶인 저장소를 이 옵션으로 바꿀 수는 없습니다.


최초 응답이 없으면 조회 행의 종목·방향·수량·가격 등이 일치해도 원래 요청의 인과적 증거와 같지는
않습니다. HTS/MTS나 다른 프로세스의 같은 조건 주문과 구별하기 어려울 수 있으며, 날짜 조건 역시
인과관계의 증명은 아닙니다. 저장소의 범위 채택은 기존 기록의 계좌 소유에 대한 사용자 확인이며,
개별 불명 요청에 증권사 주문번호를 연결하지 않습니다. 모호한 결과를 미접수로 단정하거나 재전송하지 마세요.

```python
from kis_trader import KISClient, OrderStore

# general 프로필의 실전 슬롯을 먼저 설정하세요.
with OrderStore(path="orders.db") as store:
    with KISClient(profile="general", environment="real", store=store) as kis:
        print(kis.environment)  # 같은 계좌·환경의 요청 ID 기록을 재시작 후에도 보존
```

## 사전 리스크 한도

주문 나가기 전에 미리 걸러주는 안전망. 옵션입니다. 국내·해외 주식 주문 모두 같은 게이트를 지나며,
수량·괴리율 한도는 통화와 무관하게, 금액 한도만 통화별로 적용합니다.

```python
from kis_trader import RiskLimits
from decimal import Decimal

from kis_trader import KISClient

# general 프로필의 실전 슬롯을 먼저 설정하세요.
kis = KISClient(profile="general", environment="real", risk=RiskLimits(
    max_order_quantity=1000,                 # 한 주문 최대 수량
    max_order_notional=Decimal("50000000"),  # 한 주문 최대 금액(원) -- 국내(KRW) 주문에 적용
    max_order_notional_by_currency={         # 해외주식: 거래소 그룹 검사통화별 최대 금액
        "USD": Decimal("10000"),
        "JPY": Decimal("1500000"),
    },
    price_collar_percent=Decimal("5"),       # 현재가 대비 ±5% 벗어나면 거부(해외는 해외 현재가 참조)
    enforce_tick_size=True,                  # 호가단위 안 맞으면 거부(KRX 전용)
))
```

예약 신규·정정도 수량과 확정 가격으로 계산 가능한 금액 한도를 검사합니다. 예약 경로는 추가 시세 조회를
하지 않으므로 설정한 괴리율 한도나 시장가 금액 한도에 참조 가격이 필요하면 검사할 수 없어 거부합니다.
수량 한도만 설정한 시장가 주문은 이 이유로 거부하지 않으며, 취소는 계속 사용할 수 있습니다.

해외주식 금액 한도는 검사 통화별로 두며 환율을 환산하지 않습니다. 즉시 주문과 예약의 기본 검사
통화는 다음 표와 같습니다. 홍콩 예약에서 `currency` 를 명시하면 그 정규화된 통화(HKD/CNY/USD)로
검사하므로 표의 HKD 고정 설명이 적용되지 않습니다.

| 거래소(코드) | 검사 통화 |
|---|---|
| 미국 -- 나스닥·뉴욕·아멕스(NAS/NYS/AMS) | USD |
| 홍콩(HKS) | HKD |
| 상해·심천(SHS/SZS) | CNY |
| 일본(TSE) | JPY |
| 베트남 -- 호치민·하노이(HSX/HNX) | VND |

::: {.callout-note}
즉시 주문과 예약의 기본 검사 통화는 **실제 종목 결제통화와 다를 수 있습니다.** 이 경우 환율 환산
없이 가격 숫자를 검사 통화의 한도와 비교하므로 실효 보호 수준이 달라질 수 있습니다. 종목의 실제
결제통화는 상세호가의 `curr` 등 해당 응답 필드로 별도 확인하세요. 홍콩 예약의 명시적 `currency` 는
그 통화의 금액 한도를 사용하지만, 다른 경로까지 종목별 실제 통화로 자동 교정한다는 뜻은 아닙니다.
:::

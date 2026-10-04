# 실시간 (WebSocket)

실시간 시세·체결·호가·체결통보는 WebSocket으로 받습니다. REST는 그대로 동기지만, 실시간은
`kis.realtime()`으로 얻는 별도 클라이언트입니다. 필요한 의존성(`websockets`·`cryptography`)은
기본 설치에 포함됩니다.

## 기본 사용 (동기)

```python
realtime = kis.realtime()                           # /oauth2/Approval 로 접속키 발급

realtime.subscribe("H0STCNT0", "005930", on=print)  # 삼성전자 실시간 체결(콜백)
realtime.start()                                    # 백그라운드 스레드에서 수신 시작

for tick in realtime.stream():                      # 또는 이터레이터로
    print(tick.tr_id, tick.data.current_price, tick.data.trade_volume)

realtime.stop()                                     # 종료 (with 문도 가능)
```

`subscribe`는 `start` 전에 불러도 되고(연결 후 자동 전송), 후에 불러도 됩니다. 수신 메시지는
등록한 **콜백**과 `stream()` **이터레이터** 양쪽으로 전달됩니다. `with kis.realtime() as realtime:`로
쓰면 블록을 벗어날 때 자동으로 `stop()`됩니다.

각 메시지는 `RealtimeMessage(tr_id, tr_key, data)`이고, `data`는 그 TR의 결과 엔티티(파서가 있으면)
또는 원시 필드 리스트입니다. 값을 꺼낼 땐 엔티티 종류로 좁혀 씁니다:

```python
from kis_trader.realtime.messages import StockTick

def on_tick(msg):
    tick = msg.data
    if isinstance(tick, StockTick):
        print(tick.symbol, tick.current_price, tick.change_percent)

realtime.subscribe("H0STCNT0", "005930", on=on_tick)
```

## 타입 지정 구독 (권장)

`tr_id`를 외우는 대신 자산군·거래소·세션을 메서드로 고르는 타입 지정 표면을 권장합니다.
`realtime.domestic`에서 시작해 종목/세션을 좁히면, 그 계약 하나만 소비하고 결과 타입이
정해진 `RealtimeSubscription[T]`를 돌려줍니다. 콜백에 넘길 타입이나 반환 구독의 원소 타입은
`from kis_trader.realtime import StockTick, StockOrderBook, FuturesTick, OptionTick,
DerivativeOrderBook, StockExecutionNotice, DerivativeExecutionNotice`로 가져옵니다.

```python
realtime = kis.realtime()
realtime.start()

sub = realtime.domestic.stock("005930").trades()   # RealtimeSubscription[StockTick]
for tick in sub:                                    # 이 계약만 흘러옵니다
    print(tick.symbol, tick.current_price, tick.trade_volume)
```

구독 표면은 다음과 같습니다.

- **국내주식** — `realtime.domestic.stock("005930")`에서
  `.trades(venue="KRX")` / `.order_book(venue="KRX")`. `venue`는 `KRX`·`NXT`·`unified`(통합).
  각각 `RealtimeSubscription[StockTick]` / `RealtimeSubscription[StockOrderBook]`.
- **선물** — `realtime.domestic.futures("101W09", kind="index")`에서 `.trades()` / `.order_book()`.
  `kind`는 `index`·`commodity`·`stock`·`night`. 체결은 `RealtimeSubscription[FuturesTick]`,
  호가는 `RealtimeSubscription[DerivativeOrderBook]`.
- **옵션** — `realtime.domestic.option("201W09", kind="index")`에서 `.trades()` / `.order_book()`.
  `kind`는 `index`·`stock`·`night`. 체결은 `RealtimeSubscription[OptionTick]`,
  호가는 `RealtimeSubscription[DerivativeOrderBook]`.
- **체결통보** — `realtime.domestic.execution_notices.stock(hts_id)`는
  `RealtimeSubscription[StockExecutionNotice]`,
  `.derivative(hts_id, session="regular")`는 `RealtimeSubscription[DerivativeExecutionNotice]`.
  `session`은 `regular`·`night_futures`·`night_option`. 계약이 아니라 HTS ID 단위입니다.

소비는 세 가지 중 하나로 합니다. 이터레이터, 컨텍스트 매니저, 콜백 모두 같은 구독을 다룹니다.

```python
# 1) 이터레이터
sub = realtime.domestic.futures("101W09", kind="index").trades()
for tick in sub:
    print(tick.current_price)

# 2) 컨텍스트 매니저 -- 블록을 벗어나면 자동 해제
with realtime.domestic.stock("005930").order_book(venue="NXT") as sub:
    for book in sub:
        print(book.best_ask, book.best_bid)

# 3) 콜백 -- on= 으로 넘기면 수신 스레드에서 호출됩니다
def on_notice(notice):
    print(notice.order_no, notice.executed_qty)

realtime.domestic.execution_notices.stock("myhtsid", on=on_notice)
```

필요하면 `sub.close()`로 개별 구독만 해제합니다(다른 구독·연결은 유지). 같은 계약에 여러
구독이 붙어 있으면 마지막 하나가 닫힐 때 실제 해제가 나갑니다.

```python
sub = realtime.domestic.stock("005930").trades()
...
sub.close()
```

아래 원시 `subscribe`/`stream` 표면은 그대로 쓸 수 있는 탈출구입니다. 타입 지정 표면이 덮지 않는
TR을 직접 등록하거나, 여러 TR을 한 스트림으로 합쳐 받을 때 씁니다.

## 구독 대상 (TR ID)

`tr_id`는 KIS 실시간 거래 코드, `tr_key`는 종목번호(6자리)·심볼·HTS ID입니다. 자주 쓰는 것:

| TR ID | 뜻 | tr_key |
|---|---|---|
| `H0STCNT0` | 국내주식 체결가 (KRX) | 종목번호 |
| `H0STASP0` | 국내주식 호가 (KRX) | 종목번호 |
| `H0STANC0` | 국내주식 예상체결 (KRX) | 종목번호 |
| `H0UPCNT0`·`H0UPPGM0` | 국내지수 체결·프로그램매매 | 업종코드 |
| `HDFSCNT0`·`HDFSASP0` | 해외주식 지연체결가·호가 | 심볼 |
| `H0STCNI0` | 국내주식 체결통보 (암호화) | HTS ID |

전 자산군(국내주식·지수·ELW·파생·해외·채권) 실시간 TR이 등록돼 있습니다. `NXT`/`통합` 시장은
`H0NX…`/`H0UN…` 접두어입니다.

::: {.callout-note}
**체결통보(암호화)** — `H0STCNI0` 같은 `…CNI0`/통보 계열은 AES-CBC로 암호화되어 옵니다. 접속키
발급 응답의 키/IV로 자동 복호화되므로 사용법은 동일합니다(그래서 `cryptography`가 필요).
:::

## async 앱에 붙이기 (FastAPI 등)

async 서버는 동기 래퍼 대신 **코어를 직접** 씁니다:

```python
from kis_trader.realtime import RealtimeConnection, fetch_approval_key, websocket_url

approval = fetch_approval_key(app_key, app_secret, "real")
async with RealtimeConnection(approval, websocket_url("real")) as conn:
    await conn.subscribe("H0STCNT0", "005930")
    async for msg in conn:            # 브라우저로 relay 등
        ...
```

## 알아둘 것

- **등록 상한**: 접속키 하나당 실시간 등록 **41건**. 초과 시 `KISUsageError`.
- **끊김 복원**: 연결이 끊기면 자동 재연결 + 기존 구독 재등록(백오프 -- 재시도 간격을 점점 늘려 다시 붙음). 깨진 프레임은 스트림을
  죽이지 않고 드롭됩니다(로그로 남김).
- **모의투자**: 실시간 일부는 모의투자 환경에서 지원되지 않을 수 있습니다.

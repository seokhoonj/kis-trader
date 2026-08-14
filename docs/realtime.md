# 실시간 (WebSocket)

실시간 시세·체결·호가·체결통보는 WebSocket으로 받습니다. REST는 그대로 동기지만, 실시간은
`kis.realtime()`으로 얻는 별도 클라이언트입니다. 필요한 의존성(`websockets`·`cryptography`)은
기본 설치에 포함됩니다.

## 기본 사용 (동기)

```python
ws = kis.realtime()                                  # /oauth2/Approval 로 접속키 발급

ws.subscribe("H0STCNT0", "005930", on=print)         # 삼성전자 실시간 체결(콜백)
ws.start()                                           # 백그라운드 스레드에서 수신 시작

for tick in ws.stream():                             # 또는 이터레이터로
    print(tick.tr_id, tick.data.current_price, tick.data.trade_volume)

ws.stop()                                            # 종료 (with 문도 가능)
```

`subscribe`는 `start` 전에 불러도 되고(연결 후 자동 전송), 후에 불러도 됩니다. 수신 메시지는
등록한 **콜백**과 `stream()` **이터레이터** 양쪽으로 전달됩니다. `with kis.realtime() as ws:`로
쓰면 블록을 벗어날 때 자동으로 `stop()`됩니다.

각 메시지는 `RealtimeMessage(tr_id, tr_key, data)`이고, `data`는 그 TR의 결과 엔티티(파서가 있으면)
또는 원시 필드 리스트입니다. 값을 꺼낼 땐 엔티티 종류로 좁혀 씁니다:

```python
from kis_trader.realtime.messages import TradeTick

def on_tick(msg):
    tick = msg.data
    if isinstance(tick, TradeTick):
        print(tick.symbol, tick.current_price, tick.change_percent)

ws.subscribe("H0STCNT0", "005930", on=on_tick)
```

## 무엇을 구독하나 (TR ID)

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
from kis_trader.realtime._connection import RealtimeConnection
from kis_trader.realtime._approval import fetch_approval_key
from kis_trader._endpoints import websocket_url

approval = fetch_approval_key(app_key, app_secret, "real")
async with RealtimeConnection(approval, websocket_url("real")) as conn:
    await conn.subscribe("H0STCNT0", "005930")
    async for msg in conn:            # 브라우저로 relay 등
        ...
```

## 알아둘 것

- **등록 상한**: 접속키 하나당 실시간 등록 **41건**. 초과 시 `KISUsageError`.
- **끊김 복원**: 연결이 끊기면 자동 재연결 + 기존 구독 재등록(백오프). 깨진 프레임은 스트림을
  죽이지 않고 드롭됩니다(로그로 남김).
- **모의투자**: 실시간 일부는 모의(`environment="paper"`)에서 지원되지 않을 수 있습니다.

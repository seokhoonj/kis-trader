# 빠른 시작

## 설치

**파이썬에서 `import`** 하려면:

```bash
uv pip install kis-trader     # 또는: pip install kis-trader
```

**명령줄 `kis` 도구**로 쓰려면:

```bash
uv tool install kis-trader    # kis 명령 설치 (또는: pipx install kis-trader)
kis --version
```

**MCP 서버(`kis-mcp`, Claude Desktop 연결)까지** 쓰려면 `[mcp]` 를 붙여 설치합니다(설정은 [MCP 서버](mcp.md)):

```bash
uv tool install 'kis-trader[mcp]'   # kis + kis-mcp 둘 다 설치
```

## 세션 만들기

KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**로 세션을 엽니다(발급 과정은
[앱키 발급받기](appkey.md)):

```python
from kis_trader import KISClient

kis = KISClient(app_key="YOUR_APP_KEY", app_secret="YOUR_APP_SECRET",
                account="12345678-01", environment="paper")  # 연습은 모의투자부터
```

매번 키를 넣기 번거로우면 **한 번 저장**해 두고 `KISClient(profile="general", environment="real")`
한 줄로 열 수 있습니다. 프로필은 계좌 종류, 환경(실전/모의)은 별도 축입니다 -- 자격증명 저장·환경변수·
CLI 사용법은 **[자격증명과 프로필](configuration.md)**을 보세요.

## 첫 조회

```python
stock = kis.domestic.stock("005930")  # 삼성전자
quote = stock.quote()

quote.current_price   # 현재가
quote.change          # 전일대비
quote.change_percent  # 등락률(%)
quote.volume          # 거래량
```

결과 엔티티의 필드는 **읽기전용**입니다. 목록으로 반환된 결과의 바깥 컨테이너는 일반 리스트일 수 있습니다.
필드 설명이 궁금하면 `help(type(quote))`(한국어 설명 + KIS URL·TR-ID),
원본 응답 전체는 `quote._raw`.

## 첫 주문

`buy()` 는 동기로 주문을 전송하고, 접수 응답을 받으면 리포트를 돌려줍니다. 거부와 전송 시간초과는
예외이며, 시간초과는 접수 여부 불명을 뜻합니다. 접수 성공이 체결 완료를 뜻하지는 않습니다:

```python
report = kis.domestic.stock("005930").buy(quantity=10, limit_price=70000)  # 10주 지정가 매수 -- 바로 접수됨

report.order_id          # 거래소 주문번호(ODNO) -- 접수 성공
report.status            # 주문 상태 (NEW = 접수됨. 체결 여부는 별개)
report.client_order_id   # 이 주문의 고유 키 -- 정정·취소할 때 지목

kis.orders.cancel(report.client_order_id)   # 정정·취소는 이 키로 지목
```

`reconcile` 은 저장된 리포트를 반환하거나, 지원 자산군에서 저장된 유효한 증권사 주문번호가 있는
주문의 상태를 갱신합니다. 최초 타임아웃으로 그 번호가 없으면 경제 조건만으로 자동 연결하지 않고
`None` 과 처리 중 기록을 유지합니다. 재전송하지 말고 HTS/MTS나 해당 계좌의 주문·체결 조회로 확인하세요.
시간초과가 발생하면 `report` 변수가 없을 수 있으므로, 발주 전에 정한 `client_order_id` 나 예외의
`client_order_id` 를 사용하세요. `None` 은 미접수가 아니라 아직 불명이라는 뜻입니다. 이미 접수된 주문의
현재 체결 상태를 보려면 `refresh=True` 로 다시 조회합니다. 지원 자산군과 한계는 [주문](orders.md) 참고.

::: {.callout-warning}
조회와 달리 주문은 **실제로 돈이 오가는 실거래**입니다. `buy()`/`sell()` 은 즉시 전송되고 되돌릴 수
없으니, 처음엔 **모의투자(`paper`) 환경**으로 연습하세요. 중복주문 방지·정정/취소·예약주문 같은
안전장치는 **[주문](orders.md)** 에서 자세히 다룹니다(신용거래는 기본으로 막혀 있습니다).
:::

## 다음

- 자격증명·프로필 상세(저장·환경변수·실전/모의) → [자격증명과 프로필](configuration.md)
- 시세·차트·호가 → [시세](quotes.md)
- 주문과 안전장치 → [주문](orders.md)

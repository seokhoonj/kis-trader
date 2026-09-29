# MCP 서버

이 저장소에는 KIS 계좌를 **MCP(Model Context Protocol -- AI 에이전트가 외부 도구·데이터에 표준
방식으로 접근하게 해주는 프로토콜) 도구**로 노출하는 서버가 함께 들어 있습니다: `kis_trader.mcp`.
Claude Desktop·Cursor 같은 MCP 호환 에이전트가 파이썬 코드를 쓰지 않고 이름 붙은 도구를 직접 호출해
계좌를 조회하고 주문을 미리 그려볼 수 있습니다. `kis_trader` 공개 API(안전 코어)를 소비하는 얇은
계층이라 도메인 계산은 하지 않습니다(숫자는 전부 패키지가 만듭니다).

::: {.callout-important}
**실주문 전송(매수/매도/정정/취소)은 노출하지 않습니다.** 실주문은 그 순간 사람의 명시 승인이 필요한데
(→ [주문](orders.md) 안전커널), 헤드리스로 도는 MCP 서버는 그 승인을 받을 수 없습니다. 그래서 이 서버는
**조회 + 주문 미리보기(dry-run) + reconcile** 까지만 엽니다. 실제 주문은 사람이 파이썬 API 로 직접 냅니다.
:::

## 여는 도구

| 도구 | 하는 일 |
|---|---|
| `quote` | 종목 현재가 스냅샷(`market="overseas"` 면 해외) |
| `search` | 이름/질의로 국내 종목 코드 후보 |
| `ranking_change` | 국내 등락률 순위(`gainers`/`losers`) |
| `balance` | 계좌 잔고 요약(주식 계좌) |
| `positions` | 보유 종목 |
| `open_orders` | 미체결 주문 |
| `order_preview` | 주문 **미리보기** -- 전송하지 않고 나갈 티켓만 되읽음(`sent: false`) |
| `reconcile` | 결과 불명 주문의 사후 확정 |

## 안전 설계

- **실주문 미노출** -- 위 callout 대로 전송 도구는 없습니다. `order_preview` 는 무엇이 나갈지 확인만 합니다.
- **누출 차단** -- 응답은 frozen dataclass 를 공개 필드만 골라 직렬화하며 `_raw`(원본 벤더 응답)와 밑줄
  필드를 제거하고, 계좌번호는 끝 4자리만 남겨 마스킹합니다(`****7801`). 자격증명은 환경변수에서만 읽고
  절대 출력하지 않습니다.
- **기본 paper** -- 환경은 기본 모의투자이고, 실전은 `KIS_MCP_ENVIRONMENT=real` 로 명시할 때만 씁니다.
- **주식 계좌 전용 도구** -- `balance`/`positions`/`open_orders` 는 주식 계좌 뷰에서만 동작하고, 다른
  계좌 유형이면 명확히 거부합니다.

## 설치·실행

`mcp` 는 선택 의존성입니다(`import kis_trader` 자체는 `mcp` 없이 동작합니다).

```bash
pip install 'kis-trader[mcp]'
```

자격증명을 환경변수로 준 뒤 콘솔 스크립트로 stdio 서버를 실행합니다.

```bash
export KIS_APP_KEY=...      # 또는 KIS_MCP_PROFILE 로 저장된 프로필 사용
export KIS_APP_SECRET=...
export KIS_ACCOUNT=...
export KIS_MCP_ENVIRONMENT=paper   # 실전은 real (명시할 때만)

kis-mcp
```

MCP 호환 에이전트(Claude Desktop 등)의 서버 설정에 위 `kis-mcp` 실행을 등록하면 에이전트가 도구 목록을
받아 호출합니다. 실제 매매가 필요할 때는 이 서버가 아니라 [주문](orders.md)의 파이썬 API 로, 사람의
승인을 거쳐 냅니다.

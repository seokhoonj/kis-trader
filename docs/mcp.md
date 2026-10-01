# MCP 서버

## MCP 가 처음이라면

**MCP(Model Context Protocol)** 는 **AI 채팅 앱이 외부 도구·데이터에 꽂아 쓰도록 만든 표준 규격**입니다.
비유하면 *AI 앱용 USB 단자* 입니다 -- USB 가 마우스·키보드를 PC 에 꽂는 공통 규격이듯, MCP 는 "계좌
조회 도구" 같은 것을 AI 앱에 꽂는 공통 규격입니다.

쓸모는 이렇습니다. KIS 잔고를 보려면 보통 파이썬을 씁니다:

```python
kis.account.balance()
```

MCP 를 켜면 Claude Desktop 채팅창에 **말로** 합니다:

> 내 KIS 계좌 잔고 보여줘

그러면 에이전트가 알아서 `balance` 도구를 호출해 답합니다. **코드를 쓰지 않고 대화로** 계좌를 다루는
것이 MCP 의 쓸모입니다.

구성은 두 조각입니다:

| 조각 | 무엇인가 | 여기서는 |
|---|---|---|
| **서버(server)** | 도구를 제공하는 쪽. PC 에서 돌며 KIS 연결을 쥡니다 | `kis-mcp`(이 프로젝트가 제공) |
| **클라이언트(client)** | 도구를 쓰는 AI 앱 | Claude Desktop · Cursor |

둘은 MCP 규격으로 대화합니다. 설정 파일에 서버 등록 한 줄을 넣으면(아래 **MCP 클라이언트에 연결**),
에이전트가 켜질 때 도구 목록을 받아 호출합니다.

::: {.callout-note}
MCP 는 **선택 부가기능**입니다. 파이썬으로만 쓸 거면 필요 없습니다(`import kis_trader` 는 `mcp` 없이
동작합니다). Claude Desktop 같은 AI 앱에서 **말로** 계좌를 조회·확인하고 싶을 때만 켜면 됩니다.
:::

## 이 서버가 하는 일

`kis_trader.mcp` 는 KIS 계좌를 **MCP 도구**로 노출하는 서버입니다. `kis_trader` 공개 API(안전 코어)를
소비하는 얇은 계층이라 도메인 계산은 하지 않습니다 -- 숫자는 전부 패키지가 만들고, 이 서버는 그것을
도구로 전달만 합니다.

::: {.callout-important}
**실주문(매수/매도/정정/취소)은 기본값 모의(paper)이고, 실전은 여러 겹의 가드레일을 전부 통과해야만
전송됩니다** -- 이중게이트 + fat-finger 캡(RiskLimits) + 종목 allowlist + **사람 확인(elicitation)** +
세션 서킷브레이커(아래 [실주문 가드레일](#실주문-가드레일)). 하나라도 불충족이면 와이어 전에 거부합니다
(fail-closed). 주문 파라미터(종목/방향/수량/가격)는 **사람이 직접** 주고 확인합니다 -- 조회 결과를 그대로
주문에 흘려 넣지 않습니다(taint 경계).
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
| `place_order` | 국내·해외 주식 **실주문**(매수/매도) -- 가드레일+사람확인 뒤 전송 |
| `cancel_order` | 접수 주문 취소(`client_order_id` 지목) |
| `modify_order` | 접수 주문 가격/수량 정정 |

## 안전 설계

- **실주문은 가드레일 뒤에만** -- `place_order`/`cancel_order`/`modify_order` 는 아래 가드레일을 전부
  통과해야 전송됩니다. `order_preview` 는 여전히 전송 없이 티켓만 보여줍니다.
- **누출 차단** -- 응답은 frozen dataclass 를 공개 필드만 골라 직렬화하며 `_raw`(원본 벤더 응답)와 밑줄
  필드를 제거하고, 계좌번호는 끝 4자리만 남겨 마스킹합니다(`****7801`). 자격증명은 환경변수에서만 읽고
  절대 출력하지 않습니다.
- **기본 paper** -- 환경은 기본 모의투자이고, 실전은 `KIS_MCP_ENVIRONMENT=real` + 이중게이트로 명시할 때만.
- **주식 계좌 전용 도구** -- `balance`/`positions`/`open_orders` 는 주식 계좌 뷰에서만 동작하고, 다른
  계좌 유형이면 명확히 거부합니다.

## 실주문 가드레일

실전(`real`) 주문은 **모든** 겹을 통과해야 전송됩니다(모의는 돈이 안 나가 대부분 생략). 하나라도 불충족이면
와이어 전에 거부합니다(fail-closed).

| 겹 | 내용 | 환경변수 |
|---|---|---|
| 이중게이트 | 실전 언락 2개가 **둘 다** 있어야 | `KIS_MCP_ALLOW_REAL=1` **AND** `KIS_MCP_REAL_CONFIRM=i-understand-real-money` |
| fat-finger 캡 | 실전은 RiskLimits **필수**(미설정=거부) -- 1주문 최대 수량/금액·가격 collar | `KIS_MCP_MAX_ORDER_QTY` · `KIS_MCP_MAX_ORDER_NOTIONAL` · `KIS_MCP_PRICE_COLLAR_PCT` |
| 종목 allowlist | 실전은 명시 allowlist 필수(비면 실거래 0) | `KIS_MCP_SYMBOL_ALLOWLIST=005930,000660` |
| 사람 확인 | elicitation 으로 전체 티켓을 사람에게 보여주고 승인 -- 모델이 대신 승인 못 함. 클라가 미지원이면 실주문 거부 | (클라이언트가 elicitation 지원해야) |
| 서킷브레이커 | 세션 실주문 N건 초과 시 HALT, 수동 재개 전까지 차단 | `KIS_MCP_MAX_REAL_ORDERS`(기본 10) |
| taint 경계 | 주문 파라미터는 사람이 직접 입력 -- 조회 결과(뉴스·시세)가 주문으로 흐르지 않음 | (설계상) |
| 멱등 | `client_order_id` 당일 dedup(중복 전송 방지) | (자동) |

실전 주문을 켜려면 환경에 예:

```bash
export KIS_MCP_ENVIRONMENT=real
export KIS_MCP_ALLOW_REAL=1
export KIS_MCP_REAL_CONFIRM=i-understand-real-money
export KIS_MCP_MAX_ORDER_QTY=100
export KIS_MCP_MAX_ORDER_NOTIONAL=10000000       # 1주문 최대 1천만원
export KIS_MCP_SYMBOL_ALLOWLIST=005930,000660
```

하나라도 빠지면 그 종목 실주문은 거부됩니다(fail-closed). 손실은 전액 계좌 보유자 책임입니다.

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

## MCP 클라이언트에 연결

MCP 호환 에이전트(Claude Desktop·Cursor 등)의 서버 설정 파일에 `kis-mcp` 실행을 등록하면 에이전트가
시작할 때 도구 목록을 받아 호출합니다. Claude Desktop 이라면 `claude_desktop_config.json` 에:

```json
{
  "mcpServers": {
    "kis-trader": {
      "command": "kis-mcp",
      "env": {
        "KIS_MCP_PROFILE": "main",
        "KIS_MCP_ENVIRONMENT": "paper"
      }
    }
  }
}
```

::: {.callout-tip}
**프로필(`KIS_MCP_PROFILE`)로 붙이는 것을 권합니다.** 설정 파일에 `KIS_APP_KEY`/`KIS_APP_SECRET` 을
직접 적는 대신, 저장소(`~/.config/kis-trader/credentials.json`)에 둔 프로필 이름만 주면 자격증명이 설정
파일에 노출되지 않습니다(→ [앱키·자격증명](appkey.md)). 환경은 기본 `paper`, 실전은 `KIS_MCP_ENVIRONMENT`
를 `real` 로 명시할 때만.
:::

`command` 는 `kis-trader[mcp]` 를 설치한 환경의 `kis-mcp` 를 가리켜야 합니다(가상환경이면 그 환경의
절대경로, 예: `/path/to/.venv/bin/kis-mcp`). 등록 뒤 에이전트를 재시작하면 위 8개 도구가 목록에 뜹니다.

실제 매매가 필요할 때는 이 서버가 아니라 [주문](orders.md)의 파이썬 API 로, 사람의 승인을 거쳐 냅니다.
이 서버는 조회·미리보기까지만 열어, 에이전트가 **무엇을 살지 그려 보되 실제로 사지는 못하게** 합니다.

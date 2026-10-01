# MCP 서버

## MCP 가 처음이라면

**MCP(Model Context Protocol)** 는 AI 채팅 앱이 바깥의 도구나 데이터를 가져다 쓸 수 있게 해주는 표준
규격입니다. Anthropic 공식 문서는 이걸 *AI 앱용 USB-C 단자*에 빗댑니다 -- USB-C 가 어느 기기든 꽂아
쓰는 공통 단자이듯, MCP 는 "계좌 조회 도구" 같은 걸 AI 앱에 꽂아 쓰게 해주는 공통 규격입니다.

어디에 쓰는지 보겠습니다. KIS 잔고를 확인하려면 보통 파이썬으로 이렇게 씁니다:

```python
kis.account.balance()
```

MCP 를 켜 두면 Claude Desktop 채팅창에 이렇게 말하기만 하면 됩니다:

> 내 KIS 계좌 잔고 보여줘

그러면 에이전트가 알아서 `balance` 도구를 호출해 답해 줍니다. 코드를 치지 않고 대화만으로 계좌를 다룰 수
있다는 것, 그게 MCP 의 쓸모입니다.

구성은 두 조각입니다:

| 조각 | 무엇인가 | 여기서는 |
|---|---|---|
| **서버(server)** | 도구를 내주는 쪽. 내 PC 에서 돌며 KIS 와 통신합니다 | `kis-mcp`(이 프로젝트가 제공) |
| **클라이언트(client)** | 그 도구를 쓰는 AI 앱 | Claude Desktop · Cursor |

둘은 MCP 규격으로 주고받습니다. 설정 파일에 서버를 한 줄 등록해 두면(아래 [쓰는 법](#쓰는-법-설치하고-연결하기)),
에이전트가 켜질 때 도구 목록을 받아 와 바로 씁니다.

::: {.callout-note}
MCP 는 있어도 되고 없어도 되는 **선택 기능**입니다. 파이썬으로만 다룰 거면 필요 없습니다(`import kis_trader`
는 `mcp` 없이도 동작합니다). Claude Desktop 같은 AI 앱에서 대화로 계좌를 조회·확인하고 싶을 때만 켜면 됩니다.
:::

## 쓰는 법 (설치하고 연결하기)

채팅창에서 뭘 내려받는 게 아닙니다. **한 번** 설치하고 AI 앱 설정 파일에 등록해 두면, 그다음부터는 채팅창에서
말만 하면 됩니다. 순서는 이렇습니다.

**1. 설치** -- 터미널에서(채팅창 아님) MCP 추가 묶음을 깝니다.

```bash
pip install 'kis-trader[mcp]'
```

**2. 자격증명 준비** -- 프로필을 미리 저장해 두는 걸 권합니다(→ [앱키·자격증명](appkey.md)). 그러면 다음
단계 설정 파일에 앱키를 직접 적지 않아도 됩니다.

**3. AI 앱에 서버 등록** -- Claude Desktop 이라면 설정 파일 `claude_desktop_config.json` 을 열어 아래를
넣습니다(서버를 "이 명령으로 띄워라"라고 알려 주는 겁니다).

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

`command` 는 `kis-trader[mcp]` 를 깐 환경의 `kis-mcp` 를 가리켜야 합니다. 가상환경이면 그 환경의 절대경로를
적습니다(예: `/path/to/.venv/bin/kis-mcp`). `which kis-mcp` 로 경로를 확인할 수 있습니다.

**4. AI 앱 재시작** -- Claude Desktop 을 껐다 켜면 kis-trader 도구들이 목록에 나타납니다.

**5. 그다음부터는 대화** -- 채팅창에서 "삼성전자 현재가 알려줘", "내 잔고 보여줘"처럼 말하면 에이전트가
해당 도구를 호출해 답합니다.

::: {.callout-tip}
**프로필(`KIS_MCP_PROFILE`)로 붙이는 걸 권합니다.** 설정 파일에 `KIS_APP_KEY`·`KIS_APP_SECRET` 을 직접
적는 대신, 저장소(`~/.config/kis-trader/credentials.json`)에 둔 프로필 이름만 적으면 앱키가 설정 파일에
드러나지 않습니다. 환경은 기본이 모의(`paper`)이고, 실전은 아래 [실주문 가드레일](#실주문-가드레일)을 모두
갖춰 `KIS_MCP_ENVIRONMENT=real` 로 명시했을 때만 씁니다.
:::

프로필 없이 환경변수로 직접 줘서 터미널에서 직접 띄워 볼 수도 있습니다:

```bash
export KIS_APP_KEY=...
export KIS_APP_SECRET=...
export KIS_ACCOUNT=...
export KIS_MCP_ENVIRONMENT=paper   # 실전은 real (명시할 때만)

kis-mcp
```

## 이 서버가 하는 일

`kis_trader.mcp` 는 KIS 계좌를 **MCP 도구**로 내주는 서버입니다. `kis_trader` 의 공개 API(안전 코어) 위에
얹힌 얇은 계층이라 스스로 계산하지 않습니다 -- 숫자는 전부 패키지가 만들고, 이 서버는 그걸 도구로 전달만
합니다.

::: {.callout-important}
**실주문(매수·매도·정정·취소)은 기본이 모의(paper)이고, 실전 주문은 여러 겹의 안전장치를 모두 통과해야만
나갑니다** -- 이중 잠금 + fat-finger 한도(RiskLimits) + 종목 허용목록(allowlist) + **사람 확인(elicitation)**
+ 세션 서킷브레이커(아래 [실주문 가드레일](#실주문-가드레일)). 하나라도 빠지면 주문을 보내기 전에 막습니다
(fail-closed). 주문 내용(종목·매수매도·수량·가격)은 **사람이 직접** 적고 확인합니다 -- 조회 결과를 그대로
주문에 흘려 넣지 않습니다(taint 경계).
:::

## 제공하는 도구

| 도구 | 하는 일 |
|---|---|
| `quote` | 종목 현재가 스냅샷(`market="overseas"` 면 해외) |
| `search` | 이름·질의로 국내 종목 코드 후보 찾기 |
| `ranking_change` | 국내 등락률 순위(`gainers`/`losers`) |
| `balance` | 계좌 잔고 요약(주식 계좌) |
| `positions` | 보유 종목 |
| `open_orders` | 미체결 주문 |
| `order_preview` | 주문 **미리보기** -- 보내지 않고 나갈 티켓만 보여 줌(`sent: false`) |
| `reconcile` | 결과가 불분명한 주문을 사후 확정 |
| `place_order` | 국내 주식 **실주문**(매수·매도) -- 안전장치·사람 확인을 거친 뒤 전송(해외는 추후) |
| `cancel_order` | 접수된 주문 취소(`client_order_id` 로 지정) |
| `modify_order` | 접수된 주문의 가격·수량 정정 |

## 안전 설계

- **실주문은 안전장치를 거친 뒤에만** -- `place_order`·`cancel_order`·`modify_order` 는 아래 안전장치를
  모두 통과해야 나갑니다. `order_preview` 는 지금도 아무것도 보내지 않고 티켓만 보여 줍니다.
- **정보 유출 차단** -- 응답은 frozen dataclass 에서 공개 필드만 골라 직렬화하고, `_raw`(벤더 원본 응답)와
  밑줄 필드는 떼어 냅니다. 계좌번호는 끝 4자리만 남기고 가립니다(`****7801`). 자격증명은 환경변수에서만
  읽고 절대 출력하지 않습니다.
- **기본은 모의** -- 환경은 기본이 모의투자이고, 실전은 `KIS_MCP_ENVIRONMENT=real` 과 이중 잠금을
  명시했을 때만 씁니다.
- **주식 계좌 전용 도구** -- `balance`·`positions`·`open_orders` 는 주식 계좌에서만 동작하고, 다른 계좌
  유형이면 분명히 거부합니다.

## 실주문 가드레일

실전(`real`) 주문은 아래 **모든** 겹을 통과해야 나갑니다(모의는 돈이 오가지 않아 대부분 건너뜁니다).
하나라도 어긋나면 주문을 보내기 전에 막습니다(fail-closed).

| 겹 | 내용 | 환경변수 |
|---|---|---|
| 이중 잠금 | 실전 잠금 해제 두 가지가 **모두** 있어야 함 | `KIS_MCP_ALLOW_REAL=1` **그리고** `KIS_MCP_REAL_CONFIRM=i-understand-real-money` |
| fat-finger 한도 | 실전은 RiskLimits **필수**(안 두면 거부) -- 1주문 최대 수량·금액, 가격 collar | `KIS_MCP_MAX_ORDER_QTY` · `KIS_MCP_MAX_ORDER_NOTIONAL` · `KIS_MCP_PRICE_COLLAR_PCT` |
| 종목 허용목록 | 실전은 허용목록을 명시해야 함(비어 있으면 실거래 안 함) | `KIS_MCP_SYMBOL_ALLOWLIST=005930,000660` |
| 사람 확인 | elicitation 으로 주문 내용을 사람에게 그대로 보여 주고 승인받음 -- 모델이 사람 대신 승인할 수 없음. 앱이 이 기능을 지원 안 하면 실주문 거부 | (AI 앱이 elicitation 지원해야) |
| 서킷브레이커 | 한 세션에서 실주문이 정한 횟수를 넘으면 멈추고, 사람이 다시 켜기 전까지 막음 | `KIS_MCP_MAX_REAL_ORDERS`(기본 10) |
| taint 경계 | 주문 내용은 사람이 직접 적음 -- 조회 결과(뉴스·시세)가 주문으로 흘러들지 않음 | (설계상) |
| 멱등 | `client_order_id` 로 같은 날 중복 전송을 막음 | (자동) |

실전 주문을 켜려면 환경변수를 예컨대 이렇게 둡니다:

```bash
export KIS_MCP_ENVIRONMENT=real
export KIS_MCP_ALLOW_REAL=1
export KIS_MCP_REAL_CONFIRM=i-understand-real-money
export KIS_MCP_MAX_ORDER_QTY=100
export KIS_MCP_MAX_ORDER_NOTIONAL=10000000       # 1주문 최대 1천만원
export KIS_MCP_SYMBOL_ALLOWLIST=005930,000660
```

하나라도 빠지면 그 종목의 실주문은 거부됩니다(fail-closed). 손실은 전액 계좌 보유자 책임입니다.

## 설치는 선택 묶음

`mcp` 는 선택 의존성입니다 -- `import kis_trader` 자체는 `mcp` 없이도 동작하고, MCP 서버를 쓸 때만
`pip install 'kis-trader[mcp]'` 로 깔면 됩니다(위 [쓰는 법](#쓰는-법-설치하고-연결하기) 참고).

실주문은 위 [실주문 가드레일](#실주문-가드레일)을 거쳐 `place_order` 로 나갑니다. 그 밖의 주문(파생·채권·
해외·조건부 등)은 아직 MCP 에 없으니 [주문](orders.md)의 파이썬 API 로 사람이 직접 냅니다.

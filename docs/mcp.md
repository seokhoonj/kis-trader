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
| **클라이언트(client)** | 그 도구를 쓰는 AI 앱 | Claude Desktop · Cursor · ChatGPT Desktop(참고) |

둘은 MCP 규격으로 주고받습니다. 설정 파일에 서버를 한 줄 등록해 두면(아래 [쓰는 법](#쓰는-법-설치하고-연결하기)),
에이전트가 켜질 때 도구 목록을 받아 와 바로 씁니다.

::: {.callout-note}
MCP 는 있어도 되고 없어도 되는 **선택 기능**입니다. 파이썬으로만 다룰 거면 필요 없습니다(`import kis_trader`
는 `mcp` 없이도 동작합니다). Claude Desktop 같은 AI 앱에서 대화로 계좌를 조회·확인하고 싶을 때만 켜면 됩니다.
:::

## 쓰는 법 (설치하고 연결하기)

채팅창에서 뭘 내려받는 게 아닙니다. 아래를 **한 번** 해 두면, 그다음부터는 AI 앱 채팅창에서 말만 하면
됩니다. **설치는 세 앱(Claude Desktop·Cursor·ChatGPT Desktop)이 공통**이고, **연결 방법만 앱마다
다릅니다** -- 먼저 공통 설치를 끝내고, 쓰는 앱에 맞춰 아래 절 중 하나만 따라 하세요.

**플랜 조건이 앱마다 다릅니다**(개인이라면 Claude Desktop 이 가장 쉽습니다):

| AI 앱 | 커스텀 MCP 플랜 조건 |
|---|---|
| **Claude Desktop** | 무료 포함 **전 플랜 OK**(무료는 시간당 도구 호출·컨텍스트 등 사용량만 제한) -- 장벽 가장 낮음 |
| **Cursor** | **유료(Pro 이상) 필요** -- 무료 Hobby 는 커스텀 MCP 서버 추가 불가 |
| **ChatGPT Desktop** | **Business·Enterprise/Edu 워크스페이스 전용** -- 개인 Plus·Pro·Free 전부 불가(그래서 맨 뒤 참고) |

::: {.callout-tip}
**계좌 하나만 쓰는 가장 짧은 길**은 [가장 쉽게 시작](easy.md) 에 직선으로 정리돼 있습니다(Claude Desktop
기준). 거기서는 설정 블록을 `kis config mcp` 가 **경로까지 채워 통째로** 만들어 주므로 경로·JSON 을 직접
다룰 필요가 없습니다. 아래는 각 단계를 자세히 보고 싶거나 여러 계좌를 쓸 때의 전체 설명입니다.
:::

::: {.callout-note}
**준비물** -- 쓰려는 AI 앱 하나(**Claude Desktop**
[claude.ai/download](https://claude.ai/download), 또는 **Cursor**, 또는 **ChatGPT Desktop**)와 인터넷이면
됩니다. 설치 도구로 쓰는 `uv` 는 다음 단계에서 깔고, 파이썬이 없어도 `uv` 가 알아서 받아 주므로 파이썬을
미리 설치할 필요는 없습니다.
:::

### 설치 (세 앱 공통)

**1. 터미널(명령 입력창)을 엽니다.** -- macOS 는 `⌘ + Space` 를 눌러 "터미널"을 검색해 실행, Windows 는 시작
메뉴에서 "PowerShell"을 실행합니다. 까만(또는 하얀) 글자 입력창이 뜹니다.

**2. 설치 도구 `uv` 를 깔고 패키지를 설치합니다.** -- 아래를 순서대로 붙여넣고 Enter 합니다. 먼저 `uv`
(이미 있으면 "already installed" 라고만 뜨고 넘어갑니다):

```bash
# macOS · Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

`uv` 를 **처음 깔았으면 터미널을 새로 엽니다**(PATH 반영). 그다음 패키지를 설치합니다:

```bash
uv tool install 'kis-trader[mcp]'
```

::: {.callout-note}
`pip` 이 아니라 `uv tool` 을 쓰는 이유: 요즘 macOS 의 `pip` 은 막혀 있고(`externally-managed-environment`
오류), `uv tool` 은 실행 파일을 **한 폴더에 모아** 다음 단계에서 경로를 쉽게 찾을 수 있습니다
(`uv tool dir --bin` 으로 그 폴더를 확인). `[mcp]` 를 빼면 MCP 서버가 안 깔리니 **따옴표째** 그대로 붙여넣으세요.
:::

**3. 자격증명(앱키)을 저장합니다.** -- 터미널에 아래 한 줄을 치면 묻는 대로 입력합니다(앱키·시크릿은 화면에
보이지 않게 받습니다). **모의만** 쓸 거면 실전 단계에서 그냥 Enter 로 건너뜁니다.

```bash
kis config
```

경우별 자세한 저장법(모의만·실전만·여러 계좌·계좌번호를 어디서 보는지)은
[자격증명 설정 -- 상황별 레시피](configuration.md#상황별-설정-레시피-경우별-가이드) 참고.

여기까지가 공통입니다. 이제 쓰는 앱에 맞춰 아래에서 **하나만** 따라 하세요.

## Claude Desktop 에 연결

**플랜:** 무료 플랜 포함 **모든 플랜에서 됩니다**(커스텀 MCP 서버가 무료에도 열려 있음; 무료는 시간당
도구 호출 횟수·컨텍스트 한도 같은 사용량만 제한). 개인이 추가 결제 없이 가장 쉽게 붙이는 길입니다.

Claude Desktop 은 `kis-mcp` 를 **stdio** 로 직접 띄웁니다 -- 설정 파일에 실행 파일 경로만 적으면 됩니다.

**1. `kis-mcp` 경로를 확인합니다.** -- Claude Desktop 은 터미널 PATH 를 모를 수 있어 **전체 경로**로 적어
줍니다. 가장 확실한 건 다음 단계에서 쓸 **`kis config mcp` 가 경로를 자동으로 채워 준 값**이고, 직접
확인하려면 아래를 씁니다. `uv tool install` 로 깔았으면 경로는 **보통** 아래지만(`나` 는 사용자 이름),
환경에 따라 달라질 수 있으니 `uv tool dir --bin` 으로 실제 폴더를 확인하세요:

- **macOS · Linux**: `/Users/나/.local/bin/kis-mcp` (Linux 는 `/home/나/.local/bin/kis-mcp`)
- **Windows**: `C:\Users\나\.local\bin\kis-mcp.exe`

직접 확인하려면 `which kis-mcp`(macOS·Linux) 또는 `where.exe kis-mcp`(Windows PowerShell -- 그냥 `where`
는 PowerShell 에서 다른 명령이라 안 됩니다; `Get-Command kis-mcp` 도 가능) 를 칩니다. **아무것도 안 나오면**
설치가 안 됐거나 PATH 밖입니다 -- 터미널을 **새로 열어** 공통 설치의 2단계를 다시 하세요.

**2. Claude Desktop 설정 파일을 엽니다.** -- 전부 마우스로 합니다:

1. Claude Desktop 의 **설정(Settings)** 을 엽니다 -- macOS 는 **화면 맨 위 메뉴 막대의 `Claude` → `Settings…`**,
   Windows 는 **왼쪽 위 메뉴(또는 설정 아이콘) → `Settings`**. (채팅 창 안의 설정이 아닙니다.)
2. 왼쪽에서 **`Developer`** 탭을 고르고, **`Edit Config`** 버튼을 누릅니다.
3. 그러면 설정 파일(`claude_desktop_config.json`)이 열립니다. 없으면 자동으로 만들어 줍니다. (위치는 macOS
   `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\` 안입니다 -- 버튼이 알아서 열어 주니
   직접 찾아갈 필요는 없습니다.)

**3. 아래 내용을 붙여넣고 저장합니다.** -- 파일 내용을 아래로 바꿉니다(파일이 비어 있거나 처음 만든 경우).
**이미 다른 MCP 서버가 있으면** 파일을 통째로 바꾸지 말고, 기존 `"mcpServers"` 안에 `"kis-trader"` 항목만
추가하세요(통째로 바꾸면 기존 서버가 지워집니다). `command` 에는 **1단계에서 확인한 경로**를,
`KIS_MCP_PROFILE` 에는 저장한 프로필 이름(종합위탁이면 보통 `general`)을 넣습니다. OS에 맞는 쪽을
쓰세요 -- `나` 는 본인 사용자 이름으로 바꾸고, 모르면 **1단계의 `which`/`where` 출력 줄을 그대로 복사**해
넣으면 됩니다(Windows 는 역슬래시를 `\\`/`/` 로). 저장은 `⌘ + S`(Windows `Ctrl + S`).

**macOS · Linux:**

```json
{
  "mcpServers": {
    "kis-trader": {
      "command": "/Users/나/.local/bin/kis-mcp",
      "env": { "KIS_MCP_PROFILE": "general", "KIS_MCP_ENVIRONMENT": "paper" }
    }
  }
}
```

**Windows:**

```json
{
  "mcpServers": {
    "kis-trader": {
      "command": "C:\\Users\\나\\.local\\bin\\kis-mcp.exe",
      "env": { "KIS_MCP_PROFILE": "general", "KIS_MCP_ENVIRONMENT": "paper" }
    }
  }
}
```

::: {.callout-important}
**Windows 경로는 역슬래시(`\`)를 두 번(`\\`) 쓰거나 슬래시(`/`)로 바꿔야 합니다.** `where` 가 준
`C:\Users\...` 를 그대로 붙여넣으면 JSON 이 깨져(백슬래시가 특수문자) 서버가 조용히 안 뜹니다 --
위 예처럼 `C:\\Users\\...\\kis-mcp.exe` 또는 `C:/Users/.../kis-mcp.exe` 로 적으세요.
:::

모의 키를 안 넣었고 **실전 키만** 저장했다면 `KIS_MCP_ENVIRONMENT` 을 `"real"` 로 바꿉니다(실주문은 아래
[실주문 가드레일](#실주문-가드레일)을 추가로 갖춰야 열립니다). `KIS_MCP_PROFILE` 과 `KIS_MCP_ENVIRONMENT`
은 **같은 환경 블록에 저장된 계좌**를 가리켜야 합니다 -- 저장하지 않은 조합을 가리키면 조회가 실패하니,
그때는 아래 연결 팁(HTTP 500 진단·로그 위치)을 보세요.

**4. Claude Desktop 을 완전히 껐다 켭니다.** -- 창만 닫지 말고 **완전히 종료**(macOS `⌘ + Q`, Windows 는 트레이
아이콘에서 종료)한 뒤 다시 실행합니다. 설정은 켤 때 한 번만 읽습니다.

**5. 연결됐는지 봅니다.** -- 채팅 입력창 **왼쪽 아래의 `+`(파일·커넥터 추가) 아이콘**을 누르고 `Connectors`
→ `Manage connectors` 로 가면 `kis-trader` 와 그 도구들이 보입니다. 보이면 성공입니다.

**6. 이제 채팅창에서 말만 하면 됩니다.** -- "삼성전자 현재가 알려줘", "내 KIS 잔고 보여줘"처럼 말하면 Claude
가 알아서 해당 도구를 불러 답합니다.

::: {.callout-tip}
**프로필(`KIS_MCP_PROFILE`)로 붙이는 걸 권합니다.** 설정 파일에 `KIS_APP_KEY`·`KIS_APP_SECRET` 을 직접
적는 대신, 저장소(`~/.config/kis-trader/credentials.json`)에 둔 프로필 이름만 적으면 앱키가 설정 파일에
드러나지 않습니다. `KIS_MCP_PROFILE` 은 **계좌 종류**(`general`·`isa`·`pension`·`irp`), `KIS_MCP_ENVIRONMENT`
은 **환경**(real/paper)으로 서로 다른 축입니다 -- 둘이 같이 **그 환경 블록에 저장된 계좌 종류**를
가리켜야 하며(예 `general`+`paper`), 저장하지 않은 조합(예: 모의 키만 넣고 `real` 을 고름)을 가리키면 조회가
**`HTTP 500`("Error executing tool")** 으로만 실패할 수 있습니다 -- 원인 메시지가 화면에 안 보이니, 그때는
로그를 보세요: macOS `~/Library/Logs/Claude/mcp-server-kis-trader.log`, Windows
`%APPDATA%\Claude\logs\mcp-server-kis-trader.log`. 환경은 기본이 모의(`paper`)이고, 실전은 아래
[실주문 가드레일](#실주문-가드레일)을 모두 갖춰 `KIS_MCP_ENVIRONMENT=real` 로 명시했을 때만 씁니다.
:::

::: {.callout-note collapse="true"}
## (선택) 서버가 잘 뜨는지 터미널에서 점검하기

연결이 안 되면 서버가 혼자 잘 뜨는지 확인해 볼 수 있습니다. 자격증명을 환경변수로 주고 `kis-mcp` 를
실행합니다 -- `kis-mcp` 는 공통 설치 2단계에서 깐 명령으로, 이 줄이 서버를 띄웁니다.

```bash
# macOS · Linux
export KIS_APP_KEY=...
export KIS_APP_SECRET=...
export KIS_ACCOUNT=...
export KIS_MCP_ENVIRONMENT=paper   # 실전은 real (명시할 때만)

kis-mcp   # 서버 시작. 오류 없이 뜨면 정상 -- 연결을 기다리며 멈춰 있고, Ctrl-C 로 끕니다.
```

```powershell
# Windows (PowerShell) -- export 가 아니라 $env: 로 설정합니다
$env:KIS_APP_KEY="..."
$env:KIS_APP_SECRET="..."
$env:KIS_ACCOUNT="..."
$env:KIS_MCP_ENVIRONMENT="paper"   # 실전은 real (명시할 때만)

kis-mcp   # 서버 시작. 오류 없이 멈춰 있으면 정상 -- Ctrl-C 로 끕니다.
```

오류 없이 멈춰 있으면 정상입니다. 이건 "제대로 뜨는지"만 보는 점검용이라, 이 상태로 사람이 직접 대화할
수는 없습니다. 실제 사용에선 이 줄을 직접 칠 일이 없고, 위 순서대로 Claude Desktop 이 `kis-mcp` 를 대신
띄워 줍니다.
:::

## Cursor 에 연결

**플랜:** **유료(Pro 이상) 필요** -- 무료 **Hobby** 플랜은 커스텀 MCP 서버를 추가할 수 없습니다. 무료로
쓰려면 위 Claude Desktop 을 쓰세요.

Cursor 도 Claude Desktop 과 같은 **stdio** 방식입니다. `Settings → MCP Servers` 에서 Claude Desktop 과
**똑같은** 등록 내용을 넣으면 됩니다(위 [Claude Desktop 에 연결](#claude-desktop-에-연결)의 JSON 을 그대로).
Cursor 도 stdio 를 지원하므로 `command` 에 `kis-mcp` 경로를 그대로 적습니다.

::: {.callout-note collapse="true"}
## (고급) 전송 방식 바꾸기 -- stdio · sse · streamable-http

기본 전송은 **stdio** 입니다 -- Claude Desktop·Cursor 는 이걸로 서버를 직접 띄워 씁니다. 한 서버에 여러
클라이언트를 붙이거나 `mcp-remote` 같은 브리지를 쓸 때는 `KIS_MCP_TRANSPORT` 로 HTTP 계열 전송을 고릅니다.

| `KIS_MCP_TRANSPORT` | 전송 | 엔드포인트(기본) |
|---|---|---|
| `stdio`(기본) | 표준 입출력 | (클라이언트가 직접 실행) |
| `sse` | HTTP Server-Sent Events | `http://127.0.0.1:8000/sse` |
| `streamable-http` | HTTP(스트리밍) | `http://127.0.0.1:8000/mcp` |

```bash
export KIS_MCP_TRANSPORT=streamable-http   # 또는 sse
export KIS_MCP_HOST=127.0.0.1               # bind host (기본 127.0.0.1, 로컬 전용)
export KIS_MCP_PORT=8000                    # 기본 8000
kis-mcp
```

**기본은 로컬(127.0.0.1) 전용입니다.** 돈이 오가는 서버라, 토큰 없이 비-로컬 host 로 열려 하면 거부합니다
(fail-closed). ChatGPT 처럼 공개 HTTPS 커넥터만 받는 클라이언트는 아래 둘을 갖춰 노출합니다.

1. **`KIS_MCP_ACCESS_TOKEN`** 을 설정하면 비-로컬 bind 가 열리고, 모든 HTTP 요청에
   `Authorization: Bearer <토큰>` 을 요구합니다(불일치/누락 → 401). 방어심층입니다.
2. 그 앞에 **TLS·인증(OAuth 등)을 맡는 역프록시/터널**(Cloudflare Tunnel+Access, oauth2-proxy 등)을 두어
   127.0.0.1 로 띄운 서버로 전달하는 것이 **권장 방식**입니다. bind host 는 `KIS_MCP_HOST`(기본
   `127.0.0.1`)로 바꾸며, 공개 host 로 직접 bind 한다면 `KIS_MCP_ALLOWED_HOSTS`(쉼표 구분, 예
   `my.host:*`)로 허용 Host 를, `KIS_MCP_ALLOWED_ORIGINS`(쉼표 구분)로 허용할 CORS Origin 을
   지정하세요.

실주문은 전송 방식과 무관하게 사람 확인(elicitation) 게이트를 그대로 거칩니다 -- 원격이라고 느슨해지지
않습니다.
:::

## (참고) ChatGPT Desktop 에 연결

::: {.callout-important}
**커스텀 MCP 커넥터는 현재 ChatGPT Business·Enterprise·Edu 워크스페이스 전용입니다.** OpenAI 도움말
기준 개인 **Plus·Pro 는 커스텀 커넥터를 만들 수 없고**(디렉터리 앱 read/fetch 만 가능), Free 도 안 됩니다
-- Developer mode 를 켜는 토글과 "Create" 버튼 자체가 개인 계정엔 나타나지 않습니다. 개인 계정이면 위
**[Claude Desktop 에 연결](#claude-desktop-에-연결)**(로컬 stdio, 플랜 무관·바로 됨)이나
**[Cursor 에 연결](#cursor-에-연결)**을 쓰세요. 이 절은 **Business·Enterprise/Edu 워크스페이스(관리자가
Developer mode 활성화)** 에서만 유효합니다.
:::

ChatGPT Desktop 앱은 앞의 Claude Desktop·Cursor 와 **붙는 방식이 다릅니다.** 둘은 내 PC 에서 서버를 직접
띄우는 stdio(표준 입출력) 로 붙지만, ChatGPT 는 앱이든 웹이든 **원격 HTTPS 커넥터(SSE)만** 받습니다 --
내 PC 의 명령(`command`)을 가리킬 수 없고 **URL** 로 붙어야 합니다(커넥터는 ChatGPT 계정에 등록돼 웹·앱·
모바일 어디서나 쓰입니다). 그래서 `kis-mcp` 를 **SSE 전송**으로 띄운 뒤, 공개 URL 없이 ChatGPT 가 로컬
서버에 닿게 해주는 **Secure MCP Tunnel**(OpenAI 가 제공하는, 내 PC 에서 바깥으로 연결을 여는 작은
클라이언트) 로 연결합니다. 돈이 오가는 로컬 서버를 공개 인터넷에 직접 노출하지 않아도 됩니다.

준비물: **ChatGPT Business·Enterprise·Edu 워크스페이스**(관리자가 Developer mode 활성화). 개인 Plus·Pro·Free
는 위 경고대로 불가합니다. 순서:

1. **`kis-mcp` 를 SSE 전송으로 띄웁니다** -- 기본은 로컬 전용(`127.0.0.1`) 이고, HTTP 로 열 때는 토큰을
   요구합니다(위 **(고급) 전송 방식 바꾸기** 항목의 fail-closed 규칙):

   ```bash
   export KIS_MCP_PROFILE=general
   export KIS_MCP_ENVIRONMENT=paper          # 실전은 real (가드레일 갖췄을 때만)
   export KIS_MCP_TRANSPORT=sse              # ChatGPT 는 SSE(원격 HTTPS)
   export KIS_MCP_ACCESS_TOKEN=아무_긴_비밀문자열   # 비-로컬 노출 시 필수(Bearer 토큰)
   kis-mcp                                    # http://127.0.0.1:8000/sse 로 뜸
   ```

2. **ChatGPT 에서 커넥터를 만듭니다** -- `chatgpt.com/plugins` → **`Add`** → **`Create MCP App`**.
   **Connection** 에서 **`Tunnel`**(Secure MCP Tunnel)을 고르면 공개 URL 없이 위 로컬 서버(1단계)의 SSE
   엔드포인트로 연결됩니다. 직접 URL 을 쓸 거면 **Server URL** 에 역프록시/터널이 내준 HTTPS 주소(`.../sse`)를 넣습니다.
3. **`Scan Tools`** 를 눌러 도구 목록을 받아옵니다. 인증을 걸었으면(OAuth/토큰) 그 단계를 먼저 통과합니다.
4. 연결되면 ChatGPT 대화에서 "내 KIS 잔고 보여줘" 처럼 말하면 됩니다.

안전: `kis-mcp` 는 **조회·주문 미리보기·대조만** 내주고 실주문 도구는 노출하지 않습니다([안전 설계](#안전-설계)).
HTTP 로 열 때도 비-로컬 bind 는 `KIS_MCP_ACCESS_TOKEN` 없이는 거부되고(fail-closed), 토큰 불일치 요청은
`401` 로 막힙니다. 공개 URL 로 직접 bind 하기보다 **Secure MCP Tunnel 또는 역프록시**를 쓰는 편이
안전합니다(자세한 env 설정은 위 **(고급) 전송 방식 바꾸기** 항목).

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
| `quote` | 종목 현재가 스냅샷(`region="overseas"` 면 해외, `session="after_hours"` 면 국내 시간외 단일가) |
| `search` | 이름·질의로 국내 종목 코드 후보 찾기 |
| `ranking_change` | 국내 등락률 순위(`gainers`/`losers`) |
| `bars` | 국내 종목 기간봉(OHLCV) -- `interval` 일/주/월/분. 기간봉(1d/1wk/1mo)은 `start`(YYYYMMDD) 필수, 1m만 생략 가능. 과거 가격(연초 대비 수익률 등) 계산용 |
| `stock_investor_flows` | 국내 종목 투자자 수급 -- 일자별 개인·외국인·기관 순매수(수량·대금) |
| `market_investor_flows` | 시장 전체(KOSPI/KOSDAQ) 투자자 수급 |
| `investor_net_buy_stocks` | 투자자별 순매수 상위 종목(`basis` 수량/대금, `side` 매수/매도, `investor` 전체/외국인/기관/기타) |
| `balance` | 계좌 잔고 요약 -- `region` 으로 국내/해외를 고르며, 국내는 활성 계좌 종류로 분기(위탁·ISA·연금저축=국내주식, IRP=퇴직연금) |
| `positions` | 보유 종목 -- `region` 으로 국내/해외 |
| `open_orders` | 미체결(정정취소가능) 주문 -- 주문 가능한 국내주식 계좌(위탁·ISA 01/연금저축 22) 전용, IRP(29) 거부 |
| `use_environment` | 활성 환경을 real↔paper 로 전환(재시작 없이; 기동 시 준 실주문 언락 비트는 바꾸지 못함) |
| `use_profile` | 활성 계좌 종류 전환(조회·주문 대상이 함께 바뀜; `general`/`isa`/`pension`/`irp`) |
| `current_account` | 지금 보는 중인 활성 계좌·환경 표시 |
| `order_preview` | 주문 **미리보기** -- 보내지 않고 나갈 티켓만 보여 줌(`sent: false`) |
| `reconcile` | 결과가 불분명한 주문을 사후 확정 |
| `place_order` | 주식 **실주문**(매수·매도). 국내는 `division`([주문](orders.md)의 division 표의 KRX 주문구분; 조건부지정가·애프터마켓 등. NXT 전용 `gtp_*` 는 KRX 기본 경로라 제외)을 받고, 해외는 `region="overseas"` + `exchange`(NAS/NYS/…)로 지정가 주문을 낸다. 안전장치·사람 확인을 거친 뒤 전송 |
| `cancel_order` | 접수된 주문 취소(`client_order_id` 로 지정) |
| `modify_order` | 접수된 주문의 가격·수량 정정 |

## 계좌·환경 전환 (채팅에서)

활성 계좌·환경을 채팅에서 바로 바꿀 수 있어, config 를 고치고 Claude Desktop 을 재시작할 필요가
없습니다. `KIS_MCP_PROFILE`·`KIS_MCP_ENVIRONMENT` 는 **기동 시 초기값**일 뿐입니다.

| 도구 | 말투 | 하는 일 |
|---|---|---|
| `use_environment("real")` / `("paper")` | "실전으로 바꿔줘" | 활성 환경을 토글(언락 비트는 기동 시 고정) |
| `use_profile("pension")` | "연금 계좌로 바꿔줘" | 활성 계좌 종류 변경(조회·주문 대상 함께; `general`/`isa`/`pension`/`irp`) |
| `current_account()` | "지금 뭐 보는 중?" | 활성 `(계좌 종류, 환경)` + 마스킹 계좌 |

전환은 활성 `(계좌 종류, 환경)` 맥락을 바꿀 뿐, **기동 시 정해진 실주문 언락 비트는 바꾸지 못합니다**
(위 callout 참고). 없는 조합(예 모의가 없는 `pension` 을 `paper` 로)이나 지원하지 않는 계좌(파생은
`use_profile` 대상 아님)는 전환 시점에 분명한 에러로 거부합니다(silent 전환 없음).

::: {.callout-important}
**환경을 real 로 바꾸는 것 자체는 실주문 언락 비트를 바꾸지 못합니다.** 실주문 권한은 **기동 시** 준
가드(아래 [실주문 가드레일](#실주문-가드레일))에만 묶입니다. 하지만 서버가 기동 시
`KIS_MCP_ALLOW_REAL=1` + `KIS_MCP_REAL_CONFIRM=i-understand-real-money` 와 함께 떠 있었다면,
`KIS_MCP_ENVIRONMENT=paper` 로 기동했더라도 `use_environment("real")` 뒤에는 실주문이 나갈 수
있습니다(사람 확인·RiskLimits·종목 허용목록은 여전히 적용). **실주문 의도가 없으면 기동 시
`KIS_MCP_ALLOW_REAL` 을 설정하지 마십시오** -- 그러면 환경을 real 로 바꿔도 실주문은 끝까지
막힙니다. 국내 주문은 주문 가능한 주식 계좌(위탁·ISA 는 상품코드 `01`, 연금저축은 `22`)에서 나가고,
IRP(`29`)·파생은 조회 전용이라 주문이 나가지 않습니다. 해외 주문(`region="overseas"`)은
위탁(general) 계좌 전용입니다.
:::

## 안전 설계

- **실주문은 안전장치를 거친 뒤에만** -- `place_order`·`cancel_order`·`modify_order` 는 아래 안전장치를
  거친 뒤에만 나갑니다(도구별로 적용되는 겹이 다릅니다 -- [적용 범위](#도구별-가드-적용-범위) 참고).
  `order_preview` 는 아무것도 전송하지 않지만, 주문 형상 검증에 더해 **발주와 동일한 계좌 권한 게이트**
  와 **해외 실주문 통화별 금액 한도 사전점검**을 적용합니다. 다만 실주문 언락·사람 확인·서킷브레이커는
  **발주 경로에서만** 수행하며 preview 에는 없습니다.
- **정보 유출 차단** -- 응답은 frozen dataclass 에서 공개 필드만 골라 직렬화하고, `_raw`(벤더 원본 응답)와
  밑줄 필드는 떼어 냅니다. 계좌번호는 마지막 4자리만 남기고 가립니다(`****7801`). 자격증명은 환경변수
  또는 `KIS_MCP_PROFILE` 로 지정한 저장 프로필에서만 읽고 절대 출력하지 않습니다.
- **기본은 모의** -- 환경은 기본이 모의투자이고, 실전은 `KIS_MCP_ENVIRONMENT=real` 과 이중 잠금을
  명시했을 때만 씁니다.
- **계좌 종류에 맞는 조회** -- `balance`·`positions` 는 활성 계좌 종류로 분기합니다(위탁·ISA·연금저축은
  국내주식, IRP 는 퇴직연금). 해외주식 잔고·보유는 `region="overseas"` 로 보며, 위탁(general) 계좌의
  **실전 전용**입니다(모의 미지원). `open_orders`(정정취소가능주문조회)는 주문 가능한 국내주식 계좌(위탁·ISA·연금저축) 전용이고 IRP 는 거부되며,
  그 밖의 계좌에서는 분명히 거부합니다. 파생 계좌는 조회조차 노출하지 않습니다. MCP `balance` 는
  (위탁 계좌에서) 국내주식 잔고를 돌려줍니다 -- 국내·채권·해외를 합친 통합 잔고가 필요하면 파이썬 API
  `kis.account.balance()` 를 씁니다.

## 실주문 가드레일

실전(`real`) 주문은 아래 **모든** 겹을 통과해야 나갑니다(모의는 돈이 오가지 않아 대부분 건너뜁니다).
하나라도 어긋나면 주문을 보내기 전에 막습니다(fail-closed).

| 겹 | 내용 | 환경변수 |
|---|---|---|
| 이중 잠금 | 실전 잠금 해제 두 가지가 **모두** 있어야 함 | `KIS_MCP_ALLOW_REAL=1` **그리고** `KIS_MCP_REAL_CONFIRM=i-understand-real-money` |
| fat-finger 한도 | 실전은 RiskLimits **필수**(안 두면 거부) -- 국내는 **크기 캡(1주문 최대 수량 또는 금액)** 이 있어야 게이트를 통과하고(가격 collar 는 크기 캡이 아니라 단독으론 불충족), 해외주식 주문은 **통화별 금액 한도**로 검사하며 그 통화의 한도가 없으면 거부(fail-closed) | `KIS_MCP_MAX_ORDER_QTY` · `KIS_MCP_MAX_ORDER_NOTIONAL`(국내 KRW) · `KIS_MCP_MAX_ORDER_NOTIONAL_<통화>`(예 `..._USD` 해외) · `KIS_MCP_PRICE_COLLAR_PCT`(가격 편차 가드) |
| 종목 허용목록 | 실전은 허용목록을 명시해야 함(비어 있으면 실거래 안 함) | `KIS_MCP_SYMBOL_ALLOWLIST=005930,000660` |
| 사람 확인 | elicitation 으로 주문 내용을 사람에게 그대로 보여 주고 승인받음 -- 모델이 사람 대신 승인할 수 없음. 앱이 이 기능을 지원 안 하면 실주문 거부 | (AI 앱이 elicitation 지원해야) |
| 서킷브레이커 | 한 세션에서 실주문이 정한 횟수를 넘으면 멈추고, 사람이 다시 켜기 전까지 막음 | `KIS_MCP_MAX_REAL_ORDERS`(기본 10) |
| taint 경계 | 주문 내용은 사람이 직접 적음 -- 조회 결과(뉴스·시세)가 주문으로 흘러들지 않음 | (설계상) |
| 중복 차단 | `client_order_id` 로 같은 날 중복 전송을 막음 | (자동) |

### 도구별 가드 적용 범위

세 실주문 도구가 **같은 겹을 전부** 거치는 것은 아닙니다 -- 취소는 노출을 줄이는 동작이라 fat-finger
한도·허용목록을 적용하지 않고, **서킷브레이커 슬롯도 쓰지 않으며 HALT 상태에서도 막히지 않습니다**
(위험한 주문을 빠져나오지 못하게 막는 것이 더 위험하므로 -- HALT 중에도 취소는 나갑니다).

| 도구 | 이중 잠금 | RiskLimits | 종목 허용목록 | 사람 확인 | 서킷브레이커 |
|---|---|---|---|---|---|
| `place_order` | O | O | O (주문 종목) | O | O |
| `modify_order` | O | O | O (원주문 종목; 저장소에서 해석될 때) | O | O |
| `cancel_order` | O | -- | -- | O | -- (HALT 중에도 취소 가능) |

`modify_order` 의 허용목록은 **원주문 종목**으로 확인합니다(정정은 재가격·수량으로 노출을 늘릴 수 있어
신규 주문과 같은 종목 통제를 받습니다). 원주문을 저장소에서 찾지 못하면 허용목록 확인은 건너뛰고 나머지
게이트(이중 잠금·RiskLimits·사람 확인·서킷브레이커)는 그대로 적용합니다. 정정/취소 사람 확인 티켓에는
원주문 **종목·매수/매도**가 함께 표시됩니다.

실전 주문을 켜려면 이 값들을 [쓰는 법](#쓰는-법-설치하고-연결하기)에서 만든 **설정 파일의 같은 `env`
블록**에 넣습니다 -- Claude Desktop 이 서버를 그 설정으로 띄우기 때문에, 터미널에서 친 `export` 는 그
프로세스까지 닿지 않습니다. JSON 이라 값은 전부 문자열로 적습니다.

```json
{
  "mcpServers": {
    "kis-trader": {
      "command": "/Users/나/.local/bin/kis-mcp",
      "env": {
        "KIS_MCP_PROFILE": "general",
        "KIS_MCP_ENVIRONMENT": "real",
        "KIS_MCP_ALLOW_REAL": "1",
        "KIS_MCP_REAL_CONFIRM": "i-understand-real-money",
        "KIS_MCP_MAX_ORDER_QTY": "100",
        "KIS_MCP_MAX_ORDER_NOTIONAL": "10000000",
        "KIS_MCP_MAX_ORDER_NOTIONAL_USD": "10000",
        "KIS_MCP_SYMBOL_ALLOWLIST": "005930,000660"
      }
    }
  }
}
```

터미널에서 `kis-mcp` 를 직접 띄울 때만 같은 값을 `export KIS_MCP_ALLOW_REAL=1` 식으로 줍니다.

하나라도 빠지면 그 종목의 실주문은 거부됩니다(fail-closed). 손실은 전액 계좌 보유자 책임입니다.

## 설치는 선택 묶음

`mcp` 는 선택 의존성입니다 -- `import kis_trader` 자체는 `mcp` 없이도 동작하고, MCP 서버를 쓸 때만
`uv tool install 'kis-trader[mcp]'` 로 설치하면 됩니다(위 [쓰는 법](#쓰는-법-설치하고-연결하기) 참고).

실주문은 위 [실주문 가드레일](#실주문-가드레일)을 거쳐 `place_order` 로 나갑니다. 국내 주식의 지정가·
시장가와 `division` 주문구분(조건부지정가 등), 그리고 `region="overseas"` 해외주식 지정가 주문이
`place_order` 로 되지만, 그 밖의 주문(파생·채권·스톱(조건가) 등)은 아직 MCP 에 없으니 [주문](orders.md)의
파이썬 API 로 사람이 직접 냅니다.

## 면책 · 책임 (꼭 읽어주세요)

이 MCP 서버는 **실제 돈이 오가는 증권 계좌**를 AI 에이전트가 다루게 합니다. 안전장치를 겹겹이 뒀지만,
AI 에이전트로 주문한다는 것 자체에 다음과 같은 **본질적 위험**이 있습니다 -- 모두 이해하고 쓰세요.

- **에이전트는 말을 오해할 수 있습니다.** 자연어를 해석해 도구를 부르므로 의도와 다른 종목·수량·가격으로
  주문할 수 있습니다. 실전에서는 전송 전 **사람 확인(elicitation) 창의 내용을 반드시 직접 검토**하고 맞을
  때만 승인하세요 -- 자동 승인하지 마세요.
- **프롬프트 인젝션 위험.** 에이전트가 읽는 내용(뉴스·시세·웹·문서)에 악의적 지시가 숨어 에이전트를
  조종해 원치 않는 주문을 유도할 수 있습니다(이른바 lethal trifecta). 이 서버는 주문 파라미터를 조회
  결과에서 받지 않고 **사람이 직접 주게**(taint 경계) 하고 allowlist·사람 확인·금액 상한으로 이를
  **줄이지만**, 완전히 없애지는 못합니다.
- **가드레일은 위험을 "줄이는" 장치이지 "보장"이 아닙니다.** 이중 잠금·RiskLimits·allowlist·사람 확인·
  서킷브레이커는 실수와 오용을 어렵게 만들 뿐, 버그·오설정·예기치 못한 경로로 잘못된 주문이 나갈
  가능성을 0으로 만들지는 못합니다.
- **실주문은 되돌릴 수 없습니다.** 접수·체결된 주문은 취소 전까지 유효하고 손익은 즉시 실현됩니다.
- **원격 노출은 운영자 책임.** 서버를 네트워크에 열면(ChatGPT 등) 돈이 오가는 엔드포인트가 노출됩니다 --
  TLS·인증·접근 통제는 서버를 띄운 사람의 책임입니다.

**책임 소재.** 이 소프트웨어는 비공식 오픈소스이며 **"있는 그대로(AS-IS)" 무보증**으로 제공됩니다(MIT
라이선스). 이 서버의 사용(에이전트가 대신 낸 주문을 포함한 실주문·조회)으로 발생한 **모든 금전적 손실·
주문 오류·데이터 오류·오작동에 대한 책임은 전적으로 계좌 보유자 본인에게 있으며, 제작자는 어떠한 책임도
지지 않습니다.** 에이전트가 사용자를 대신해 낸 주문도 **사용자가 낸 주문**으로 간주됩니다.

실거래 전 반드시 **모의투자(paper)로 충분히 연습**하고, 실전은 **소액·좁은 allowlist·낮은 한도**로
시작하세요.

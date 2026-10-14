# 가장 쉽게 시작 (계좌 하나)

Claude Desktop 에서 **"내 잔고 보여줘"** 처럼 말로 쓰는 가장 짧은 길입니다. 계좌 하나만 있으면 됩니다
(실전이든 모의든). 위에서 아래로 그대로 따라 하세요 -- 바꿀 곳은 거의 없습니다.

::: {.callout-tip}
처음이면 **모의투자(paper)** 로 연습하는 것을 권합니다(가상 자금이라 돈이 안 나갑니다). 실전 계좌면
⑤에서 한 군데만 바꾸면 됩니다.
:::

## ① 앱키 받기

[앱키 발급받기](appkey.md) 를 따라 **APP Key · APP Secret · 계좌번호** 세 가지를 준비합니다. 모의로 연습할
거면 모의투자용으로 받으세요(모의는 모의투자 참가신청이 먼저 필요합니다 -- 그 페이지에 안내가 있습니다).

## ② 터미널 열기

명령을 입력하는 까만(또는 하얀) 창입니다.

- **Windows**: 시작 메뉴에서 `PowerShell` 을 검색해 실행
- **Mac**: `⌘ + Space` 를 누르고 `터미널` 을 검색해 실행

아래 단계의 명령들을 이 창에 **붙여넣고 Enter** 합니다.

## ③ 설치

설치 도구 `uv` 를 먼저 깝니다(OS 에 맞는 한 줄):

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv` 를 방금 깔았으면 **터미널을 새로 엽니다**(설치가 반영되도록). 그다음 패키지를 깝니다:

```bash
uv tool install 'kis-trader[mcp]'
```

## ④ 계좌 저장

```bash
kis config
```

묻는 대로 입력합니다(앱키·시크릿은 화면에 보이지 않습니다).

- **계좌 종류 이름**: 그냥 Enter (`general` 로 저장됩니다)
- **[real] 실전 설정?**: 실전 계좌면 `y` 를 누르고 앱키·시크릿·계좌번호 입력 / **모의만 할 거면 그냥 Enter**(건너뜀)
- **[paper] 모의**: 모의 계좌면 앱키·시크릿·계좌번호 입력 / 아니면 그냥 Enter(건너뜀)

계좌번호는 `12345678-01` 처럼 **`계좌번호-상품코드`** 형태로 넣습니다(국내·해외 주식은 끝이 `-01`).
그 뒤에 **계좌비밀번호**를 물으면 모의는 **그냥 Enter** 로 넘어가면 됩니다.

## ⑤ Claude 에 넣을 설정 만들기

```bash
kis config mcp
```

**실전 계좌면** 뒤에 `--environment real` 을 꼭 붙이세요: `kis config mcp --environment real`
(모의 계좌면 그냥 `kis config mcp`.)

그러면 아래처럼 **붙여넣을 설정이 출력**됩니다. 경로도 알아서 채워 주니 손댈 것이 없습니다 --
화면의 **`{` 부터 `}` 까지 복사**하세요(맨 위 `#` 로 시작하는 줄은 설명이라 안 넣어도 됩니다).

```json
{
  "mcpServers": {
    "kis-trader": {
      "command": "/.../kis-mcp",
      "env": { "KIS_MCP_PROFILE": "general", "KIS_MCP_ENVIRONMENT": "paper" }
    }
  }
}
```

## ⑥ Claude Desktop 에 붙여넣기

1. Claude Desktop 에서 **Settings → Developer → `Edit Config`** 를 엽니다.
   (Mac 은 화면 맨 위 `Claude` 메뉴, Windows 는 왼쪽 위 메뉴/설정 아이콘에서 Settings)
2. 열린 파일 내용을 **⑤에서 복사한 것으로 바꾸고 저장**합니다(`⌘ + S` / `Ctrl + S`).
   파일이 바로 안 열리고 폴더만 뜨면 `claude_desktop_config.json` 을 텍스트 편집기(메모장·TextEdit 등)로 여세요.
   (이미 다른 MCP 서버가 적혀 있으면 통째로 바꾸지 말고 `"mcpServers"` 안에 `"kis-trader"` 항목만 추가하세요.)
3. Claude Desktop 을 **완전히 종료**했다가(Mac `⌘ + Q`, Windows 는 트레이 아이콘에서 종료) 다시 켭니다.

## ⑦ 써보기

채팅창에 말로 물어봅니다:

> 삼성전자 현재가 알려줘
>
> 내 KIS 잔고 보여줘

Claude 가 알아서 조회해 답합니다. 끝입니다.

::: {.callout-note}
안 되면 -- 채팅 입력창 왼쪽 아래 `+` → `Connectors` 에 `kis-trader` 가 보이는지 확인하고, 자세한 점검·로그는
[MCP 서버](mcp.md) 아래쪽을 보세요. 계좌 종류가 여럿이거나 실전·모의를 함께 쓰는 등 더 복잡한 경우는
[자격증명 설정](configuration.md) 에 경우별 레시피가 있습니다.
:::

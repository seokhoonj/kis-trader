# Claude Code 스킬

이 저장소에는 Claude Code(에이전트)가 kis_trader 를 **정해진 규율대로** 다루도록 안내하는
스킬이 함께 들어 있습니다: `skills/kis-trader/SKILL.md`. 도구가 아니라 "언제·어떻게" 판단하는
플레이북입니다 — 실제 조회·주문은 에이전트가 파이썬 API(`import kis_trader`)로 합니다.

::: {.callout-warning}
스킬은 **안전을 보장하지 않습니다.** 규율(심볼 해석 먼저, 주문 티켓 확인, 재전송 금지 등)을
따르도록 **안내**할 뿐, 강제하는 샌드박스가 아닙니다 -- 모델이 규율을 벗어날 수 있습니다. 실계좌를
다룰 때는 스킬만 믿지 말고 [주문](orders.md)의 클라이언트측 안전장치와 도구 권한(네트워크 쓰기
제한 등)을 함께 쓰세요.
:::

## 무엇을 안내하나

- 요청을 **조회 / 주문준비 / 주문전송 / 결과확정** 으로 먼저 분류하고, 조회만 확인 없이 진행합니다.
- 이름은 `search` 로 실제 코드를 해석한 뒤에만 주문합니다(코드 추측 금지).
- 주문 전 **티켓을 되읽어 보여주고**, 실전이면 그 자리에서 명시 승인을 받습니다.
- 매수/매도/정정/취소를 **정확히 1회** 호출하고, 타임아웃은 재전송하지 않고 `reconcile` 로만 확정합니다.
- 자격증명·`._raw`·전체 계좌번호를 출력에 흘리지 않습니다.

요컨대 [주문](orders.md)의 안전커널 규율을 에이전트 행동 지침으로 옮긴 것입니다.

## 설치 — Claude Code (마켓플레이스)

이 저장소는 Claude Code 플러그인 마켓플레이스로 등록돼 있습니다. 마켓플레이스를 추가하고
설치합니다.

```text
/plugin marketplace add seokhoonj/kis-trader
/plugin install kis-trader@kis-trader
```

설치하면 스킬이 자동 로드됩니다 -- KIS 계좌 관련 작업("삼성전자 시세 봐줘", "종목 검색해서
주문")을 말하면 설명이 요청과 맞아 활성화됩니다. `/plugin marketplace update kis-trader` 로
최신본을 당겨오고, `/plugin` 으로 관리합니다.

## 설치 — Claude Code (직접 복사)

마켓플레이스 없이 개인 스킬 폴더에 바로 둘 수도 있습니다.

```bash
mkdir -p ~/.claude/skills
cp -r skills/kis-trader ~/.claude/skills/
```

## Codex 에서 쓰기

Codex CLI 에는 플러그인 마켓플레이스가 없습니다. 대신 **`AGENTS.md`** (저장소 루트 또는
`~/.codex/AGENTS.md`)에 항상 로드되는 지침을 둡니다. 같은 규율을 Codex 에 주려면 그 파일에서
이 스킬을 가리키거나 핵심 규칙을 옮겨 적습니다(이 저장소의 `AGENTS.md` 는 로컬 전용이라
공유되지 않으니, 각자 추가합니다).

```text
# AGENTS.md (사용자 각자)
KIS 계좌 작업은 kis_trader 공개 API 로만 하고, skills/kis-trader/SKILL.md 의 규율을 따른다:
심볼은 search 로 해석 후 주문, 실전 주문 전 티켓 확인, 타임아웃은 재전송 대신 reconcile.
```

Codex 버전에 따라 `~/.codex/skills/` 스킬 디렉터리를 읽기도 하지만(에이전트 스킬 표준),
지원 여부가 버전마다 다를 수 있으니 확실한 경로는 `AGENTS.md` 입니다.

::: {.callout-note}
스킬은 저장소에 실려 있지만 `pip install kis-trader` 로는 배포되지 않습니다(파이썬 아티팩트가
아니라 리포 자산입니다). `.claude/` 는 로컬 전용이라 커밋되지 않으므로, 스킬 본체는 `skills/`,
마켓플레이스 매니페스트는 `.claude-plugin/` 아래에 둡니다.
:::

# Claude Code Skill

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

요컨대 [주문](orders.md)의 안전커널 규율을 에이전트 행동 지침으로 옮긴 것입니다. (Codex 에서
쓰려면 → [Codex Skill](codex-skill.md).)

## 설치 (마켓플레이스)

이 저장소는 Claude Code 플러그인 마켓플레이스로 등록돼 있습니다. Claude Code **채팅창(프롬프트)에서**
아래 `/plugin` 명령을 입력해 마켓플레이스를 추가하고 설치합니다(터미널이 아니라 Claude Code 안에서 칩니다).

```claude
/plugin marketplace add seokhoonj/kis-trader
/plugin install kis-trader@kis-trader
```

설치하면 스킬이 자동 로드됩니다 -- KIS 계좌 관련 작업("삼성전자 시세 봐줘", "종목 검색해서
주문")을 말하면 설명이 요청과 맞아 활성화됩니다. `/plugin marketplace update kis-trader` 로
최신본을 당겨오고, `/plugin` 으로 관리합니다.

## 설치 (직접 복사)

마켓플레이스 없이 개인 스킬 폴더에 바로 둘 수도 있습니다.

```bash
mkdir -p ~/.claude/skills
cp -r skills/kis-trader ~/.claude/skills/
```

::: {.callout-note}
스킬은 저장소에 실려 있지만 `pip install kis-trader` 로는 배포되지 않습니다(파이썬 아티팩트가
아니라 리포 자산입니다). `.claude/` 는 로컬 전용이라 커밋되지 않으므로, 스킬 본체는 `skills/`,
마켓플레이스 매니페스트는 `.claude-plugin/` 아래에 둡니다.
:::

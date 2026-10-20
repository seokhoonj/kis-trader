# Codex Skill

같은 `plugins/kis-trader/skills/kis-trader/` 스킬을 Codex CLI 에서도 씁니다. 스킬이 무엇을 안내하는지(규율)는
[Claude Code Skill](claude-skill.md) 과 동일합니다 -- 여기서는 Codex 설치만 다룹니다.

::: {.callout-warning}
스킬은 **안전을 보장하지 않습니다** -- 규율을 안내할 뿐, 강제하는 샌드박스가 아닙니다. 실계좌를
다룰 때는 [주문](orders.md)의 클라이언트측 안전장치와 도구 권한을 함께 쓰세요.
:::

## 설치 (마켓플레이스, 터미널)

아래는 Codex CLI **0.163.0 기준**입니다. 먼저 `codex --version` 과
`codex plugin --help` 로 설치된 버전의 명령을 확인하고,
[공식 플러그인 안내](https://developers.openai.com/plugins/build/plugins)와 함께 사용하세요.

```bash
codex plugin marketplace add seokhoonj/kis-trader
codex plugin add kis-trader@kis-trader
```

설치 후 Codex 세션을 새로 시작하면 스킬이 잡히고, 채팅에서 `@kis-trader` 로 부릅니다.
설치된 CLI의 명령 형식이 위 예제와 다르면 해당 버전의 `--help` 를 따르세요.

## 대안 (AGENTS.md)

마켓플레이스를 안 쓰면 `AGENTS.md`(저장소 루트 또는 `~/.codex/AGENTS.md`)에 항상 로드되는
지침을 둡니다. 이 저장소의 `AGENTS.md` 는 로컬 전용이라 공유되지 않으니 각자 추가합니다.

```text
# AGENTS.md (사용자 각자)
KIS 계좌 작업은 kis_trader 공개 API 로만 하고, plugins/kis-trader/skills/kis-trader/SKILL.md 의 규율을 따른다:
심볼은 search_stocks 로 해석 후 주문, 실전 주문 전 티켓 확인, 타임아웃은 재전송 금지. reconcile 은 저장된 유효한 증권사 주문번호가 없으면 불명으로 유지하므로 HTS/MTS나 계좌 주문·체결 조회로 수동 확인.
```

::: {.callout-note}
Claude Code 마켓플레이스 매니페스트는 리포지토리 최상위 `.claude-plugin/marketplace.json`, Codex
마켓플레이스는 최상위 `.agents/plugins/marketplace.json` 입니다. `.codex-plugin/` 는 플러그인 안
(`plugins/kis-trader/.codex-plugin/plugin.json`)의 플러그인-레벨 매니페스트로, 최상위엔 없습니다.
스킬 본체는 `plugins/kis-trader/skills/kis-trader/` 하나를 공유합니다.
:::

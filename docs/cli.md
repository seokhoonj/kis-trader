# Command Line (kis)

패키지를 설치하면 터미널 명령 `kis` 가 함께 깔립니다. 파이썬을 짜지 않고도 조회·주문을 할 수
있는 얇은 래퍼입니다(내부적으로 같은 공개 API 를 부릅니다).

자격증명과 환경은 **`--profile`** 로 정합니다. 프로필은 환경변수나 설정 파일에서 읽으며,
환경(실전/모의)은 **그 프로필에 저장된 값**입니다(이름이 정하지 않음). 기본 프로필은 `main`(실전
주계좌)이고, 다른 계좌·모의는 저장해 둔 프로필 이름을 `--profile` 로 고릅니다(`KIS_PROFILE` 로 기본
변경). 실전이 기본이어도 주문은 `--execute` 전까지 전송되지 않아 안전합니다.

```bash
kis --profile paper account balance     # 모의 프로필(저장 시 environment=paper)
kis --profile main  account balance     # 실전 주계좌
```

프로필별 변수 접두어·설정 파일 위치(Linux·macOS·Windows 공통)는 [3. 자격증명과 프로필](configuration.md)
을 참고하세요.

## 조회

```bash
kis stock quote 005930                                # 현재가
kis stock quote AAPL --venue overseas                 # 해외(거래소 자동)
kis stock bars 005930 --interval 1d --start 20240101
kis stock book 005930                                 # 호가
kis search 삼성전자 --market KOSPI
kis ranking change --direction gainers                # 상승률 상위
kis account balance                                   # 국내 잔고
kis account positions --venue overseas --market US
```

아무 옵션 없이 쓰면 결과가 **눈으로 보기 좋은 표**로 나옵니다. 이 출력을 다른 프로그램이나
스크립트에서 자동으로 읽어(파싱해) 쓰려면 `--format json` 을 붙여 JSON 으로 받습니다(결과가
여러 행이면 한 줄에 한 건씩 주는 `--format jsonl` 도 있습니다).

```bash
kis stock quote 005930 --format json
```

## 주문 — 기본은 dry-run

주문 명령은 **`--execute` 가 없으면 전송하지 않고** 주문 티켓만 되읽어 보여줍니다.

```bash
kis order buy 005930 10 --limit-price 70000                          # dry-run (전송 안 됨)
kis --profile paper order buy 005930 10 --limit-price 70000 --execute paper   # 모의 전송(확인 후)
```

국내 현금주문은 `--division` 으로 KRX 주문구분을 고릅니다 -- `conditional_limit`(조건부지정가,
`--limit-price` 필요), `immediate_limit`(최유리지정가), `priority_limit`(최우선지정가). 최유리/최우선은
시장이 가격을 정하므로 `--limit-price` 를 주지 않습니다(해외 `--venue overseas` 엔 미지원).

```bash
kis --profile main order buy 005930 10 --division immediate_limit --execute real --yes --confirm-account 7801
```

실제 전송하려면 `--execute` 값이 세션 환경(프로필에 저장된 실전/모의)과 같아야 합니다. 대화형에서는 확인을
받습니다(모의는 y/N, 실전은 계좌 끝 4자리 입력). 스크립트(비대화형)에서는 `--yes` 가
필요하고, 실전은 `--confirm-account` 로 계좌 끝 4자리를 한 번 더 맞춰야 합니다.

```bash
kis --profile main order buy 005930 10 --limit-price 70000 \
    --execute real --yes --confirm-account 7801
```

정정·취소·확인은 `client_order_id` 로 지목합니다. 결과가 불명(타임아웃)이면 **재전송하지 말고**
`reconcile` 로 실제 상태를 확정하세요.

```bash
kis order modify <client_order_id> --limit-price 70500 --execute real --yes --confirm-account 7801
kis order cancel <client_order_id> --execute real --yes --confirm-account 7801
kis order reconcile <client_order_id>
```

::: {.callout-important}
CLI 의 `--yes` 는 **오타 방어**용입니다. 에이전트(Claude 등)가 계좌를 몰 때는 CLI 가
아니라 파이썬 API 를 안전커널 규율대로 쓰는 것이 맞습니다 -- [주문](orders.md)의 안전장치 참고.
:::

## 종료 코드

스크립트에서 결과를 분기할 수 있게 종료 코드를 구분합니다.

| 코드 | 뜻 |
|:--:|----|
| 0 | 성공 |
| 2 | 인자 형식 오류 |
| 3 | 로컬 구성·자격증명 문제 |
| 4 | 도메인 검증·리스크 거부(전송 안 됨) |
| 5 | 전송 실패 |
| 6 | 브로커 거부 |
| 7 | 주문 결과 불확실(`reconcile` 필요) |
| 130 | 사용자 중단(Ctrl-C) |

이 종료 코드는 KIS 가 정한 게 아니라 **이 CLI 가 스크립트용으로 매긴 규약**입니다. `2` 는
argparse 표준(인자 오류), `130` 은 유닉스 관례(Ctrl-C 중단 = 128 + 시그널 2)를 따른 것이고,
`3`~`7` 은 주문 결과를 스크립트가 구분할 수 있게 이 도구가 정했습니다.

# 명령줄 도구 (`kis`)

패키지를 설치하면 터미널 명령 `kis` 가 함께 깔립니다. 파이썬을 짜지 않고도 조회·주문을 할 수
있는 얇은 래퍼입니다(내부적으로 같은 공개 API 를 부릅니다). 자격증명은 **환경변수**로 줍니다.

```bash
export KIS_APP_KEY=... KIS_APP_SECRET=... KIS_ACCOUNT=12345678-01
```

## 조회

```bash
kis stock quote 005930                 # 현재가
kis stock quote AAPL --venue overseas  # 해외(거래소 자동)
kis stock bars 005930 --interval 1d --start 20240101
kis stock book 005930                  # 호가
kis search 삼성전자 --market KOSPI      # 이름으로 검색
kis ranking change --direction gainers # 상승률 상위
kis account balance                    # 국내 잔고
kis account positions --venue overseas --market US
```

기본 출력은 사람용 표입니다. 기계가 읽으려면 `--format json`(또는 `--format jsonl`):

```bash
kis stock quote 005930 --format json
```

## 주문 — 기본은 dry-run

주문 명령은 **`--execute` 가 없으면 전송하지 않고** 주문 티켓만 되읽어 보여줍니다.

```bash
kis order buy 005930 10 --limit-price 70000              # dry-run (전송 안 됨)
kis order buy 005930 10 --limit-price 70000 --execute paper   # 모의 전송(확인 후)
```

실제 전송하려면 `--execute` 값이 세션 환경(`--env`)과 같아야 합니다. 대화형에서는 확인을
받습니다(모의는 y/N, 실전은 계좌 끝 4자리 입력). 스크립트(비대화형)에서는 `--yes` 가
필요하고, 실전은 `--confirm-account` 로 계좌 끝 4자리를 한 번 더 맞춰야 합니다.

```bash
kis --env real order buy 005930 10 --limit-price 70000 \
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
CLI 의 `--yes` 는 **사람의 오타 방어**용입니다. 에이전트(Claude 등)가 계좌를 몰 때는 CLI 가
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
| 130 | 사용자 중단 |

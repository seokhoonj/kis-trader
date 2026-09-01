# Command Line (kis)

패키지를 설치하면 터미널 명령 `kis` 가 함께 깔립니다. 파이썬을 짜지 않고도 조회·주문을 할 수
있는 얇은 래퍼입니다(내부적으로 같은 공개 API 를 부릅니다).

자격증명과 환경은 **`--profile`** 로 정합니다. 프로필은 환경변수나 설정 파일에서 읽으며,
환경(실전/모의)은 **그 프로필에 저장된 값**입니다(이름이 정하지 않음). `--profile` 을 생략하면 기본
프로필을 씁니다(`KIS_DEFAULT_PROFILE` 환경변수 > `credentials.json` 의 `default_profile` 마커 > 첫 항목 > `main`). 다른 계좌·모의는
저장해 둔 프로필 이름을 `--profile` 로 고릅니다. 실전이 기본이어도 주문은 `--execute` 전까지 전송되지
않아 안전합니다.

```bash
kis --profile paper account balance     # 모의 프로필(저장 시 environment=paper)
kis --profile main  account balance     # 실전 주계좌
```

프로필별 변수 접두어·설정 파일 위치(Linux·macOS·Windows 공통)는 [자격증명과 프로필](configuration.md)
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

일별 주문·체결 내역(기간)은 `kis account fills` 로 조회합니다. 기본은 국내주식(`--asset stock`),
장내채권은 `--asset bond`. `--start`/`--end`(YYYYMMDD, 주식은 3개월 이내) 로 기간을 주고,
`--side`(all/buy/sell)·`--symbol`(주식 6자리 / 채권 ISIN)·`--unfilled-only` 로 좁힙니다.
미체결(정정·취소 대상)만 보려면 `kis account orders` 를 씁니다. 국내주식은 실전·모의 모두,
장내채권은 실전 계좌 전용입니다(모의투자 미지원).

```bash
kis account fills --start 20240101 --end 20240131                              # 국내주식 체결내역(기간)
kis account fills --start 20240101 --end 20240131 --symbol 005930 --side buy   # 종목·매수만
kis account fills --asset bond --start 20240101 --end 20240131                 # 장내채권 체결내역
kis account fills --asset bond --start 20240101 --end 20240131 --unfilled-only # 미체결만
```

예약주문(정규장이 열리기 전에 미리 걸어두는 예약) 목록은 `kis account reserved --start/--end` 로
조회합니다(YYYYMMDD). 국내 조회는 `--process` 로 all/processed/unprocessed 를 골라 좁히며 실전 계좌
전용입니다(모의투자 미지원). 해외 예약주문은 `--venue overseas` 로 조회하고(미국+아시아 합산)
`--process` 는 지원하지 않습니다.

```bash
kis account reserved --start 20240101 --end 20240131                         # 국내 예약주문 목록
kis account reserved --start 20240101 --end 20240131 --process unprocessed   # 미처리분만
kis account reserved --venue overseas --start 20240101 --end 20240131        # 해외 예약주문 목록
```

기간별 실현손익은 `kis account profits --start/--end` 로 봅니다. `--symbol` 로 종목을 좁히는 건
국내·해외 공통이고, 국내는 `--by symbol`(종목별 실현손익, 기본)/`--by day`(일별 매매손익)·
`--sort recent|oldest`, 해외주식은 `--venue overseas` 로 기간손익을 보며 `--currency`·
`--won-basis`(원화 기준) 를 씁니다. 실전 계좌 전용입니다(모의투자 미지원).

```bash
kis account profits --start 20240101 --end 20240131                          # 국내 종목별 실현손익
kis account profits --start 20240101 --end 20240131 --by day                 # 국내 일별 매매손익
kis account profits --venue overseas --start 20240101 --end 20240131         # 해외주식 기간손익
```

거래·입출금내역(매매·결제·수수료)은 `kis account transactions --start/--end` 로 조회합니다.
해외주식은 `--venue overseas`(`--symbol`·`--side all|buy|sell` 로 좁힘), 해외선물옵션은 08 프로필로
실행하면 같은 명령이 해외파생 입출금내역을 조회합니다. 실전 계좌 전용입니다.

```bash
kis account transactions --venue overseas --start 20240101 --end 20240131    # 해외 거래·입출금내역
```

### 선물옵션 계좌 조회

`kis account` 명령은 **프로필이 연 계좌 종류**(주식 / 국내선물옵션 / 해외선물옵션)에 맞게
동작합니다 -- 선물옵션 프로필로 실행하면 같은 명령이 선물옵션 계좌를 조회합니다(계좌 종류는
프로필의 상품코드로 정해지며, 각 분기가 안 쓰는 플래그를 주면 거부합니다).

- `balance` -- 국내파생은 잔고(보유+예수금·증거금·손익 요약), 해외파생은 예수금현황.
- `positions` -- 해외파생 미결제내역(국내파생은 balance 에 포함).
- `orders` -- 국내파생 미체결(`--date` 로 주문일자), 해외파생은 당일 주문(기간 `--start/--end` 주면 일별).
- `fills` -- 국내파생 기준일체결내역(`--date` 필수), 해외파생 일별체결(`--start/--end`).
- `profits` -- 해외파생 기간손익(`--start/--end`).
- `transactions` -- 해외파생 기간 입출금내역(`--start/--end`).

계좌 종류별 추가 조회:

- `deposit` -- 예수금현황(국내·해외 선물옵션; 해외파생은 `--currency`/`--date`).
- `margin` -- 증거금상세(국내파생 야간증거금 / 해외파생 `--currency`/`--date`).
- `valuation` -- 국내파생 평가손익내역.
- `settlement` -- 국내파생 정산손익(`--date`) / 해외주식 정산잔고(`--venue overseas --date`).
- `commissions` -- 국내파생 기간약정수수료(`--start/--end`).
- `present` -- 해외주식 체결기준현재잔고.
- `foreign-margin` -- 해외주식 외화증거금.

모두 조회 전용입니다(대부분 실전 계좌 전용 -- 모의 지원 여부는 라이브러리가 소유).

```bash
kis --profile futures account balance                       # 국내선물옵션 잔고(03 프로필)
kis --profile futures account fills --date 20240102         # 국내파생 기준일 체결내역
kis --profile ovs-futures account profits --start 20240101 --end 20240131  # 해외파생 기간손익(08)
kis --profile ovs-futures account margin --currency USD     # 해외파생 증거금상세
```

## 주문 — 기본은 dry-run

주문 명령은 **`--execute` 가 없으면 전송하지 않고** 주문 티켓만 되읽어 보여줍니다.

```bash
kis order buy 005930 10 --limit-price 70000                          # dry-run (전송 안 됨)
kis --profile paper order buy 005930 10 --limit-price 70000 --execute paper   # 모의 전송(확인 후)
```

국내 현금주문은 `--division` 으로 KRX 주문구분을 고릅니다 -- `conditional_limit`(조건부지정가,
`--limit-price` 필요), `immediate_limit`(최유리지정가), `priority_limit`(최우선지정가),
`midpoint`(중간가; 수량만, 호가 중간값으로 시장이 가격 결정, 전 보드, IOC/FOK 가능),
`pre_market_close`(장전 시간외 종가; KRX 전용), `post_market_close`(장후 시간외 종가; KRX 전용),
`after_hours_single`(시간외 단일가; `--limit-price` 필요, KRX 전용). 최유리/최우선·중간가·시간외 종가는
시장이 가격을 정하므로 `--limit-price` 를 주지 않습니다(해외 `--venue overseas` 엔 미지원).

```bash
kis --profile main order buy 005930 10 --division immediate_limit --execute real --yes --confirm-account 7801
```

국내 주식 스톱지정가는 `--stop-price` 로 트리거(조건가격)를 줍니다. 시장이 그 값에 닿으면 그때
`--limit-price` 가격으로 지정가가 접수됩니다. `--stop-price` 는 `--limit-price` 가 반드시 필요하고
(스톱시장가는 없습니다), `--division` 과 함께 쓸 수 없습니다. KRX 전용이라 정규장(09:00~15:30)에만
나갑니다 -- 장 시간 밖의 접수는 서버가 거부합니다.

```bash
kis order buy 005930 10 --limit-price 70000 --stop-price 69000        # dry-run (전송 안 됨)
```

예약주문(정규장이 열리기 전에 미리 걸어두는 예약)은 `kis order buy`/`kis order sell` 에 `--reserve`
를 주어 냅니다 -- 국내 주식은 다음 영업일 동시호가에 집행되며 실전 전용이고 `--end-date YYYYMMDD`
로 예약 유효 종료일을 지정합니다. 취소는
`kis order cancel-reserved <순번>` 이며, 순번은 예약 발주 결과나 `kis account reserved` 목록의
sequence 입니다. 정정은 `kis order modify-reserved <순번>` 이며,
브로커 규격상 `--symbol`/`--side`/`--quantity` 로 종목·방향·수량을 **전체 재지정**합니다 --
`--limit-price` 를 생략하면 기존 단가 유지가 아니라 시장가로 바뀌니 주의하세요(`--end-date`/
`--order-date` 도 지정 가능). 정정 후 예약 순번이 재배정될 수 있어(응답은 새 순번을 주지 않음)
이어서 정정·취소하려면 `kis account reserved` 로 순번을 재확인하세요 -- 영수증에도 같은 안내가
실립니다. 다른 주문과 같은 dry-run/`--execute` 안전장치를 씁니다.

해외 예약주문은 `--venue overseas` 로 냅니다 -- 국내와 달리 **지정가 전용**(`--limit-price` 필수),
`--end-date` 미지원, **모의(paper) 허용**입니다. `--currency`(HKD/CNY/USD)는 **홍콩 예약 전용**이고
미지정 시 홍콩은 HKD 입니다. 목록은 `kis account reserved --venue overseas`(미국+아시아 합산),
취소는 `kis order cancel-reserved --venue overseas --receipt-date YYYYMMDD` 로 **미국 예약만** 됩니다
-- 아시아(일/중/홍/베) 예약 취소는 전용 엔드포인트가 없어 이 경로가 아니라 발주 리포트의
client_order_id 로 `kis order cancel` 이 취소합니다. 정정(modify)은 해외 예약에 없습니다.

```bash
kis order buy 005930 10 --limit-price 70000 --reserve --end-date 20240131    # 국내 예약매수(dry-run)
kis order cancel-reserved SEQ7 --order-date 20240131                         # 국내 예약 취소(dry-run)
kis order modify-reserved SEQ7 --symbol 005930 --side buy --quantity 10 --limit-price 71000  # 국내 예약 정정(dry-run)
kis order buy 00700 100 --venue overseas --reserve --limit-price 350 --exchange HKS --currency HKD  # 해외(홍콩) 예약매수(dry-run)
kis account reserved --venue overseas --start 20240101 --end 20240131       # 해외 예약주문 목록
kis order cancel-reserved US123 --venue overseas --receipt-date 20240131    # 해외(미국) 예약 취소(dry-run)
```

국내 주식 **TWAP 분할**은 `kis order twap` 으로 냅니다 -- 총 수량을 `--over`(총 소요시간, 예 `30m`/
`1h`/`1h30m`) 동안 `--slices`(분할 횟수)로 나눠 **시장가로 여러 번** 발주합니다. `--start HHMMSS`(KST,
생략 시 지금부터, 과거는 거부)로 시작 시각을 정하며, 모든 슬라이스가 KRX 정규장(09:00~15:30 KST) 안이어야
합니다. 다른 주문과 달리 실행 동안 **호출을 블로킹**하고, 기본은 스케줄만 보여주는 dry-run, `--execute`
로 실제 집행합니다(슬라이스 거부/타임아웃은 기록하고 계속, 부분 실행+미달 보고).

```bash
kis order twap 005930 --side buy --quantity 100 --over 30m --slices 3                 # dry-run: 스케줄 미리보기
kis order twap 005930 --side buy --quantity 100 --over 30m --slices 3 \
  --start 130000 --execute paper --yes                                               # 13:00 시작, 실제 집행
```

미국주식 **algo 분할주문**(서버가 쪼개 집행)은 해외 매수/매도에 `--algo twap`/`vwap` 를 줍니다 --
**미국·실전 전용·최소 10주**입니다. `--algo-start`/`--algo-end`(KST HHMMSS, 같은 날·시작<종료, 자정 넘김
불가)로 시간창을 주거나 생략하면 정규장 종료까지 집행합니다(미국 정규장은 KST 로 자정을 넘어 한 창이 세션
전체를 덮지 못하니 전체 집행은 시간창 생략). 자세한 규칙은 [주문](orders.md) 참조.

```bash
kis order buy AAPL 10 --venue overseas --limit-price 150 --algo twap --execute real --yes       # 전체 세션(시간창 생략)
kis order buy AAPL 10 --venue overseas --limit-price 150 \
  --algo twap --algo-start 223000 --algo-end 235959 --execute real --yes                         # KST 같은 날 구간
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

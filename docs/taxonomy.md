# API 함수 트리

공개 파사드 전체를 한 화면에 담은 함수 지도입니다. 각 함수의 상세는 해당 주제 페이지를 보세요. 핸들이 반환하는 결과 타입(`Quote`, `Balance` 등)은 트리에 펼치지 않습니다.

## 세션 — `KISClient`

```text
kis  (KISClient 세션)
├── account                            # 계좌 조회 (상품코드로 분기) → 아래 계좌 트리
├── instrument(symbol, exchange=None)  # 해외 심볼 마스터 조회
├── realtime(...)                      # 실시간 웹소켓 → 아래 실시간 트리
├── revoke_token()                     # 접근 토큰 폐기
├── transport                          # 저수준 전송 계층
├── environment                        # 실전/모의 환경
└── hts_id                             # HTS 로그인 아이디
```

## `kis.domestic` — 국내 시세·발주·시장·검색

```text
kis.domestic
├── stock(code)                         # 국내 종목/ETF 핸들
│   │  ── 현재가·종목정보 ───────────────────────────
│   ├── quote()                         # 현재가 스냅샷
│   ├── profile()                       # 종목 기본정보
│   ├── status()                        # 현재가·거래·규제 상태
│   │  ── 봉·시세 이력 ────────────────────────────
│   ├── bars(interval='1d', ...)        # OHLCV 봉
│   ├── recent_prices()                 # 최근 30개 주가·수급
│   ├── minute_bars_on(day)             # 특정 과거일 1분봉
│   ├── expected_price_trend()          # 동시호가 예상체결가 추이
│   │  ── 호가·체결 ──────────────────────────────
│   ├── order_book()                    # 호가창(10단계)
│   ├── trades()                        # 최근 체결(time & sales)
│   ├── intraday_executions()           # 당일 체결·최우선호가
│   ├── daily_trade_volume()            # 일별 매수/매도 체결량
│   ├── trade_amount_bands()            # 체결금액대별 매매비중
│   ├── volume_profile()                # 가격대별 거래량(매물대)
│   │  ── 투자자 매매동향 ───────────────────────────
│   ├── investor_flows()                # 일자별 투자자 매매동향
│   ├── detailed_investor_history()     # 세부 투자자 일별 내역
│   ├── investor_estimate()             # 장중 투자자 순매수 추정
│   ├── foreign_net_buy_trend()         # 장중 외국계 순매수 추이
│   │  ── 회원사(증권사 창구) ────────────────────────
│   ├── broker_activity()               # 상위 회원사 매매 비중
│   ├── broker_daily_activity(...)      # 회원사 일별 매매
│   ├── broker_trade_ticks(...)         # 회원사 실시간 체결 틱
│   │  ── 시간외 거래 ─────────────────────────────
│   ├── after_hours_quote()             # 시간외 단일가 스냅샷
│   ├── after_hours_conclusions()       # 시간외 시간별 체결
│   ├── after_hours_daily()             # 시간외 일자별 종가
│   ├── after_hours_order_book()        # 시간외 10단계 호가
│   │  ── 프로그램매매 ─────────────────────────────
│   ├── program_trades()                # 장중 프로그램매매 흐름
│   ├── daily_program_trades()          # 프로그램매매 일별 추이
│   │  ── 신용·공매도·대차 ──────────────────────────
│   ├── credit_balance_trend()          # 일별 신용잔고 추이
│   ├── short_sale_trend()              # 일별 공매도 추이
│   ├── loan_trend()                    # 일별 대차거래 추이
│   │  ── 재무제표 ───────────────────────────────
│   ├── balance_sheet()                 # 결산기별 대차대조표
│   ├── income_statement()              # 결산기별 손익계산서
│   │  ── 재무비율 ───────────────────────────────
│   ├── financial_ratios()              # 주요 재무비율
│   ├── profitability_ratios()          # 수익성비율
│   ├── stability_ratios()              # 안정성비율
│   ├── growth_ratios()                 # 성장성비율
│   ├── other_ratios()                  # 기타주요비율
│   │  ── 애널리스트·추정 ───────────────────────────
│   ├── analyst_opinions()              # 애널리스트 의견·목표주가
│   ├── earnings_estimate()             # 월간 추정 손익·투자지표
│   │  ── ETF/ETN 전용 ─────────────────────────
│   ├── nav()                           # ETF/ETN 순자산가치
│   ├── nav_comparison()                # ETF 시장가-NAV 비교
│   ├── nav_intraday()                  # ETF 시장가-NAV 분별
│   ├── nav_history(start, end)         # 일별 NAV-가격 추이
│   ├── etf_order_book()                # ETF 10단계 호가·LP
│   ├── etf_components()                # ETF 구성종목(PDF, Portfolio Deposit File)
│   │  ── 주문가능 조회(여력) ────────────────────────
│   ├── buyable()                       # 현금 매수가능 여력
│   ├── credit_buyable()                # 신용 매수가능 여력
│   ├── sellable()                      # 매도가능 수량
│   │  ── 주문 실행 ──────────────────────────────
│   ├── buy(quantity, ...)              # 매수 주문(KRX 주문구분)
│   ├── sell(quantity, ...)             # 매도 주문(KRX 주문구분)
│   ├── credit_buy(...)                 # 신용 매수 주문
│   ├── credit_sell(...)                # 신용 매도 주문
│   ├── reserve_buy(...)                # 예약매수
│   ├── reserve_sell(...)               # 예약매도
│   └── twap(side, quantity, over, slices, ...)  # 클라이언트 TWAP 분할(정규장 내 시장가 여러 번)
├── index(code)                         # 지수/업종 핸들
│   ├── quote()                         # 지수 현재가
│   ├── bars(interval='1d', ...)        # 지수 봉
│   ├── intraday(interval='1m')         # 당일 시간대별 시계열
│   ├── ticks()                         # 당일 10초 시계열
│   ├── daily_history()                 # 일·주·월 지수 통계
│   ├── expected_trend()                # 동시호가 예상 지수 추이
│   ├── expected_snapshot()             # 동시호가 예상 지수 스냅샷
│   └── categories()                    # 하위 업종 지수 목록
├── bond(code)                          # 장내채권 핸들
│   ├── profile()                       # 채권 기본/발행 정보
│   ├── issuance()                      # 발행 조건·신용등급
│   ├── quote()                         # 채권 현재가
│   ├── bars(interval='1d')             # 채권 일별 OHLCV
│   ├── daily_prices()                  # 날짜별 현재가·OHLCV
│   ├── valuations(start, end)          # 평가기관별 단가·수익률
│   ├── order_book()                    # 채권 호가창(5단계)
│   ├── trades()                        # 채권 최근 체결
│   ├── buy(quantity, limit_price)      # 채권 매수 주문
│   └── sell(quantity, ...)             # 채권 lot 매도 주문
├── elw(code)                           # ELW 고유 지표 핸들
│   ├── quote()                         # ELW 현재가·그릭스
│   ├── sensitivity_trend()             # 민감도(그릭스) 추이
│   ├── volatility_trend()              # 내재변동성 추이
│   ├── indicator_trend()               # 투자지표(레버리지) 추이
│   └── lp_flows()                      # 일별 LP 매매 흐름
├── futures(code)                       # 지수선물 계약 핸들
│   ├── quote()                         # 계약 현재가
│   ├── order_book()                    # 계약 호가창(5단계)
│   ├── expected_execution_trend()      # 예상체결 요약·추이
│   ├── bars(interval='1d', ...)        # OHLCV 봉
│   ├── underlying_quote()              # 선물+기초자산(베이시스)
│   ├── orderable(side)                 # 주간 주문가능수량
│   ├── night_orderable(side)           # 야간장 주문가능수량
│   ├── buy(quantity, ...)              # 계약 매수 주문
│   └── sell(quantity, ...)             # 계약 매도 주문
├── option(code, right=None)            # 지수옵션 계약 핸들
│   ├── quote()                         # 계약 현재가
│   ├── order_book()                    # 계약 호가창(5단계)
│   ├── expected_execution_trend()      # 예상체결 요약·추이
│   ├── bars(interval='1d', ...)        # OHLCV 봉
│   ├── orderable(side)                 # 주간 주문가능수량
│   ├── night_orderable(side)           # 야간장 주문가능수량
│   ├── buy(quantity, ...)              # 계약 매수 주문
│   └── sell(quantity, ...)             # 계약 매도 주문
│  ── 파생 전광판 ─────────────────────────────────
├── option_expiries()                   # 지수옵션 만기 월물 목록
├── option_board(expiry)                # 옵션 콜/풋 전광판
├── option_board_futures()              # 전광판 하단 선물 현재가
├── derivative_margin_rates(base_date)  # 선물 증거금율 표
│  ── 종목검색·관심·공통 조회 ──────────────────────────
├── search(query)                       # 상장 종목 이름/코드 검색
├── quotes(symbols)                     # 여러 종목 현재가(최대 30)
├── product_info(symbol)                # 상품 공통 기본정보
├── saved_screens()                     # HTS 저장 종목검색 조건
├── saved_screen_stocks(sequence)       # 저장 조건 일치 종목 시세
├── watchlist_groups()                  # 관심종목 그룹 목록
├── watchlist(group_code)               # 관심종목 그룹 요약·종목
├── ranking                             # 시장 전체 순위 질의
│   │  ── 가격·등락 ──────────────────────────────
│   ├── by_change()                     # 등락률 순위
│   ├── by_near_high_low()              # 신고/신저 근접 순위
│   ├── by_disparity()                  # 이격도 순위
│   ├── by_preferred_disparity()        # 우선주 괴리율 순위
│   │  ── 거래·체결 ──────────────────────────────
│   ├── by_volume()                     # 거래량 계열 순위
│   ├── by_volume_power()               # 체결강도 순위
│   ├── by_bulk_trades()                # 대량체결건수 순위
│   ├── by_quote_balance()              # 호가잔량 순위
│   │  ── 규모·밸류·재무 ───────────────────────────
│   ├── by_market_cap()                 # 시가총액 순위
│   ├── by_valuation(...)               # 밸류에이션 순위
│   ├── by_finance_ratio(...)           # 재무비율 순위
│   ├── by_profit_asset(...)            # 수익자산지표 순위
│   ├── by_dividend(...)                # 배당률 순위
│   │  ── 수급(공매도·신용·관심) ──────────────────────
│   ├── by_short_sale()                 # 공매도 순위
│   ├── by_credit_balance()             # 신용잔고 순위
│   ├── by_company_trades(...)          # 당사매매종목 순위
│   ├── by_interest()                   # 관심종목 등록상위
│   ├── by_views()                      # HTS 조회 상위 종목
│   │  ── 예상체결·시간외 ───────────────────────────
│   ├── by_expected_execution_change()  # 장전 예상체결 등락
│   ├── by_expected_close()             # 장마감 예상체결 종목
│   ├── by_after_hours_change()         # 시간외 등락률 순위
│   ├── by_after_hours_volume()         # 시간외 거래량 순위
│   ├── by_after_hours_expected_change()# 시간외 예상체결 등락
│   └── by_after_hours_balance()        # 시간외 잔량 순위
├── market                              # 시장 전체 분석 질의
│   │  ── 투자자·프로그램 수급 ────────────────────────
│   ├── investor_flows()                # 투자자 순매수 히스토리
│   ├── investor_snapshot(...)          # 시장·업종 투자자 총량
│   ├── investor_net_buy_stocks()       # 투자자 순매수 상위 종목
│   ├── program_investor_trades()       # 프로그램매매 투자자 집계
│   ├── program_trades()                # 일별 프로그램매매 종합
│   ├── program_flow()                  # 시간대별 프로그램 순매수
│   ├── foreign_broker_trades()         # 외국계 창구 매매종목
│   │  ── 자금·금리 ──────────────────────────────
│   ├── funds()                         # 증시자금 종합 추이
│   ├── interest_rates()                # 주요 금리·채권지수
│   │  ── 종목 자격·이벤트 ──────────────────────────
│   ├── lendable_stocks()               # 대주 가능 종목·한도
│   ├── credit_eligible_stocks()        # 신용주문 가능 종목
│   ├── vi_events()                     # 전 시장 VI 발동
│   ├── limit_stocks()                  # 상/하한가 도달 종목
│   │  ── 의견·뉴스·일정 ───────────────────────────
│   ├── broker_opinions(broker)         # 증권사 종목 투자의견
│   ├── news()                          # 시황/공시 뉴스 피드
│   ├── trading_calendar()              # 거래 캘린더(영업/개장)
│   └── futures_market_schedule()       # 국내선물 영업일·장 시각
├── calendar                            # 기업행위 캘린더 질의
│   ├── dividends(start, end)           # 배당 일정
│   ├── ipo_subscriptions(...)          # 공모주 청약 일정
│   ├── rights_offerings(...)           # 유상증자 일정
│   ├── bonus_issues(...)               # 무상증자 일정
│   ├── capital_reductions(...)         # 자본감소 일정
│   ├── merger_splits(...)              # 합병분할 일정
│   ├── shareholder_meetings(...)       # 주주총회 일정
│   ├── mandatory_deposits(...)         # 의무예치 내역
│   ├── listings(...)                   # 상장정보
│   ├── par_value_changes(...)          # 액면교체 일정
│   ├── forfeited_shares(...)           # 실권주 일정
│   └── appraisal_rights(...)           # 주식매수청구 일정
├── elw_ranking                         # ELW 순위 질의
│   ├── by_volume()                     # 거래량 순위
│   ├── by_change()                     # 등락률 순위
│   ├── by_sensitivity()                # 민감도(그릭스) 순위
│   ├── by_indicator()                  # 투자지표 순위
│   └── by_quick_change()               # 당일 급변 종목
└── elw_screener                        # ELW 스크리닝 질의
    ├── underlyings()                   # 상장 기초자산 목록
    ├── by_underlying(underlying)       # 한 기초자산 ELW 목록
    ├── comparables(underlying)         # 비교대상 ELW 목록
    ├── newly_listed(date)              # 신규상장 ELW 목록
    ├── expiring(start, end)            # 만기예정 ELW 목록
    └── search()                        # 조건검색 ELW 목록
```

## `kis.overseas` — 해외 시세·발주·순위

```text
kis.overseas
├── stock(symbol, exchange=None)        # 해외 종목 핸들
│   ├── quote()                         # 현재가 스냅샷
│   ├── current_price()                 # 현재체결가·누적 거래량
│   ├── bars(interval='1d', ...)        # OHLCV 봉
│   ├── order_book()                    # 호가창
│   ├── trades()                        # 최근 체결 목록
│   ├── buy(quantity, ...)              # 매수
│   ├── sell(quantity, ...)             # 매도
│   ├── overnight_buy(...)              # 미국 오버나이트 매수
│   ├── overnight_sell(...)             # 미국 오버나이트 매도
│   ├── reserve_buy(...)                # 예약매수
│   └── reserve_sell(...)               # 예약매도
├── index(symbol, kind='index')         # 지수/환율/국채/금선물 핸들
│   └── bars(interval='1d', ...)        # 지수류 기간봉
├── futures(series_code)                # 해외 선물 계약 핸들
│   ├── quote()                         # 계약 현재가
│   ├── order_book()                    # 계약 호가창(5단계)
│   ├── bars(exchange, ...)             # 분·일·주·월 OHLCV
│   ├── trades(exchange, ...)           # 최근 틱 체결
│   ├── detail()                        # 계약 명세
│   ├── buy(quantity, ...)              # 계약 매수
│   └── sell(quantity, ...)             # 계약 매도
├── option(series_code)                 # 해외 옵션 핸들(선물과 동일 메서드)
├── futures_details(symbols)            # 선물 명세 배치(최대 32)
├── option_details(symbols)             # 옵션 명세 배치(최대 30)
├── derivatives_market_hours(...)       # 상품군별 장운영시간
├── futures_open_interest(product)      # CFTC 미결제약정
├── settlement_dates()                  # 각 시장 결제일자 참조표
├── quotes(symbols)                     # 여러 종목 현재가(최대 10)
├── search_stocks(exchange, ...)        # 조건검색(exchange='US'는 NAS/NYS/AMS 통합)
├── product_info(exchange, symbol)      # 상품기본정보
├── industries(exchange)                # 업종(섹터) 코드 목록
├── industry_stocks(exchange, code)     # 업종 소속 종목 시세
├── collateral_stocks(...)              # 담보대출 가능종목
├── news(...)                           # 해외뉴스 제목 피드
├── breaking_news(...)                  # 해외속보 제목 피드
├── rights(start, end, ...)             # 기간별 권리(배당·증자)
├── corporate_actions(country, symbol)  # 기업행사 종합 일정
└── ranking                             # 시장 순위 질의
    ├── by_volume(exchange)             # 거래량 순위
    ├── by_amount(exchange)             # 거래대금 순위
    ├── by_trade_growth(exchange)       # 거래증가율 순위
    ├── by_market_cap(exchange)         # 시가총액 순위
    ├── by_change(exchange, ...)        # 등락률 순위
    ├── by_volume_surge(exchange)       # 거래량 급증 순위
    ├── by_buy_strength(exchange)       # 매수 체결강도 순위
    ├── by_turnover(exchange)           # 거래 회전율 순위
    ├── by_price_fluctuation(exchange)  # 가격 급등/급락 순위
    └── by_new_highlow(exchange, ...)   # 신고가/신저가 순위
```

## `kis.account` — 계좌 조회 (상품코드로 분기)

```text
kis.account
├── [StockAccount]  위탁 01 · 연금저축 22 · IRP 29
│   ├── kind                                  # 판별자 "stock" (isinstance 대신 match)
│   ├── product_code                          # raw 상품코드(01/22/29) -- IRP/일반 분기
│   ├── domestic                              # 국내주식 계좌 조회
│   │   ├── balance()                         # 계좌 현금·자산 요약
│   │   ├── positions()                       # 보유 종목(0수량 포함)
│   │   ├── portfolio()                       # 보유+요약 스냅샷
│   │   ├── assets()                          # 투자계좌 자산현황
│   │   ├── realized_profit_balance()         # 실현손익 체결기준잔고
│   │   ├── integrated_margin()               # 통합증거금
│   │   ├── trade_profits(start, end)         # 종목별 실현손익
│   │   ├── daily_profits(start, end)         # 일별 매매손익
│   │   ├── rights(start, end)                # 계좌 권리 내역
│   │   ├── open_orders()                     # 미체결 주문
│   │   ├── reserved_orders(start, end)       # 예약주문 목록
│   │   ├── cancel_reserved_order(seq)        # 예약주문 취소
│   │   ├── modify_reserved_order(...)        # 예약주문 정정
│   │   └── bonds                             # 장내채권 계좌 조회
│   │       ├── balance()                     # 채권 보유 잔고
│   │       ├── buyable(code)                 # 채권 매수가능
│   │       ├── open_orders(order_date)       # 채권 미체결 주문
│   │       └── fills(start, end)             # 채권 주문·체결 내역
│   ├── overseas                              # 해외주식 계좌 조회
│   │   ├── positions()                       # 보유 종목(시장·통화별)
│   │   ├── balance(market)                   # 계좌 손익 요약(시장별)
│   │   ├── buyable(symbol, exchange, price)  # 매수가능금액
│   │   ├── foreign_margin()                  # 통화별 외화 예수금·증거금
│   │   ├── present_balance()                 # 체결기준현재잔고
│   │   ├── settlement_balance(basis_date)    # 결제기준잔고
│   │   ├── period_profit(start, end)         # 기간손익
│   │   ├── transactions(start, end)          # 일별 거래내역
│   │   ├── open_orders()                     # 미체결 주문
│   │   ├── algo_orders()                     # 알고주문 목록
│   │   ├── algo_executions(...)              # 알고주문 체결내역
│   │   ├── reserved_orders(start, end)       # 예약주문 목록
│   │   └── cancel_reserved_order(...)        # 미국 예약주문 취소
│   ├── pension                               # IRP(29) 퇴직연금 조회 렌즈(조회전용)
│   │   ├── deposit()                         # 예수금 요약
│   │   ├── buyable(symbol)                   # 매수가능 여력
│   │   ├── balance()                         # 잔고
│   │   ├── present_balance()                 # 체결기준잔고
│   │   └── orders()                          # 당일 주문 내역
│   └── balance()                             # 국내+채권+해외 통합잔고
├── [DomesticDerivativesAccount]  국내선물옵션 03
│   ├── kind                                  # 판별자 "domestic_derivatives" (isinstance 대신 match)
│   ├── product_code                          # raw 상품코드(03)
│   ├── balance()                             # 선물옵션 잔고(모의 지원)
│   ├── open_orders()                         # 미체결 주문(모의 지원)
│   ├── deposit()                             # 총자산현황
│   ├── valuation_pl()                        # 잔고평가손익
│   ├── settlement_pl(base_date)              # 잔고정산손익
│   ├── base_date_fills(order_date)           # 기준일체결내역
│   ├── commissions(start, end)               # 기간약정수수료
│   ├── night_balance()                       # 야간 잔고현황
│   └── night_margin()                        # 야간 증거금상세
└── [OverseasDerivativesAccount]  해외선물옵션 08
    ├── kind                                  # 판별자 "overseas_derivatives" (isinstance 대신 match)
    ├── product_code                          # raw 상품코드(08)
    ├── deposit()                             # 예수금현황
    ├── margin_detail()                       # 증거금상세
    ├── positions()                           # 미결제내역
    ├── orderable(symbol, side)               # 주문가능수량
    ├── today_orders()                        # 당일 주문내역
    ├── daily_fills(start, end)               # 일별 체결내역
    ├── daily_orders(start, end)              # 일별 주문내역
    ├── period_pnl(start, end)                # 기간 손익
    └── transactions(start, end)              # 기간 입출금내역
```

## `kis.orders` — 주문 라이프사이클

```text
kis.orders
├── open()                        # 미체결 주문 통합 조회(계좌별 -> OpenOrders)
├── reconcile(client_order_id)    # 미확정 주문 상태 재조회
├── cancel(client_order_id, ...)  # 접수주문 취소(부분가능)
└── modify(client_order_id, ...)  # 가격/수량 정정
```

## `kis.realtime()` — 실시간 웹소켓(타입 지정 구독)

```text
kis.realtime()
├── start() / stop()                         # 백그라운드 수신 시작/중단
├── subscribe / unsubscribe / stream         # 원시 등록·해제·통합 이터레이터(탈출구)
├── domestic                                 # 국내 실시간
│   ├── stock(code)                          # → StockHandle
│   │   ├── trades(venue='KRX')              # 체결 → StockTick
│   │   └── order_book(venue='KRX')          # 호가 → StockOrderBook
│   ├── futures(code, kind='index')          # kind: index|commodity|stock|night
│   │   ├── trades()                         # 체결 → FuturesTick
│   │   └── order_book()                     # 호가 → DerivativeOrderBook
│   ├── option(code, kind='index')           # kind: index|stock|night
│   │   ├── trades()                         # 체결 → OptionTick
│   │   └── order_book()                     # 호가 → DerivativeOrderBook
│   ├── index(code)                          # → IndexHandle
│   │   ├── trades()                         # 체결 → IndexTick
│   │   ├── expected_conclusion()            # 예상체결 → IndexExpectedConclusion
│   │   └── program_trade()                  # 프로그램매매 → IndexProgramTrade
│   ├── elw(code)                            # → ELWHandle
│   │   ├── trades()                         # 체결 → ELWTick
│   │   ├── order_book()                     # 호가 → ELWOrderBook
│   │   └── expected_conclusion()            # 예상체결 → ELWExpectedConclusion
│   ├── bond(code)                           # → BondHandle
│   │   ├── trades()                         # 체결 → BondTick
│   │   └── order_book()                     # 호가 → BondOrderBook
│   ├── bond_index(code)                     # → BondIndexHandle
│   │   └── trades()                         # 체결 → BondIndexTick
│   └── execution_notices                    # 체결통보(hts_id 단위)
│       ├── stock(hts_id)                    # → StockExecutionNotice
│       └── derivative(hts_id, session=...)  # → DerivativeExecutionNotice
└── overseas                                 # 해외 실시간
    ├── stock(symbol, exchange=None)         # RSYM 은 종목마스터로 해석
    │   ├── trades()                         # 지연체결 → DelayedTradeTick
    │   └── order_book(venue='global')       # 호가(global 10 / asia 1)
    ├── futures(series_code)                 # → OverseasFuturesHandle
    │   ├── trades()                         # 체결 → FuturesTradeTick
    │   └── order_book()                     # 호가 → FuturesOrderBook
    ├── option(series_code)                  # 선물과 동일 TR·핸들 공유
    └── execution_notices                    # 체결/주문 통보(hts_id 단위)
        ├── stock(hts_id)                    # → OverseasExecutionNotice
        ├── derivative_orders(hts_id)        # → FuturesOrderNotice
        └── derivative_fills(hts_id)         # → FuturesExecutionNotice
```

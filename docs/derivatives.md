# 선물·옵션

국내 지수선물·옵션과 해외 파생. 계약 핸들은 코드로 만듭니다.

## 국내

```python
f = kis.domestic.futures("101W09")  # 지수선물 계약 핸들

f.quote()                           # 현재가
f.underlying_quote()                # 선물 + 기초지수 나란히 (베이시스 판단)
f.expected_execution_trend()        # 예상체결 요약·추이
f.order_book()                      # 호가

o = kis.domestic.option("201W09")   # 지수옵션 계약 핸들
o.quote()
```

전광판·만기:

```python
kis.domestic.option_expiries()       # 상장된 옵션 만기월 목록
kis.domestic.option_board("202409")  # 한 만기의 콜/풋 전광판 (행사가별 시세·그릭스)
kis.domestic.option_board_futures()  # 전광판 하단 선물 계약별 시세
```

## 해외

```python
fu = kis.overseas.futures("ESZ25")                          # 해외 선물 (E-mini S&P 2025.12 = 시리즈코드)

fu.quote()                                                  # 현재가
fu.detail()                                                 # 계약 명세

kis.overseas.futures_details(["ESZ25", "NQZ25"])            # 여러 계약 명세 (최대 32)
kis.overseas.futures_open_interest("ES", as_of="20240628")  # CFTC 미결제약정
kis.overseas.derivatives_market_hours()                     # 상품군별 장운영시간
```

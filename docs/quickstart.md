# 시작하기

## 설치

```bash
uv pip install -e .  # 아직 PyPI 미배포 (0.0.0)
```

## 세션 만들기

KIS 개발자센터에서 발급한 **앱키·앱시크릿**과 **계좌번호**가 필요하다.

```python
from kis_openapi import KISClient

kis = KISClient(
    app_key="YOUR_APP_KEY",
    app_secret="YOUR_APP_SECRET",
    account="12345678-01",  # 계좌번호 8자리-2자리
    environment="real",  # "real" 실전 / "demo" 모의투자
)
```

모의투자로 연습하려면 `environment="demo"` — TR이 자동으로 모의용으로 바뀐다.

## 자격증명 숨기기

코드에 키를 직접 쓰지 말고 환경변수로:

```python
import os
kis = KISClient(
    app_key=os.environ["KIS_APP_KEY"],
    app_secret=os.environ["KIS_APP_SECRET"],
    account=os.environ["KIS_ACCOUNT"],
)
```

## 첫 조회

```python
s = kis.domestic.stock("005930")  # 삼성전자
q = s.quote()

q.current_price  # 현재가
q.change  # 전일대비
q.change_percent  # 등락률(%)
q.volume  # 거래량
```

## 안전 스위치 (주문할 때)

```python
kis = KISClient(
    …,
    orderable=True,  # 주문 가능 여부 (계좌 유형에서 자동 판단)
    allow_credit=False,  # 신용주문은 명시적으로 켜야 함
    throttle=True,  # 초당 호출 제한 자동 준수 (실전 15 / 모의 1)
)
```

조회만 할 거면 신경 안 써도 된다. 주문의 안전장치는 [주문](orders.md)에서 자세히.

## 결과 객체 다루기

모든 결과는 **읽기전용**. 필드로 꺼내 쓰고, 필드 설명이 궁금하면 `help`.

```python
q = kis.domestic.stock("005930").quote()

q.current_price  # 매핑된 값 (Decimal)
q._raw  # KIS 원본 응답 전체
help(type(q))  # 이 결과의 필드 설명(한국어) + KIS URL·TR-id
```

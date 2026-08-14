"""실시간 WebSocket 와이어 프로토콜 -- 순수 함수/타입.

KIS 실시간(웹소켓)의 저수준 규약만 담는다. 부작용(네트워크/스레드) 없이 문자열 <-> 구조를
오가는 순수 로직이라 단독 테스트가 쉽다. 상위 연결/디스패치는 :mod:`._connection` 이 쓴다.

프레임 규약(KIS):

- 수신 raw 의 첫 글자가 ``0``/``1`` 이면 **데이터 프레임**: ``flag|tr_id|record_count|payload``.
  ``flag`` ``0``=평문, ``1``=암호(AES-CBC). ``payload`` 는 ``^`` 로 구분된 필드열이며,
  ``record_count`` 개 레코드가 같은 필드셋으로 연속해 붙는다(멀티레코드).
- 그 밖이면 **시스템 응답(JSON)**: 구독 등록/해제 ACK(암호 TR 은 ``body.output.{key,iv}`` 동봉),
  또는 ``PINGPONG`` 하트비트.

구독/해제 메시지 규약:

``{"header":{"approval_key","custtype","tr_type","content-type"},
   "body":{"input":{"tr_id","tr_key"}}}`` -- ``tr_type`` ``1``=등록 ``2``=해제.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

CustomerType = Literal["P", "B"]  # P: 개인, B: 법인

_SUBSCRIBE = "1"
_UNSUBSCRIBE = "2"


def build_subscription_message(
    approval_key: str,
    tr_id: str,
    tr_key: str,
    *,
    subscribe: bool = True,
    customer_type: CustomerType = "P",
) -> str:
    """구독(또는 해제) 요청 JSON 문자열을 만든다.

    ``subscribe=False`` 면 해제(``tr_type="2"``). ``tr_key`` 는 TR 종류에 따라 종목번호(6자리),
    HTS ID(체결통보), 심볼 등이다 -- 여기서는 값을 검증하지 않고 그대로 싣는다.
    """
    message = {
        "header": {
            "approval_key": approval_key,
            "custtype": customer_type,
            "tr_type": _SUBSCRIBE if subscribe else _UNSUBSCRIBE,
            "content-type": "utf-8",
        },
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    }
    return json.dumps(message)


@dataclass(frozen=True, slots=True)
class DataFrame:
    """데이터 프레임 한 개(파싱 전 payload 보유).

    ``records`` 로 필드셋 개수(field_count)를 알 때 레코드 단위로 쪼갠다. ``encrypted`` 면
    상위 계층이 복호화한 평문을 :func:`records` 에 넣어 부른다.
    """

    encrypted: bool
    tr_id: str
    record_count: int
    payload: str

    def records(self, field_count: int) -> list[list[str]]:
        """``^`` 구분 payload 를 ``record_count`` x ``field_count`` 레코드로 분해한다.

        멀티레코드는 같은 필드셋이 연속해 붙은 형태다. 필드 수가 안 맞으면 fail-closed
        (``ValueError``) -- 상위에서 드롭/경고 처리.
        """
        fields = self.payload.split("^")
        expected = self.record_count * field_count
        if len(fields) != expected:
            # 부족·초과 모두 레이아웃 불일치 -> fail-closed(초과를 조용히 잘라 오정렬 방지).
            raise ValueError(
                f"{self.tr_id}: 필드 수 불일치 (기대 {expected}, 실제 {len(fields)})"
            )
        return [
            fields[i * field_count : (i + 1) * field_count]
            for i in range(self.record_count)
        ]


@dataclass(frozen=True, slots=True)
class SystemMessage:
    """시스템 응답(JSON) -- 구독 ACK 또는 PINGPONG."""

    tr_id: str
    raw: Mapping[str, Any]

    @property
    def is_pingpong(self) -> bool:
        return self.tr_id == "PINGPONG"

    @property
    def encryption_key(self) -> tuple[str, str] | None:
        """구독 ACK 에 담긴 (key, iv). 암호 TR 일 때만 존재, 아니면 ``None``."""
        output = self.raw.get("body", {}).get("output", {})
        key, iv = output.get("key"), output.get("iv")
        return (key, iv) if key and iv else None

    @property
    def return_code(self) -> str | None:
        """구독 ACK 의 처리 결과 코드(``body.rt_cd``). ``"0"`` 성공."""
        return self.raw.get("body", {}).get("rt_cd")


def parse_frame(raw: str) -> DataFrame | SystemMessage:
    """수신 raw 문자열을 :class:`DataFrame` 또는 :class:`SystemMessage` 로 분류/파싱한다.

    첫 글자 ``0``/``1`` 이면 데이터 프레임(암호화 여부 = ``1``), 아니면 JSON 시스템 응답.
    """
    if raw and raw[0] in ("0", "1"):
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"잘못된 데이터 프레임: {raw[:40]!r}")
        flag, tr_id, count, payload = parts
        return DataFrame(
            encrypted=(flag == "1"),
            tr_id=tr_id,
            record_count=int(count),
            payload=payload,
        )
    parsed = json.loads(raw)
    tr_id = parsed.get("header", {}).get("tr_id", "")
    return SystemMessage(tr_id=tr_id, raw=parsed)


def aes_cbc_decrypt(key: str, iv: str, cipher_text_base64: str) -> str:
    """AES-CBC/PKCS7 복호화(체결통보 등 암호 프레임).

    ``key``/``iv`` 는 구독 ACK 에서 받은 문자열(utf-8 bytes 로 사용). ``cipher_text_base64`` 는
    base64 로 인코딩된 암호문. :mod:`cryptography` 는 암호 프레임을 실제로 복호화할 때만 쓰이도록
    지연 import 한다(평문 피드만 쓰면 로드조차 안 됨).
    """
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - 설치 안내
        raise RuntimeError(
            "'cryptography' 를 import 할 수 없습니다(기본 의존성이어야 함): pip install kis-trader"
        ) from exc

    cipher_bytes = base64.b64decode(cipher_text_base64)
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")

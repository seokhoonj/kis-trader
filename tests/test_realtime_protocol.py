"""실시간 WebSocket 와이어 프로토콜(_protocol) 순수 단위테스트.

네트워크/스레드 없이 문자열 <-> 구조 변환만 검증한다: 구독 메시지 빌더, 프레임 분류/파싱,
멀티레코드 분해, AES-CBC 복호화(고정 벡터).
"""

from __future__ import annotations

import base64
import json

import pytest

from kis_trader.realtime._protocol import (
    DataFrame,
    SystemMessage,
    aes_cbc_decrypt,
    build_subscription_message,
    parse_frame,
)


def test_build_subscription_message_subscribe():
    msg = json.loads(build_subscription_message("APPKEY", "H0STCNT0", "005930"))
    assert msg["header"]["approval_key"] == "APPKEY"
    assert msg["header"]["tr_type"] == "1"  # 등록
    assert msg["header"]["custtype"] == "P"
    assert msg["header"]["content-type"] == "utf-8"
    assert msg["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}


def test_build_subscription_message_unsubscribe_and_corporate():
    msg = json.loads(
        build_subscription_message(
            "K", "H0STASP0", "005930", subscribe=False, customer_type="B"
        )
    )
    assert msg["header"]["tr_type"] == "2"  # 해제
    assert msg["header"]["custtype"] == "B"


def test_parse_frame_plaintext_data():
    frame = parse_frame("0|H0STCNT0|001|005930^093000^71500^2^100")
    assert isinstance(frame, DataFrame)
    assert frame.encrypted is False
    assert frame.tr_id == "H0STCNT0"
    assert frame.record_count == 1
    assert frame.payload == "005930^093000^71500^2^100"


def test_parse_frame_encrypted_flag():
    frame = parse_frame("1|H0STCNI0|001|ENCRYPTEDBASE64PAYLOAD")
    assert isinstance(frame, DataFrame)
    assert frame.encrypted is True
    assert frame.tr_id == "H0STCNI0"


def test_parse_frame_payload_may_contain_no_extra_pipe_split():
    # payload 는 3번째 '|' 이후 전부 -- 내부에 '|' 가 있어도 잘리지 않는다(maxsplit=3).
    frame = parse_frame("0|TR|001|a^b|c")
    assert isinstance(frame, DataFrame)
    assert frame.payload == "a^b|c"


def test_dataframe_records_single():
    frame = DataFrame(encrypted=False, tr_id="T", record_count=1, payload="a^b^c")
    assert frame.records(3) == [["a", "b", "c"]]


def test_dataframe_records_multi():
    frame = DataFrame(
        encrypted=False, tr_id="T", record_count=2, payload="a^b^c^d^e^f"
    )
    assert frame.records(3) == [["a", "b", "c"], ["d", "e", "f"]]


def test_dataframe_records_field_shortfall_fails_closed():
    frame = DataFrame(encrypted=False, tr_id="T", record_count=1, payload="a^b")
    with pytest.raises(ValueError):
        frame.records(3)


def test_dataframe_records_field_excess_fails_closed():
    # 초과도 레이아웃 불일치 -> 조용히 자르지 않고 raise(오정렬 방지).
    frame = DataFrame(encrypted=False, tr_id="T", record_count=1, payload="a^b^c^EXTRA")
    with pytest.raises(ValueError):
        frame.records(3)


def test_parse_frame_system_message_and_pingpong():
    ping = parse_frame(json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "1"}}))
    assert isinstance(ping, SystemMessage)
    assert ping.is_pingpong is True

    ack = parse_frame(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNI0"},
                "body": {"rt_cd": "0", "output": {"key": "k" * 32, "iv": "i" * 16}},
            }
        )
    )
    assert isinstance(ack, SystemMessage)
    assert ack.is_pingpong is False
    assert ack.return_code == "0"
    assert ack.encryption_key == ("k" * 32, "i" * 16)


def test_system_message_without_encryption_key():
    ack = parse_frame(
        json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {"rt_cd": "0"}})
    )
    assert isinstance(ack, SystemMessage)
    assert ack.encryption_key is None


def test_aes_cbc_decrypt_roundtrip():
    # 고정 키/iv 로 평문을 암호화한 뒤(테스트 내에서 cryptography 로), 복호화가 원문을 복원하는지.
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = "0123456789abcdef0123456789abcdef", "abcdef9876543210"
    plaintext = "005930^093000^71500^체결통보"
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(key.encode()), modes.CBC(iv.encode())
    ).encryptor()
    cipher_bytes = encryptor.update(padded) + encryptor.finalize()
    cipher_b64 = base64.b64encode(cipher_bytes).decode()

    assert aes_cbc_decrypt(key, iv, cipher_b64) == plaintext


def test_system_message_with_null_body_does_not_crash():
    # body 가 명시적 null 인 시스템 프레임에서 encryption_key/return_code 가 AttributeError 를 내면
    # 수신 루프가 죽고 재연결도 못 탄다 -- None-safe 로 흡수해 조용히 None 을 돌려줘야 한다.
    msg = SystemMessage(tr_id="H0STCNI0", raw={"body": None})
    assert msg.encryption_key is None
    assert msg.return_code is None

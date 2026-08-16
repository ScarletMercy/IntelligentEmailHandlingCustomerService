"""QQ 扫码配置模块与占位符守卫（无网络）。"""

import base64
import os
from urllib.parse import quote

from ai_email.qq_bot import is_placeholder_target


def test_placeholder_targets_detected():
    assert is_placeholder_target("c2c:openid")
    assert is_placeholder_target("group:groupid")
    assert is_placeholder_target("openid")  # 无前缀裸占位符（_parse_target 按 c2c 处理）
    assert is_placeholder_target("  group:groupid  ")


def test_real_targets_not_placeholder():
    assert not is_placeholder_target("c2c:4C73666A6F356F70")
    assert not is_placeholder_target("group:ABC123xyz")
    assert not is_placeholder_target("")


def test_decrypt_secret_roundtrip():
    """_decrypt_secret 兼容平台密文布局 IV(12) || ciphertext || tag(16)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from ai_email import qq_onboard

    key_b64 = qq_onboard._generate_bind_key()
    aesgcm = AESGCM(base64.b64decode(key_b64))
    iv = os.urandom(12)
    blob = base64.b64encode(iv + aesgcm.encrypt(iv, "密钥中文secret".encode(), None)).decode()
    assert qq_onboard._decrypt_secret(blob, key_b64) == "密钥中文secret"


def test_bind_key_is_256_bit():
    from ai_email import qq_onboard

    assert len(base64.b64decode(qq_onboard._generate_bind_key())) == 32


def test_build_connect_url_quotes_task_id():
    from ai_email import qq_onboard

    url = qq_onboard.build_connect_url("task id/含中文")
    assert url.startswith("https://q.qq.com/qqbot/openclaw/connect.html?task_id=")
    assert quote("task id/含中文") in url


def test_decrypt_secret_short_ciphertext_raises_value_error():
    """密文短于 IV(12)+tag(16) 下限时给出可定位的 ValueError，而非底层 InvalidTag。"""
    import base64

    import pytest

    from ai_email.qq_onboard import _decrypt_secret

    key = base64.b64encode(b"\x00" * 32).decode()
    short_ct = base64.b64encode(b"\x00" * 10).decode()
    with pytest.raises(ValueError, match="密文长度"):
        _decrypt_secret(short_ct, key)

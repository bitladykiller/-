"""认证服务单测：哈希、令牌、注册/登录规则。"""

from __future__ import annotations

import pytest
from app.user.application.auth_service import (
    AuthenticatedUser,
    AuthError,
    RegistrationError,
    hash_password,
    issue_access_token,
    validate_registration,
    verify_access_token,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    hashed = hash_password("s3cret-密码")

    assert hashed != "s3cret-密码"
    assert verify_password("s3cret-密码", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_password_tolerates_malformed_hash() -> None:
    assert verify_password("any", "not-a-bcrypt-hash") is False


def test_token_round_trip() -> None:
    token = issue_access_token(42, "alice")

    user = verify_access_token(token)

    assert user == AuthenticatedUser(id=42, username="alice")


def test_expired_token_is_rejected() -> None:
    token = issue_access_token(42, "alice", now=1_000_000, ttl_seconds=60)

    with pytest.raises(AuthError, match="过期"):
        verify_access_token(token)


def test_tampered_token_is_rejected() -> None:
    token = issue_access_token(42, "alice")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")

    with pytest.raises(AuthError):
        verify_access_token(tampered)


def test_token_signed_with_other_key_is_rejected() -> None:
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "1", "username": "admin", "exp": 9_999_999_999},
        "attacker-key",
        algorithm="HS256",
    )

    with pytest.raises(AuthError):
        verify_access_token(forged)


def test_registration_validation_rules() -> None:
    with pytest.raises(RegistrationError, match="用户名"):
        validate_registration("a", "password1")
    with pytest.raises(RegistrationError, match="密码"):
        validate_registration("alice", "123")
    # 合规参数不抛
    validate_registration("alice", "password1")


async def test_register_race_maps_integrity_error_to_registration_error() -> None:
    """并发同名注册竞态：唯一键冲突必须转 RegistrationError（400），不是 500。

    查重 SELECT 只是友好文案的快捷路径；真正的裁判是数据库唯一约束。
    """
    from app.user.application.auth_service import AuthService
    from sqlalchemy.exc import IntegrityError

    class RaceSession:
        def __init__(self) -> None:
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def execute(self, _stmt):
            class _R:
                def scalar_one_or_none(self):
                    return None  # 查重时对方还没提交

            return _R()

        def add(self, _obj) -> None:
            pass

        async def commit(self):
            raise IntegrityError("dup", None, Exception("Duplicate entry"))

        async def rollback(self):
            self.rolled_back = True

    session = RaceSession()
    service = AuthService(session_factory=lambda: session)

    with pytest.raises(RegistrationError, match="占用"):
        await service.register("alice", "password1")

    assert session.rolled_back is True

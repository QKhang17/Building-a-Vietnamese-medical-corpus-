import base64
import hashlib

import pytest

from core.expert_service import create_token, decode_token, verify_password


def test_password_hash_and_signed_token(monkeypatch):
    monkeypatch.setenv("EXPERT_TOKEN_SECRET", "test-only-signing-secret-not-for-production")
    password = "test-only-password"
    salt = b"test-only-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")
    encoded = f"$pbkdf2-sha256$260000${encode(salt)}${encode(digest)}"
    assert verify_password(password, encoded)
    assert not verify_password("incorrect-password", encoded)
    token = create_token({"id": 1, "username": "test-expert", "full_name": "Test Expert"})
    payload = decode_token(token)
    assert payload["sub"] == 1
    assert payload["role"] == "expert"


@pytest.mark.parametrize("secret", [None, "", "too-short"])
def test_signing_requires_private_secret(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("EXPERT_TOKEN_SECRET", raising=False)
    else:
        monkeypatch.setenv("EXPERT_TOKEN_SECRET", secret)
    with pytest.raises(ValueError, match="EXPERT_TOKEN_SECRET"):
        create_token({"id": 1, "username": "test", "full_name": "Test"})


def test_token_tampering_is_rejected(monkeypatch):
    monkeypatch.setenv("EXPERT_TOKEN_SECRET", "test-only-signing-secret-not-for-production")
    token = create_token({"id": 1, "username": "test", "full_name": "Test"})
    body, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    with pytest.raises(ValueError):
        decode_token(body + "." + replacement + signature[1:])

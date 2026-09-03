from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from base64 import urlsafe_b64encode

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet_from_secret(settings.credential_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet_from_secret(settings.credential_key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("credential cannot be decrypted with current key") from exc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def last4(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 4:
        return cleaned
    return cleaned[-4:]


def sign_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def random_client_pepper() -> str:
    return os.urandom(8).hex()

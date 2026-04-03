from __future__ import annotations

import hashlib
import secrets


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str, length: int = 8) -> str:
    return token[:length]


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def generate_claim_token() -> str:
    return f"clm_{secrets.token_urlsafe(10)}"

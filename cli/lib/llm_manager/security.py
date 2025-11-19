from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
from typing import Optional


# ---- POC static-key encryption ----
# Try to use cryptography.Fernet if available; otherwise fall back to a simple
# XOR+HMAC scheme (still protected against tampering, but NOT strong encryption).

def _kdf(secret: str) -> bytes:
    # Simple KDF: SHA-256 of UTF-8 secret (POC)
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt(plaintext: bytes, shared_secret: str) -> bytes:
    try:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(_kdf(shared_secret))
        return Fernet(key).encrypt(plaintext)
    except Exception:
        # Fallback: XOR with stream derived from HMAC(secret, counter), plus HMAC tag
        key = _kdf(shared_secret)
        stream = _prng_stream(key, len(plaintext))
        ct = bytes([a ^ b for a, b in zip(plaintext, stream)])
        tag = hmac.new(key, ct, hashlib.sha256).digest()
        return b"X1" + base64.urlsafe_b64encode(tag + ct)


def decrypt(ciphertext: bytes, shared_secret: str) -> bytes:
    # Fernet
    if not ciphertext.startswith(b"X1"):
        try:
            from cryptography.fernet import Fernet
            key = base64.urlsafe_b64encode(_kdf(shared_secret))
            return Fernet(key).decrypt(ciphertext)
        except Exception as e:
            raise

    # Fallback format "X1" + b64(tag | ct)
    raw = base64.urlsafe_b64decode(ciphertext[2:])
    tag, ct = raw[:32], raw[32:]
    key = _kdf(shared_secret)
    if not hmac.compare_digest(tag, hmac.new(key, ct, hashlib.sha256).digest()):
        raise ValueError("HMAC verification failed")
    stream = _prng_stream(key, len(ct))
    pt = bytes([a ^ b for a, b in zip(ct, stream)])
    return pt


def _prng_stream(key: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        counter_bytes = counter.to_bytes(8, "big")
        out.extend(hashlib.sha256(key + counter_bytes).digest())
        counter += 1
    return bytes(out[:n])


# ---- Simple filesystem "vault" for POC ----

class LLMKeyVault:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def path_for(self, provider: str) -> Path:
        return self.base_dir / f"{provider.lower()}.key.enc"

    def write_encrypted_key(self, provider: str, ciphertext: bytes) -> Path:
        p = self.path_for(provider)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(ciphertext)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return p

    def read_encrypted_key(self, provider: str) -> Optional[bytes]:
        p = self.path_for(provider)
        if not p.exists():
            return None
        with open(p, "rb") as f:
            return f.read()
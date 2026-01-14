from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def unb64url(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def new_secret() -> str:
    # 32 bytes of entropy, URL-safe for storage/transport.
    return b64url(os.urandom(32))


def sign_hmac(secret: str, *, ts: int, method: str, path: str, body: Any) -> str:
    """
    HMAC over canonical JSON body + request metadata.
    """
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    msg = b"\n".join(
        [
            str(ts).encode("ascii"),
            method.upper().encode("ascii"),
            path.encode("utf-8"),
            body_bytes,
        ]
    )
    mac = hmac.new(unb64url(secret), msg, hashlib.sha256).digest()
    return b64url(mac)


def verify_hmac(
    secret: str,
    *,
    ts: int,
    method: str,
    path: str,
    body: Any,
    sig: str,
    max_skew_s: int = 60,
) -> bool:
    now = int(time.time())
    if ts < now - max_skew_s or ts > now + max_skew_s:
        return False
    expected = sign_hmac(secret, ts=ts, method=method, path=path, body=body)
    return hmac.compare_digest(expected, sig)



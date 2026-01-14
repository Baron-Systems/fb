from __future__ import annotations

from dataclasses import dataclass

from .security import sign_hmac


@dataclass(frozen=True)
class SignedHeaders:
    ts: int
    sig: str


def signed_headers(secret: str, *, ts: int, method: str, path: str, body: dict) -> dict[str, str]:
    sig = sign_hmac(secret, ts=ts, method=method, path=path, body=body)
    return {"X-FB-TS": str(ts), "X-FB-SIG": sig}



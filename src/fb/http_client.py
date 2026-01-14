from __future__ import annotations

import httpx


def client() -> httpx.Client:
    # Conservative defaults; short timeouts to avoid UI hangs.
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))



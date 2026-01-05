from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .config import Config
from .utils import FBError


def telegram_send(cfg: Config, message: str, *, dry_run: bool) -> None:
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return
    if dry_run:
        return

    token = cfg.telegram_token
    chat_id = cfg.telegram_chat_id
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            if not payload.get("ok", False):
                raise FBError("Telegram API returned failure.", exit_code=1)
    except FBError:
        raise
    except Exception as e:
        raise FBError(f"Failed to send Telegram notification: {e}", exit_code=1) from e



from __future__ import annotations

import os
from pathlib import Path


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def fb_state_dir() -> Path:
    """
    Zero-config state dir.
    Prefers XDG; falls back to ~/.local/share/fb.
    """
    xdg = os.environ.get("XDG_STATE_HOME") or os.environ.get("XDG_DATA_HOME")
    if xdg:
        return _ensure_dir(Path(xdg) / "fb")
    return _ensure_dir(Path.home() / ".local" / "share" / "fb")


def fb_db_path() -> Path:
    return fb_state_dir() / "fb.sqlite3"


def fb_backups_root() -> Path:
    """
    Required layout target is /backups/<agent>/<stack>/<site>/<timestamp>/.
    We try to use /backups if possible, otherwise fall back to state dir.
    """
    preferred = Path("/backups")
    try:
        _ensure_dir(preferred)
        test = preferred / ".fb_write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return preferred
    except Exception:
        return _ensure_dir(fb_state_dir() / "backups")



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
    Backups storage with preference order:
    1. /srv/backups (production standard)
    2. /backups (if writable)
    3. ~/.local/share/fb/backups (fallback)
    """
    # Try /srv/backups first (production standard)
    srv_path = Path("/srv/backups")
    if srv_path.exists() and os.access(srv_path, os.W_OK):
        return srv_path
    
    # Try to create /srv/backups if /srv exists
    srv_parent = Path("/srv")
    if srv_parent.exists() and os.access(srv_parent, os.W_OK):
        try:
            _ensure_dir(srv_path)
            # Test write
            test = srv_path / ".fb_write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return srv_path
        except (OSError, PermissionError):
            pass
    
    # Try /backups
    preferred = Path("/backups")
    try:
        _ensure_dir(preferred)
        test = preferred / ".fb_write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return preferred
    except Exception:
        pass
    
    # Fallback to user data dir
    return _ensure_dir(fb_state_dir() / "backups")



from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .utils import atomic_write_json, utc_now_iso


@dataclass(frozen=True)
class RunMetadata:
    site: str
    date: str  # YYYY-MM-DD
    started_at: str
    finished_at: str
    status: str  # "ok" | "failed"
    message: str
    artifacts: dict[str, Any]


def meta_dir(site_root: Path) -> Path:
    return site_root / ".meta"


def last_run_path(site_root: Path) -> Path:
    return meta_dir(site_root) / "last_run.json"


def write_last_run(site_root: Path, meta: RunMetadata, *, dry_run: bool) -> None:
    data = {
        "site": meta.site,
        "date": meta.date,
        "started_at": meta.started_at,
        "finished_at": meta.finished_at,
        "status": meta.status,
        "message": meta.message,
        "artifacts": meta.artifacts,
        "written_at": utc_now_iso(),
    }
    if dry_run:
        return
    atomic_write_json(last_run_path(site_root), data, mode=0o600)


def read_last_run(site_root: Path) -> Optional[dict[str, Any]]:
    p = last_run_path(site_root)
    if not p.exists():
        return None
    import json

    return json.loads(p.read_text(encoding="utf-8"))



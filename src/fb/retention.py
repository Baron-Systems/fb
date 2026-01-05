from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .utils import FBError, list_date_dirs, parse_date_yyyy_mm_dd


@dataclass(frozen=True)
class RetentionResult:
    deleted: list[Path]
    kept: list[Path]


def apply_retention(site_root: Path, *, retention_days: int, dry_run: bool) -> RetentionResult:
    """
    Retention is configured as days; since backups are stored in date folders,
    we prune folders older than (today - retention_days).
    """
    if retention_days <= 0:
        raise FBError("Retention days must be > 0.", exit_code=2)
    dirs = list_date_dirs(site_root)
    if not dirs:
        return RetentionResult(deleted=[], kept=[])

    cutoff = date.today() - timedelta(days=retention_days)
    deleted: list[Path] = []
    kept: list[Path] = []
    for d in dirs:
        dt = parse_date_yyyy_mm_dd(d.name).date()
        if dt < cutoff:
            deleted.append(d)
        else:
            kept.append(d)

    for d in deleted:
        if dry_run:
            continue
        # careful: only delete date dirs under site_root
        if d.parent.resolve() != site_root.resolve():
            raise FBError("Refusing to delete outside site root.", exit_code=2)
        for p in sorted(d.rglob("*"), reverse=True):
            if p.is_file() or p.is_symlink():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass

    return RetentionResult(deleted=deleted, kept=kept)



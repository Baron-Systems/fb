from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import FBError, require_bin, run_cmd, validate_site_name


@dataclass(frozen=True)
class VerifyResult:
    site: str
    date: str
    ok: bool
    message: str
    db_path: Optional[Path] = None
    public_files_tar: Optional[Path] = None
    private_files_tar: Optional[Path] = None


def _pick_file(dest_dir: Path, *, suffix: str) -> Path:
    matches = [p for p in dest_dir.iterdir() if p.is_file() and p.name.endswith(suffix)]
    if not matches:
        raise FBError(f"Missing required file ({suffix}) in {dest_dir}", exit_code=1)
    return max(matches, key=lambda p: p.stat().st_mtime)


def _pick_optional_file(dest_dir: Path, *, suffix: str) -> Optional[Path]:
    matches = [p for p in dest_dir.iterdir() if p.is_file() and p.name.endswith(suffix)]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def verify_backup_dir(site: str, date: str, backup_dir: Path, *, dry_run: bool) -> VerifyResult:
    site = validate_site_name(site)
    if not backup_dir.exists() or not backup_dir.is_dir():
        return VerifyResult(site=site, date=date, ok=False, message=f"Backup directory missing: {backup_dir}")

    try:
        db = _pick_file(backup_dir, suffix="database.sql.gz")
        pub = _pick_file(backup_dir, suffix="files.tar")
        priv = _pick_optional_file(backup_dir, suffix="private-files.tar")

        # Use system tools exactly as required.
        require_bin("gzip")
        require_bin("tar")

        run_cmd(["gzip", "-t", str(db)], dry_run=dry_run, check=True)
        run_cmd(["tar", "-tf", str(pub)], dry_run=dry_run, check=True)
        if priv:
            run_cmd(["tar", "-tf", str(priv)], dry_run=dry_run, check=True)

        return VerifyResult(
            site=site,
            date=date,
            ok=True,
            message="OK",
            db_path=db,
            public_files_tar=pub,
            private_files_tar=priv,
        )
    except FBError as e:
        return VerifyResult(site=site, date=date, ok=False, message=str(e))
    except Exception as e:
        return VerifyResult(site=site, date=date, ok=False, message=f"Unexpected error: {e}")



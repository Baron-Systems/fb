from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .remote import Remote
from .rsync import rsync_push_dir
from .utils import FBError, validate_site_name
from .verify import VerifyResult, verify_backup_dir


@dataclass(frozen=True)
class RestoreResult:
    site: str
    date: str
    ok: bool
    message: str


def restore_site(
    cfg: Config,
    *,
    site: str,
    date: str,
    local_backup_dir: Path,
    confirm: bool,
    dry_run: bool,
) -> RestoreResult:
    site = validate_site_name(site)
    if not dry_run and not confirm:
        raise FBError("--confirm is mandatory for restore.", exit_code=2)

    # Always do a verify step first; for non-dry-run, it must succeed.
    verify: VerifyResult = verify_backup_dir(site, date, local_backup_dir, dry_run=dry_run)
    if not verify.ok:
        raise FBError(f"Verify failed; refusing restore: {verify.message}", exit_code=1)

    assert verify.db_path and verify.public_files_tar  # for mypy; verify.ok guarantees
    remote = Remote(cfg)

    # Upload into a dedicated temp dir.
    temp_dir = remote.mktemp_dir(prefix=f"fb-restore-{site}", dry_run=dry_run)
    completed = False
    try:
        rsync_push_dir(cfg, local_dir=local_backup_dir, remote_dir=temp_dir, dry_run=dry_run)

        remote_db = f"{temp_dir}/{verify.db_path.name}"
        remote_pub = f"{temp_dir}/{verify.public_files_tar.name}"
        remote_priv: Optional[str] = (
            f"{temp_dir}/{verify.private_files_tar.name}" if verify.private_files_tar else None
        )

        # Maintenance mode: keep enabled if restore fails.
        remote.set_maintenance_mode(site, enabled=True, dry_run=dry_run)
        restore_ok = False
        try:
            remote.bench_restore(
                site,
                db_path=remote_db,
                public_files_tar=remote_pub,
                private_files_tar=remote_priv,
                dry_run=dry_run,
            )
            restore_ok = True
        finally:
            if restore_ok:
                remote.set_maintenance_mode(site, enabled=False, dry_run=dry_run)

        if dry_run:
            completed = True
            return RestoreResult(site=site, date=date, ok=True, message="DRY-RUN: would restore")
        completed = True
        return RestoreResult(site=site, date=date, ok=True, message="restore completed")
    finally:
        # Clean up temp dir only on non-dry-run success; keep on failure for debugging.
        if completed and (not dry_run):
            try:
                remote.rm_rf(temp_dir, dry_run=False)
            except Exception:
                pass



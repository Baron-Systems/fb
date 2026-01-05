from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .config import Config
from .metadata import RunMetadata, write_last_run
from .remote import Remote
from .retention import apply_retention
from .rsync import rsync_pull_files
from .sites import SiteEntry
import shlex

from .utils import FBError, ensure_dir, require_bin, run_pipe, utc_now_iso, validate_site_name


@dataclass(frozen=True)
class BackupResult:
    site: str
    date: str
    local_dir: Path
    pulled: dict[str, Optional[Path]]


def _site_root(cfg: Config, site: str) -> Path:
    return Path(cfg.local_backup_root) / site


def run_backup_for_site(
    cfg: Config,
    entry: SiteEntry,
    *,
    dry_run: bool,
) -> BackupResult:
    site = validate_site_name(entry.site)
    today = date.today().strftime("%Y-%m-%d")
    site_root = _site_root(cfg, site)
    dest_dir = site_root / today
    meta_started = utc_now_iso()
    remote = Remote(cfg)

    ensure_dir(dest_dir, dry_run=dry_run)

    status = "ok"
    message = "backup completed"
    pulled_paths: dict[str, Optional[Path]] = {"db": None, "public": None, "private": None}
    try:
        remote.ping(dry_run=dry_run)
        if cfg.remote_mode == "fm" and getattr(cfg, "fm_transport", "export") == "stream":
            _fm_stream_backup(cfg, site, dest_dir, dry_run=dry_run)
            artifacts = {"stage_dir": None, "db": None, "public": None, "private": None}
        else:
            remote.bench_backup_with_files(site, dry_run=dry_run)
            artifacts = remote.latest_backup_paths(site, dry_run=dry_run)

        if not (cfg.remote_mode == "fm" and getattr(cfg, "fm_transport", "export") == "stream"):
            remote_files = [artifacts["db"], artifacts["public"]]
            if artifacts.get("private"):
                remote_files.append(artifacts["private"])

            # Pull artifacts into date directory.
            rsync_pull_files(cfg, remote_paths=[p for p in remote_files if p], local_dir=dest_dir, dry_run=dry_run)

            # Docker mode stages artifacts onto remote host /tmp; clean up after successful pull.
            stage_dir = artifacts.get("stage_dir")
            if stage_dir:
                remote.rm_rf(stage_dir, dry_run=dry_run)

        if not dry_run:
            pulled_paths["db"] = _pick_file(dest_dir, suffix="database.sql.gz")
            pulled_paths["public"] = _pick_file(dest_dir, suffix="files.tar")
            pulled_paths["private"] = _pick_optional_file(dest_dir, suffix="private-files.tar")
        else:
            pulled_paths["db"] = dest_dir / "<database.sql.gz>"
            pulled_paths["public"] = dest_dir / "<files.tar>"
            pulled_paths["private"] = dest_dir / "<private-files.tar?>"

        # Apply retention for this site.
        apply_retention(site_root, retention_days=entry.retention_days, dry_run=dry_run)

    except Exception as e:
        status = "failed"
        message = str(e)
        if isinstance(e, FBError):
            # preserve message, exit code handled by CLI
            pass
        else:
            message = f"Unexpected error: {e}"
        raise
    finally:
        meta = RunMetadata(
            site=site,
            date=today,
            started_at=meta_started,
            finished_at=utc_now_iso(),
            status=status,
            message=message,
            artifacts={
                "local_dir": str(dest_dir),
                "pulled": {k: (str(v) if v else None) for k, v in pulled_paths.items()},
            },
        )
        write_last_run(site_root, meta, dry_run=dry_run)

    return BackupResult(site=site, date=today, local_dir=dest_dir, pulled=pulled_paths)


def _fm_stream_backup(cfg: Config, site: str, dest_dir: Path, *, dry_run: bool) -> None:
    """
    fm transport=stream:
    ssh user@remote "fm shell SITE -c '<backup + tar -czf - .>'" | tar -xzf - -C <dest_dir>
    """
    require_bin("ssh")
    require_bin("tar")
    site = validate_site_name(site)
    dest_dir = dest_dir.resolve()
    ensure_dir(dest_dir, dry_run=dry_run)

    # remote command: use heredoc stdin execution (fm doesn't accept extra args in many builds).
    # Hard requirement: keep the pipe's stdout clean and send ONLY the tar.gz bytes to it.
    # We do that by:
    # - keeping stdout redirected to stderr for the whole remote script
    # - opening FD 3 as the original stdout
    # - writing tar output to /proc/self/fd/3 from inside the fm shell
    bench_candidates = [
        cfg.bench_path,
        "/workspace/frappe-bench",
        "/home/frappe/frappe-bench",
    ]
    bench_list = " ".join([shlex.quote(p) for p in bench_candidates])
    inner_lines = "\n".join(
        [
            "set -euo pipefail",
            "bench_root=''",
            f"for p in {bench_list}; do",
            '  if [ -d "$p" ] && [ -d "$p/sites" ]; then bench_root="$p"; break; fi',
            "done",
            'if [ -z "$bench_root" ]; then echo "ERR=Cannot find bench root inside fm shell. Set FRAPPE_BENCH_PATH to the bench root visible inside fm." 1>&2; exit 2; fi',
            'cd "$bench_root"',
            f"bench --site {site} backup --with-files",
            f"cd sites/{site}/private/backups",
            # Write the stream to FD 3 explicitly to avoid any stdout contamination.
            "tar -czf /proc/self/fd/3 .",
        ]
    )
    fm_bin = getattr(cfg, "fm_bin", "/home/baron/.local/bin/fm")
    fm_target = getattr(cfg, "remote_bench", None) or site
    remote_script = "\n".join(
        [
            "set -euo pipefail",
            "exec 3>&1",
            "exec 1>&2",
            # Redirect all fm/bench output to stderr; tar writes to FD 3 explicitly.
            f"{shlex.quote(fm_bin)} shell {shlex.quote(fm_target)} <<'FMEOF' 1>&2",
            inner_lines,
            "FMEOF",
            "exec 3>&-",
        ]
    )

    ssh_argv = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "IdentitiesOnly=yes",
        f"{cfg.remote_user}@{cfg.remote_host}",
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        remote_script,
    ]
    tar_argv = ["tar", "-xzf", "-", "-C", str(dest_dir)]
    run_pipe(ssh_argv, tar_argv, dry_run=dry_run, check=True)


def _pick_file(dest_dir: Path, *, suffix: str) -> Path:
    matches = sorted([p for p in dest_dir.iterdir() if p.is_file() and p.name.endswith(suffix)])
    if not matches:
        raise FBError(f"Expected {suffix} in {dest_dir}", exit_code=1)
    # The directory should contain only one latest file for each suffix, but pick newest if multiple.
    return max(matches, key=lambda p: p.stat().st_mtime)


def _pick_optional_file(dest_dir: Path, *, suffix: str) -> Optional[Path]:
    matches = sorted([p for p in dest_dir.iterdir() if p.is_file() and p.name.endswith(suffix)])
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)



from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .config import Config
from .utils import FBError, CmdResult, require_bin, run_cmd, safe_relpath


@dataclass(frozen=True)
class RsyncOptions:
    archive: bool = True
    compress: bool = True
    delete: bool = False
    protect_args: bool = True
    human: bool = False


def _ssh_rsync_transport(cfg: Config) -> str:
    # Keep options aligned with Remote: BatchMode, strict host key checking, timeouts.
    # Note: rsync invokes ssh as a subcommand; we pass a single -e string.
    parts = [
        "ssh",
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
    ]
    return shlex.join(parts)


def rsync_pull_files(
    cfg: Config,
    *,
    remote_paths: Iterable[str],
    local_dir: Path,
    dry_run: bool,
    opts: Optional[RsyncOptions] = None,
) -> CmdResult:
    require_bin("rsync")
    opts = opts or RsyncOptions()
    local_dir = local_dir.resolve()
    local_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None

    require_remote = []
    for p in remote_paths:
        # remote paths are absolute from Remote.latest_backup_paths or other internal construction
        safe_relpath(p)  # best-effort checks (rejects nulls/..)
        require_remote.append(p)

    base = ["rsync"]
    if opts.archive:
        base.append("-a")
    if opts.compress:
        base.append("-z")
    if opts.human:
        base.append("-h")
    if dry_run:
        base.append("-n")
    if opts.delete:
        base.append("--delete")
    if opts.protect_args:
        base.append("--protect-args")

    base += ["-e", _ssh_rsync_transport(cfg)]

    srcs = [f"{cfg.remote_user}@{cfg.remote_host}:{p}" for p in require_remote]
    argv = base + srcs + [str(local_dir) + "/"]
    return run_cmd(argv, dry_run=dry_run, check=True, capture=True)


def rsync_push_dir(
    cfg: Config,
    *,
    local_dir: Path,
    remote_dir: str,
    dry_run: bool,
    opts: Optional[RsyncOptions] = None,
) -> CmdResult:
    require_bin("rsync")
    opts = opts or RsyncOptions()
    local_dir = local_dir.resolve()
    if not local_dir.exists() or not local_dir.is_dir():
        raise FBError(f"Local path is not a directory: {local_dir}", exit_code=2)
    safe_relpath(remote_dir)

    base = ["rsync"]
    if opts.archive:
        base.append("-a")
    if opts.compress:
        base.append("-z")
    if opts.human:
        base.append("-h")
    if dry_run:
        base.append("-n")
    if opts.delete:
        base.append("--delete")
    if opts.protect_args:
        base.append("--protect-args")

    base += ["-e", _ssh_rsync_transport(cfg)]
    # trailing slash copies contents into remote_dir
    src = str(local_dir) + "/"
    dst = f"{cfg.remote_user}@{cfg.remote_host}:{remote_dir.rstrip('/')}/"
    argv = base + [src, dst]
    return run_cmd(argv, dry_run=dry_run, check=True, capture=True)



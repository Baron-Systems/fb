from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .utils import FBError, CmdResult, run_cmd, validate_site_name


@dataclass(frozen=True)
class SSHOptions:
    batch_mode: bool = True
    strict_host_key_checking: str = "yes"  # "yes" recommended for production
    connect_timeout: int = 15


class Remote:
    """
    SSH wrapper for executing a *small, whitelisted* set of remote operations.

    We intentionally do not expose "run arbitrary remote command" in the public API.
    """

    def __init__(self, cfg: Config, *, opts: Optional[SSHOptions] = None):
        self.cfg = cfg
        self.opts = opts or SSHOptions()

    def _ssh_base(self) -> list[str]:
        argv = ["ssh"]
        if self.opts.batch_mode:
            argv += ["-o", "BatchMode=yes"]
        argv += ["-o", f"StrictHostKeyChecking={self.opts.strict_host_key_checking}"]
        argv += ["-o", f"ConnectTimeout={self.opts.connect_timeout}"]
        argv += ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        argv += ["-o", "IdentitiesOnly=yes"]
        argv += [f"{self.cfg.remote_user}@{self.cfg.remote_host}"]
        return argv

    def _bash_lc(self, script: str) -> list[str]:
        # We control the script; no user-provided data is injected without validation/quoting.
        return self._ssh_base() + ["bash", "-lc", script]

    def _run(self, script: str, *, dry_run: bool, check: bool = True) -> CmdResult:
        return run_cmd(self._bash_lc(script), dry_run=dry_run, check=check, capture=True)

    def ping(self, *, dry_run: bool) -> None:
        self._run("true", dry_run=dry_run, check=True)

    def bench_backup_with_files(self, site: str, *, dry_run: bool) -> None:
        site = validate_site_name(site)
        bench = shlex.quote(self.cfg.bench_path)
        # bench itself is on the remote system; fb never needs it locally
        script = f"cd {bench} && bench --site {shlex.quote(site)} backup --with-files"
        self._run(script, dry_run=dry_run, check=True)

    def latest_backup_paths(self, site: str, *, dry_run: bool) -> dict[str, Optional[str]]:
        """
        Return newest artifacts in sites/<SITE>/private/backups/:
        - database.sql.gz (required)
        - files.tar (required)
        - private-files.tar (optional)
        """
        site = validate_site_name(site)
        bench = shlex.quote(self.cfg.bench_path)
        base = f"sites/{site}/private/backups"
        # We use ls -1t to find newest matching file for each glob, and prefix with $PWD
        # (since we cd into bench path).
        script = (
            f"cd {bench} && "
            f"db=$(ls -1t {shlex.quote(base)}/*_database.sql.gz 2>/dev/null | head -n1 || true); "
            f"pub=$(ls -1t {shlex.quote(base)}/*_files.tar 2>/dev/null | head -n1 || true); "
            f"priv=$(ls -1t {shlex.quote(base)}/*_private-files.tar 2>/dev/null | head -n1 || true); "
            f"[ -n \"$db\" ] && db=\"$PWD/$db\" || true; "
            f"[ -n \"$pub\" ] && pub=\"$PWD/$pub\" || true; "
            f"[ -n \"$priv\" ] && priv=\"$PWD/$priv\" || true; "
            f"printf 'DB=%s\\nPUB=%s\\nPRIV=%s\\n' \"$db\" \"$pub\" \"$priv\""
        )
        res = self._run(script, dry_run=dry_run, check=True)
        if dry_run:
            return {"db": None, "public": None, "private": None}

        kv: dict[str, str] = {}
        for line in res.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        db = kv.get("DB", "")
        pub = kv.get("PUB", "")
        priv = kv.get("PRIV", "")
        if not db or not pub:
            raise FBError(
                f"Remote backup artifacts not found for site '{site}'. "
                "Expected *_database.sql.gz and *_files.tar in sites/<SITE>/private/backups/.",
                exit_code=1,
            )
        return {"db": db, "public": pub, "private": priv or None}

    def mktemp_dir(self, *, prefix: str, dry_run: bool) -> str:
        safe_prefix = "".join([c for c in prefix if c.isalnum() or c in ("-", "_")])[:40] or "fb"
        script = f"mktemp -d -p /tmp {shlex.quote(safe_prefix)}.XXXXXX"
        res = self._run(script, dry_run=dry_run, check=True)
        if dry_run:
            return f"/tmp/{safe_prefix}.DRYRUN"
        path = res.stdout.strip()
        if not path.startswith("/"):
            raise FBError("Failed to create remote temp directory.", exit_code=1)
        return path

    def rm_rf(self, path: str, *, dry_run: bool) -> None:
        # Only allow /tmp paths created by fb
        if not path.startswith("/tmp/") or ".." in path or "\x00" in path:
            raise FBError("Refusing to remove unsafe remote path.", exit_code=2)
        script = f"rm -rf -- {shlex.quote(path)}"
        self._run(script, dry_run=dry_run, check=True)

    def set_maintenance_mode(self, site: str, *, enabled: bool, dry_run: bool) -> None:
        site = validate_site_name(site)
        bench = shlex.quote(self.cfg.bench_path)
        mode = "on" if enabled else "off"
        script = f"cd {bench} && bench --site {shlex.quote(site)} set-maintenance-mode {mode}"
        self._run(script, dry_run=dry_run, check=True)

    def bench_restore(
        self,
        site: str,
        *,
        db_path: str,
        public_files_tar: str,
        private_files_tar: Optional[str],
        dry_run: bool,
    ) -> None:
        site = validate_site_name(site)
        bench = shlex.quote(self.cfg.bench_path)
        cmd = [
            "bench",
            "--site",
            site,
            "restore",
            db_path,
            "--with-public-files",
            public_files_tar,
            "--force",
        ]
        if private_files_tar:
            cmd += ["--with-private-files", private_files_tar]
        # Use shlex.join for safe quoting in remote bash -lc.
        script = f"cd {bench} && {shlex.join(cmd)}"
        self._run(script, dry_run=dry_run, check=True)



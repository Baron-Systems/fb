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

    def require_remote_bin(self, name: str, *, dry_run: bool) -> None:
        """
        Best-effort remote check for a binary.
        """
        if not name or any(c.isspace() for c in name):
            raise FBError("Invalid binary name.", exit_code=2)
        self._run(f"command -v {shlex.quote(name)} >/dev/null 2>&1", dry_run=dry_run, check=True)

    def ping(self, *, dry_run: bool) -> None:
        self._run("true", dry_run=dry_run, check=True)

    def bench_backup_with_files(self, site: str, *, dry_run: bool) -> None:
        site = validate_site_name(site)
        bench = shlex.quote(self.cfg.bench_path)
        if self.cfg.remote_mode == "bench":
            # bench itself is on the remote system; fb never needs it locally
            script = f"cd {bench} && bench --site {shlex.quote(site)} backup --with-files"
            self._run(script, dry_run=dry_run, check=True)
            return

        # Docker mode: run inside container
        if self.cfg.remote_mode == "docker":
            container = _validate_container(self.cfg.docker_container)
            inner = f"cd {bench} && bench --site {shlex.quote(site)} backup --with-files"
            script = f"docker exec {shlex.quote(container)} bash -lc {shlex.quote(inner)}"
            self._run(script, dry_run=dry_run, check=True)
            return

        # Frappe Manager mode: run bench inside fm-managed environment
        # In fm mode, we don't run backup here to avoid double-running; see latest_backup_paths()
        # which performs backup+export staging in one fm shell command.
        return

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
        if self.cfg.remote_mode == "bench":
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
        elif self.cfg.remote_mode == "fm":
            export_root = _validate_abs_dir(self.cfg.fm_export_dir, "FRAPPE_FM_EXPORT_DIR")
            export_site = f"{export_root.rstrip('/')}/{site}"

            # Single fm shell command: run backup, find newest artifacts, copy them to export dir.
            inner = (
                f"set -euo pipefail; "
                f"bench --site {shlex.quote(site)} backup --with-files; "
                f"cd {bench}; "
                f"bdir={shlex.quote(base)}; "
                "db=$(ls -1t \"$bdir\"/*_database.sql.gz 2>/dev/null | head -n1 || true); "
                "pub=$(ls -1t \"$bdir\"/*_files.tar 2>/dev/null | head -n1 || true); "
                "priv=$(ls -1t \"$bdir\"/*_private-files.tar 2>/dev/null | head -n1 || true); "
                "[ -n \"$db\" ] || { echo 'ERR=Missing DB artifact'; exit 1; }; "
                "[ -n \"$pub\" ] || { echo 'ERR=Missing public files artifact'; exit 1; }; "
                f"mkdir -p {shlex.quote(export_site)}; "
                f"cp -f -- \"$db\" {shlex.quote(export_site)}/; "
                f"cp -f -- \"$pub\" {shlex.quote(export_site)}/; "
                "if [ -n \"$priv\" ]; then "
                f"cp -f -- \"$priv\" {shlex.quote(export_site)}/; "
                "fi; "
                "dbb=${db##*/}; pubb=${pub##*/}; privb=${priv##*/}; "
                f"printf 'DB={shlex.quote(export_site)}/%s\\nPUB={shlex.quote(export_site)}/%s\\nPRIV={shlex.quote(export_site)}/%s\\n' "
                "\"$dbb\" \"$pubb\" \"$privb\""
            )
            script = f"fm shell {shlex.quote(site)} -c {shlex.quote(inner)}"
        else:
            container = _validate_container(self.cfg.docker_container)
            # Discover newest artifacts inside container, then stage them onto the host /tmp
            # using docker cp, so fb can pull them via rsync.
            inner = (
                f"cd {bench} && "
                f"db=$(ls -1t {shlex.quote(base)}/*_database.sql.gz 2>/dev/null | head -n1 || true); "
                f"pub=$(ls -1t {shlex.quote(base)}/*_files.tar 2>/dev/null | head -n1 || true); "
                f"priv=$(ls -1t {shlex.quote(base)}/*_private-files.tar 2>/dev/null | head -n1 || true); "
                f"printf 'DB=%s\\nPUB=%s\\nPRIV=%s\\n' \"$db\" \"$pub\" \"$priv\""
            )
            # We run docker exec to get the paths, then docker cp to stage.
            script = (
                "set -euo pipefail; "
                f"tmp=$(mktemp -d -p /tmp fb-stage.XXXXXX); "
                f"out=$(docker exec {shlex.quote(container)} bash -lc {shlex.quote(inner)}); "
                "db=$(printf '%s\n' \"$out\" | sed -n 's/^DB=//p'); "
                "pub=$(printf '%s\n' \"$out\" | sed -n 's/^PUB=//p'); "
                "priv=$(printf '%s\n' \"$out\" | sed -n 's/^PRIV=//p'); "
                "[ -n \"$db\" ] || { echo 'ERR=Missing DB artifact'; exit 1; }; "
                "[ -n \"$pub\" ] || { echo 'ERR=Missing public files artifact'; exit 1; }; "
                "dbb=${db##*/}; pubb=${pub##*/}; "
                f"docker cp {shlex.quote(container)}:\"$db\" \"$tmp/$dbb\"; "
                f"docker cp {shlex.quote(container)}:\"$pub\" \"$tmp/$pubb\"; "
                "privb=''; "
                "if [ -n \"$priv\" ]; then privb=${priv##*/}; "
                f"docker cp {shlex.quote(container)}:\"$priv\" \"$tmp/$privb\"; "
                "fi; "
                "printf 'STAGED=%s\nDB=%s/%s\nPUB=%s/%s\nPRIV=%s/%s\n' "
                "\"$tmp\" \"$tmp\" \"$dbb\" \"$tmp\" \"$pubb\" \"$tmp\" \"$privb\""
            )
        res = self._run(script, dry_run=dry_run, check=True)
        if dry_run:
            return {"stage_dir": None, "db": None, "public": None, "private": None}

        kv: dict[str, str] = {}
        for line in res.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        stage = kv.get("STAGED", "")
        db = kv.get("DB", "")
        pub = kv.get("PUB", "")
        priv = kv.get("PRIV", "")
        if not db or not pub:
            raise FBError(
                f"Remote backup artifacts not found for site '{site}'. "
                "Expected *_database.sql.gz and *_files.tar in sites/<SITE>/private/backups/.",
                exit_code=1,
            )
        return {"stage_dir": stage or None, "db": db, "public": pub, "private": priv or None}

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
        if self.cfg.remote_mode == "bench":
            script = f"cd {bench} && bench --site {shlex.quote(site)} set-maintenance-mode {mode}"
            self._run(script, dry_run=dry_run, check=True)
            return
        if self.cfg.remote_mode == "docker":
            container = _validate_container(self.cfg.docker_container)
            inner = f"cd {bench} && bench --site {shlex.quote(site)} set-maintenance-mode {mode}"
            script = f"docker exec {shlex.quote(container)} bash -lc {shlex.quote(inner)}"
            self._run(script, dry_run=dry_run, check=True)
            return

        inner = f"bench --site {shlex.quote(site)} set-maintenance-mode {mode}"
        script = f"fm shell {shlex.quote(site)} -c {shlex.quote(inner)}"
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
        if self.cfg.remote_mode == "bench":
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
            script = f"cd {bench} && {shlex.join(cmd)}"
            self._run(script, dry_run=dry_run, check=True)
            return

        # Docker mode:
        # - db_path/public_files_tar/private_files_tar are HOST paths (uploaded via rsync to /tmp)
        # - copy them into container temp dir via docker cp
        # - run bench restore inside container using container paths
        if self.cfg.remote_mode == "docker":
            container = _validate_container(self.cfg.docker_container)
            priv = private_files_tar or ""
            script = (
                "set -euo pipefail; "
                f"ctmp=$(docker exec {shlex.quote(container)} bash -lc 'mktemp -d -p /tmp fb-restore.XXXXXX'); "
                f"docker cp {shlex.quote(db_path)} {shlex.quote(container)}:\"$ctmp/\"; "
                f"docker cp {shlex.quote(public_files_tar)} {shlex.quote(container)}:\"$ctmp/\"; "
                "privname=''; "
                f"if [ -n {shlex.quote(priv)} ]; then docker cp {shlex.quote(priv)} {shlex.quote(container)}:\"$ctmp/\"; privname=$(basename {shlex.quote(priv)}); fi; "
                f"dbname=$(basename {shlex.quote(db_path)}); pubname=$(basename {shlex.quote(public_files_tar)}); "
                f"inner_cmd='cd {bench} && bench --site {shlex.quote(site)} restore \"$ctmp/$dbname\" --with-public-files \"$ctmp/$pubname\" "
                + ("--with-private-files \"$ctmp/$privname\" " if private_files_tar else "")
                + "--force'; "
                f"docker exec {shlex.quote(container)} bash -lc \"$inner_cmd\""
            )
            self._run(script, dry_run=dry_run, check=True)
            return

        # fm mode: delegate restore to fm shell (fm is expected to provide bench environment)
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
        inner = shlex.join(cmd)
        script = f"fm shell {shlex.quote(site)} -c {shlex.quote(inner)}"
        self._run(script, dry_run=dry_run, check=True)


def _validate_container(container: Optional[str]) -> str:
    if not container:
        raise FBError("FRAPPE_DOCKER_CONTAINER is required for docker mode.", exit_code=2)
    # conservative validation
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if any(c not in allowed for c in container) or container[0] == "-":
        raise FBError("Invalid FRAPPE_DOCKER_CONTAINER.", exit_code=2)
    return container


def _validate_fm_bench(bench: Optional[str]) -> str:
    # Deprecated (kept for backward compatibility): older fm mode used an explicit bench name.
    if not bench:
        raise FBError("FRAPPE_REMOTE_BENCH is required for this operation.", exit_code=2)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if any(c not in allowed for c in bench) or bench[0] == "-":
        raise FBError("Invalid FRAPPE_REMOTE_BENCH.", exit_code=2)
    return bench


def _validate_abs_dir(path: Optional[str], key: str) -> str:
    if not path:
        raise FBError(f"{key} is required.", exit_code=2)
    if not path.startswith("/") or any(c.isspace() for c in path) or "\x00" in path:
        raise FBError(f"{key} must be an absolute path without whitespace.", exit_code=2)
    if "//" in path or "/../" in path or path.endswith("/.."):
        raise FBError(f"{key} contains an unsafe path.", exit_code=2)
    return path.rstrip("/") or "/"



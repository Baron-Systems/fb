from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from .backup_engine import run_backup_for_site
from .config import (
    CONFIG_ENV_KEYS,
    config_set,
    config_unset,
    default_config_dir,
    default_config_path,
    default_sites_path,
    load_config,
    read_config_file,
)
from .metadata import read_last_run
from .notifications import telegram_send
from .remote import Remote
from .restore import restore_site
from .sites import get_site, load_sites, site_add, site_edit, site_remove
from .utils import FBError, configure_logging, list_date_dirs, parse_date_yyyy_mm_dd, require_bin, validate_site_name
from .verify import verify_backup_dir


@dataclass(frozen=True)
class Ctx:
    dry_run: bool
    verbose: bool


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _die(e: FBError) -> int:
    _print_err(f"ERROR: {e}")
    return e.exit_code


def _cmd_init(ctx: Ctx) -> int:
    cfg_dir = default_config_dir()
    cfg_path = default_config_path()
    sites_path = default_sites_path()

    if ctx.dry_run:
        print(f"DRY-RUN: would create {cfg_dir}")
    else:
        cfg_dir.mkdir(parents=True, exist_ok=True)

    if not cfg_path.exists():
        template = (
            "# fb config\n"
            "# You can also set these via environment variables.\n"
            "#\n"
            "# FRAPPE_REMOTE_MODE = \"bench\"  # bench|docker|fm\n"
            "# FRAPPE_REMOTE_HOST = \"\"\n"
            "# FRAPPE_REMOTE_USER = \"frappe\"\n"
            "# FRAPPE_BENCH_PATH = \"/home/frappe/frappe-bench\"\n"
            "# FRAPPE_LOCAL_BACKUP_ROOT = \"/data/frappe-backups\"\n"
            "# FRAPPE_DOCKER_CONTAINER = \"\"  # required if FRAPPE_REMOTE_MODE=docker\n"
            "# FRAPPE_REMOTE_BENCH = \"\"  # required if FRAPPE_REMOTE_MODE=fm\n"
            "# FRAPPE_FM_EXPORT_DIR = \"/workspace/exports\"  # required if FRAPPE_REMOTE_MODE=fm\n"
            "# TELEGRAM_TOKEN = \"\"\n"
            "# TELEGRAM_CHAT_ID = \"\"\n"
        )
        if ctx.dry_run:
            print(f"DRY-RUN: would write {cfg_path}")
        else:
            cfg_path.write_text(template, encoding="utf-8")
            cfg_path.chmod(0o600)

    if not sites_path.exists():
        template = "# SITE  RETENTION_DAYS\n"
        if ctx.dry_run:
            print(f"DRY-RUN: would write {sites_path}")
        else:
            sites_path.write_text(template, encoding="utf-8")
            sites_path.chmod(0o600)

    print(f"Initialized at {cfg_dir}")
    return 0


def _cmd_list(ctx: Ctx) -> int:
    _ = ctx
    entries = load_sites()
    if not entries:
        print("(no sites configured)")
        return 0
    print("SITE\tRETENTION_DAYS")
    for e in entries:
        print(f"{e.site}\t{e.retention_days}")
    return 0


def _cmd_site_add(ctx: Ctx, site: str, retention: int) -> int:
    validate_site_name(site)
    if retention <= 0:
        return _die(FBError("RETENTION must be > 0", exit_code=2))
    if ctx.dry_run:
        print(f"DRY-RUN: would add {site} retention={retention}")
        return 0
    site_add(site, retention)
    print(f"Added {site} retention={retention}")
    return 0


def _cmd_site_remove(ctx: Ctx, site: str) -> int:
    validate_site_name(site)
    if ctx.dry_run:
        print(f"DRY-RUN: would remove {site}")
        return 0
    site_remove(site)
    print(f"Removed {site}")
    return 0


def _cmd_site_edit(ctx: Ctx, site: str, retention: int) -> int:
    validate_site_name(site)
    if retention <= 0:
        return _die(FBError("RETENTION must be > 0", exit_code=2))
    if ctx.dry_run:
        print(f"DRY-RUN: would edit {site} retention={retention}")
        return 0
    site_edit(site, retention)
    print(f"Updated {site} retention={retention}")
    return 0


def _cmd_config_show(ctx: Ctx) -> int:
    _ = ctx
    cfg = load_config()
    print("KEY\tVALUE")
    for k, v in cfg.as_env_mapping(redact=True).items():
        print(f"{k}\t{v}")
    return 0


def _cmd_config_check(ctx: Ctx) -> int:
    _ = ctx
    cfg = load_config()
    for b in ["ssh", "rsync", "tar", "gzip"]:
        require_bin(b)
    if cfg.remote_mode == "docker":
        # Docker binary required on remote host; we also ensure local ssh client exists (above).
        # This does not validate remote permissions, but will fail fast in fb test.
        pass
    print("OK")
    return 0


def _cmd_config_get(ctx: Ctx, key: str) -> int:
    _ = ctx
    key = key.strip()
    if key not in CONFIG_ENV_KEYS:
        return _die(FBError(f"Unknown key: {key}", exit_code=2))
    values = read_config_file()
    if key not in values:
        return 1
    if key == "TELEGRAM_TOKEN":
        print("<redacted>")
    else:
        print(str(values[key]))
    return 0


def _cmd_config_set(ctx: Ctx, key: str, value: str) -> int:
    key = key.strip()
    if key not in CONFIG_ENV_KEYS:
        return _die(FBError(f"Unknown key: {key}", exit_code=2))
    if ctx.dry_run:
        print(f"DRY-RUN: would set {key}")
        return 0
    config_set(key, value)
    print(f"Set {key}")
    return 0


def _cmd_config_unset(ctx: Ctx, key: str) -> int:
    key = key.strip()
    if key not in CONFIG_ENV_KEYS:
        return _die(FBError(f"Unknown key: {key}", exit_code=2))
    if ctx.dry_run:
        print(f"DRY-RUN: would unset {key}")
        return 0
    config_unset(key)
    print(f"Unset {key}")
    return 0


def _cmd_backup(ctx: Ctx, site: Optional[str]) -> int:
    cfg = load_config()
    entries = load_sites()
    if site:
        entries = [get_site(site)]
    if not entries:
        return _die(FBError("No sites configured. Use: fb site add SITE RETENTION", exit_code=2))

    ok_all = True
    for e in entries:
        try:
            res = run_backup_for_site(cfg, e, dry_run=ctx.dry_run)
            print(f"OK\t{res.site}\t{res.date}\t{res.local_dir}")
        except Exception as ex:
            ok_all = False
            msg = str(ex)
            _print_err(f"FAILED\t{e.site}\t{msg}")
            try:
                telegram_send(cfg, f"fb backup FAILED for {e.site}: {msg}", dry_run=ctx.dry_run)
            except Exception:
                pass
    return 0 if ok_all else 1


def _cmd_verify(ctx: Ctx, site: Optional[str], date: Optional[str]) -> int:
    cfg = load_config()
    entries = load_sites()
    if site:
        entries = [get_site(site)]
    if not entries:
        return _die(FBError("No sites configured.", exit_code=2))

    ok_all = True
    for e in entries:
        site_root = Path(cfg.local_backup_root) / e.site
        if date:
            parse_date_yyyy_mm_dd(date)
            backup_dir = site_root / date
            r = verify_backup_dir(e.site, date, backup_dir, dry_run=ctx.dry_run)
            ok_all = ok_all and r.ok
            print(("OK" if r.ok else "FAILED") + f"\t{e.site}\t{date}\t{r.message}")
        else:
            dirs = list_date_dirs(site_root)
            if not dirs:
                ok_all = False
                print(f"FAILED\t{e.site}\t-\tno backups found in {site_root}")
                continue
            latest = dirs[-1]
            r = verify_backup_dir(e.site, latest.name, latest, dry_run=ctx.dry_run)
            ok_all = ok_all and r.ok
            print(("OK" if r.ok else "FAILED") + f"\t{e.site}\t{latest.name}\t{r.message}")

    return 0 if ok_all else 1


def _cmd_restore(ctx: Ctx, site: str, date: str, confirm: bool) -> int:
    cfg = load_config()
    parse_date_yyyy_mm_dd(date)
    site = validate_site_name(site)
    get_site(site)  # must be configured
    local_dir = Path(cfg.local_backup_root) / site / date
    r = restore_site(cfg, site=site, date=date, local_backup_dir=local_dir, confirm=confirm, dry_run=ctx.dry_run)
    print(f"OK\t{r.site}\t{r.date}\t{r.message}")
    return 0


def _cmd_status(ctx: Ctx) -> int:
    _ = ctx
    cfg = load_config()
    entries = load_sites()
    if not entries:
        return _die(FBError("No sites configured.", exit_code=2))
    print("SITE\tLAST_DATE\tSTATUS\tMESSAGE")
    for e in entries:
        site_root = Path(cfg.local_backup_root) / e.site
        meta = read_last_run(site_root)
        if not meta:
            print(f"{e.site}\t-\tunknown\tno metadata")
        else:
            msg = str(meta.get("message", ""))[:120]
            print(f"{e.site}\t{meta.get('date','-')}\t{meta.get('status','-')}\t{msg}")
    return 0


def _cmd_test(ctx: Ctx) -> int:
    cfg = load_config()
    for b in ["ssh", "rsync", "tar", "gzip"]:
        require_bin(b)
    r = Remote(cfg)
    r.ping(dry_run=ctx.dry_run)
    if cfg.remote_mode == "docker" and not ctx.dry_run:
        # Best-effort remote check that docker is available.
        r.require_remote_bin("docker", dry_run=False)
    if cfg.remote_mode == "fm" and not ctx.dry_run:
        # Best-effort remote check that fm is available.
        r.require_remote_bin("fm", dry_run=False)
    print("OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fb", description="Frappe Backup (fb) - pull-based backups over SSH+rsync")
    p.add_argument("--dry-run", action="store_true", help="Print what would happen; execute nothing")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print version")
    sub.add_parser("init", help="Initialize config directory and registry")
    sub.add_parser("list", help="List configured sites")

    p_site = sub.add_parser("site", help="Manage sites registry")
    sub_site = p_site.add_subparsers(dest="site_cmd", required=True)
    a = sub_site.add_parser("add", help="Add site")
    a.add_argument("site")
    a.add_argument("retention", type=int)
    r = sub_site.add_parser("remove", help="Remove site")
    r.add_argument("site")
    e = sub_site.add_parser("edit", help="Edit site retention")
    e.add_argument("site")
    e.add_argument("retention", type=int)

    p_cfg = sub.add_parser("config", help="Manage config")
    sub_cfg = p_cfg.add_subparsers(dest="cfg_cmd", required=True)
    sub_cfg.add_parser("show", help="Show effective config (redacted)")
    sub_cfg.add_parser("check", help="Check config and required tools")
    g = sub_cfg.add_parser("get", help="Get key (from config file)")
    g.add_argument("key")
    s = sub_cfg.add_parser("set", help="Set key in config file")
    s.add_argument("key")
    s.add_argument("value")
    u = sub_cfg.add_parser("unset", help="Unset key in config file")
    u.add_argument("key")

    b = sub.add_parser("backup", help="Run remote bench backup and pull artifacts")
    b.add_argument("--site", dest="site", default=None)

    v = sub.add_parser("verify", help="Verify backups locally")
    v.add_argument("--site", dest="site", default=None)
    v.add_argument("--date", dest="date", default=None)

    rs = sub.add_parser("restore", help="Restore a backup to remote host (verify-first)")
    rs.add_argument("--site", required=True)
    rs.add_argument("--date", required=True)
    rs.add_argument("--confirm", action="store_true", help="Required for non-dry-run restore")

    sub.add_parser("status", help="Show last run per site")
    sub.add_parser("test", help="Operational readiness test")
    return p


def run(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Ctx(dry_run=bool(args.dry_run), verbose=bool(args.verbose))
    configure_logging(verbose=ctx.verbose)

    try:
        if args.cmd == "version":
            print(__version__)
            return 0
        if args.cmd == "init":
            return _cmd_init(ctx)
        if args.cmd == "list":
            return _cmd_list(ctx)
        if args.cmd == "site":
            if args.site_cmd == "add":
                return _cmd_site_add(ctx, args.site, args.retention)
            if args.site_cmd == "remove":
                return _cmd_site_remove(ctx, args.site)
            if args.site_cmd == "edit":
                return _cmd_site_edit(ctx, args.site, args.retention)
        if args.cmd == "config":
            if args.cfg_cmd == "show":
                return _cmd_config_show(ctx)
            if args.cfg_cmd == "check":
                return _cmd_config_check(ctx)
            if args.cfg_cmd == "get":
                return _cmd_config_get(ctx, args.key)
            if args.cfg_cmd == "set":
                return _cmd_config_set(ctx, args.key, args.value)
            if args.cfg_cmd == "unset":
                return _cmd_config_unset(ctx, args.key)
        if args.cmd == "backup":
            return _cmd_backup(ctx, args.site)
        if args.cmd == "verify":
            return _cmd_verify(ctx, args.site, args.date)
        if args.cmd == "restore":
            return _cmd_restore(ctx, args.site, args.date, bool(args.confirm))
        if args.cmd == "status":
            return _cmd_status(ctx)
        if args.cmd == "test":
            return _cmd_test(ctx)
        return 2
    except FBError as e:
        return _die(e)


def main() -> None:
    sys.exit(run())



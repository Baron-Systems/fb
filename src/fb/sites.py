from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .config import default_sites_path
from .utils import FBError, atomic_write_text, validate_site_name


@dataclass(frozen=True)
class SiteEntry:
    site: str
    retention_days: int


def parse_sites_conf(text: str) -> list[SiteEntry]:
    entries: list[SiteEntry] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise FBError(f"Invalid sites.conf line {i}: expected 'SITE RETENTION_DAYS'", exit_code=2)
        site = validate_site_name(parts[0])
        try:
            retention = int(parts[1])
        except ValueError as e:
            raise FBError(f"Invalid retention days on line {i}.", exit_code=2) from e
        if retention <= 0 or retention > 3650:
            raise FBError(f"Invalid retention days on line {i} (1..3650).", exit_code=2)
        entries.append(SiteEntry(site=site, retention_days=retention))

    # unique by site
    seen: set[str] = set()
    out: list[SiteEntry] = []
    for e in entries:
        if e.site in seen:
            raise FBError(f"Duplicate site in sites.conf: {e.site}", exit_code=2)
        seen.add(e.site)
        out.append(e)
    return out


def format_sites_conf(entries: Iterable[SiteEntry]) -> str:
    lines = ["# SITE  RETENTION_DAYS"]
    for e in sorted(entries, key=lambda x: x.site):
        lines.append(f"{e.site}\t{e.retention_days}")
    return "\n".join(lines) + "\n"


def load_sites(sites_path: Optional[Path] = None) -> list[SiteEntry]:
    sites_path = sites_path or default_sites_path()
    if not sites_path.exists():
        return []
    return parse_sites_conf(sites_path.read_text(encoding="utf-8"))


def write_sites(entries: list[SiteEntry], sites_path: Optional[Path] = None) -> None:
    sites_path = sites_path or default_sites_path()
    sites_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(sites_path, format_sites_conf(entries), mode=0o600)


def site_add(site: str, retention_days: int, *, sites_path: Optional[Path] = None) -> None:
    site = validate_site_name(site)
    if retention_days <= 0 or retention_days > 3650:
        raise FBError("RETENTION must be 1..3650 days.", exit_code=2)
    entries = load_sites(sites_path)
    if any(e.site == site for e in entries):
        raise FBError(f"Site already exists: {site}", exit_code=2)
    entries.append(SiteEntry(site=site, retention_days=retention_days))
    write_sites(entries, sites_path)


def site_remove(site: str, *, sites_path: Optional[Path] = None) -> None:
    site = validate_site_name(site)
    entries = load_sites(sites_path)
    new = [e for e in entries if e.site != site]
    if len(new) == len(entries):
        raise FBError(f"Site not found: {site}", exit_code=2)
    write_sites(new, sites_path)


def site_edit(site: str, retention_days: int, *, sites_path: Optional[Path] = None) -> None:
    site = validate_site_name(site)
    if retention_days <= 0 or retention_days > 3650:
        raise FBError("RETENTION must be 1..3650 days.", exit_code=2)
    entries = load_sites(sites_path)
    found = False
    out: list[SiteEntry] = []
    for e in entries:
        if e.site == site:
            out.append(SiteEntry(site=site, retention_days=retention_days))
            found = True
        else:
            out.append(e)
    if not found:
        raise FBError(f"Site not found: {site}", exit_code=2)
    write_sites(out, sites_path)


def get_site(site: str, *, sites_path: Optional[Path] = None) -> SiteEntry:
    site = validate_site_name(site)
    for e in load_sites(sites_path):
        if e.site == site:
            return e
    raise FBError(f"Site not found: {site}", exit_code=2)



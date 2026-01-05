from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .utils import FBError, atomic_write_text, redact_secret

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


CONFIG_ENV_KEYS = [
    "FRAPPE_REMOTE_MODE",
    "FRAPPE_REMOTE_HOST",
    "FRAPPE_REMOTE_USER",
    "FRAPPE_BENCH_PATH",
    "FRAPPE_LOCAL_BACKUP_ROOT",
    "FRAPPE_DOCKER_CONTAINER",
    "FRAPPE_REMOTE_BENCH",
    "FRAPPE_FM_EXPORT_DIR",
    "FRAPPE_FM_TRANSPORT",
    "FRAPPE_FM_BIN",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
]

ALLOWED_MODES = ["bench", "docker", "fm"]


def default_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "fb"
    return Path.home() / ".config" / "fb"


def default_config_path() -> Path:
    return default_config_dir() / "config.toml"


def default_sites_path() -> Path:
    return default_config_dir() / "sites.conf"


def default_local_backup_root() -> Path:
    return Path("/data/frappe-backups")


def _safe_abs_path(value: str, key: str) -> str:
    value = value.strip()
    if not value.startswith("/"):
        raise FBError(f"{key} must be an absolute path.", exit_code=2)
    if any(c.isspace() for c in value):
        raise FBError(f"{key} must not contain whitespace.", exit_code=2)
    if "\x00" in value or "//" in value or "/../" in value or value.endswith("/.."):
        raise FBError(f"{key} contains an unsafe path.", exit_code=2)
    return value.rstrip("/") or "/"


@dataclass(frozen=True)
class Config:
    remote_mode: str
    remote_host: str
    remote_user: str
    bench_path: str
    local_backup_root: str
    docker_container: Optional[str] = None
    remote_bench: Optional[str] = None
    fm_export_dir: Optional[str] = None
    fm_transport: str = "export"
    fm_bin: str = "/home/baron/.local/bin/fm"
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @staticmethod
    def from_mapping(m: dict[str, Any]) -> "Config":
        mode = str(m.get("FRAPPE_REMOTE_MODE", "bench")).strip().lower() or "bench"
        if mode not in ALLOWED_MODES:
            raise FBError("FRAPPE_REMOTE_MODE must be one of: bench, docker, fm.", exit_code=2)

        host = str(m.get("FRAPPE_REMOTE_HOST", "")).strip()
        if not host:
            raise FBError("FRAPPE_REMOTE_HOST is required.", exit_code=2)

        user = str(m.get("FRAPPE_REMOTE_USER", "frappe")).strip() or "frappe"
        bench = str(m.get("FRAPPE_BENCH_PATH", "")).strip()
        if not bench:
            raise FBError("FRAPPE_BENCH_PATH is required.", exit_code=2)
        bench = _safe_abs_path(bench, "FRAPPE_BENCH_PATH")

        local_root = str(m.get("FRAPPE_LOCAL_BACKUP_ROOT", str(default_local_backup_root()))).strip()
        local_root = _safe_abs_path(local_root, "FRAPPE_LOCAL_BACKUP_ROOT")

        docker_container = str(m.get("FRAPPE_DOCKER_CONTAINER", "")).strip() or None
        if mode == "docker" and not docker_container:
            raise FBError("FRAPPE_DOCKER_CONTAINER is required when FRAPPE_REMOTE_MODE=docker.", exit_code=2)

        # Optional: some fm setups identify benches by name; other setups use site context.
        # In this repo, fm mode defaults to using the SITE as the fm shell target.
        remote_bench = str(m.get("FRAPPE_REMOTE_BENCH", "")).strip() or None

        fm_export_dir = str(m.get("FRAPPE_FM_EXPORT_DIR", "")).strip() or None
        fm_transport = str(m.get("FRAPPE_FM_TRANSPORT", "export")).strip().lower() or "export"
        fm_bin = str(m.get("FRAPPE_FM_BIN", "/home/baron/.local/bin/fm")).strip() or "/home/baron/.local/bin/fm"
        if mode == "fm":
            if fm_transport not in ("export", "stream"):
                raise FBError("FRAPPE_FM_TRANSPORT must be 'export' or 'stream'.", exit_code=2)
            fm_bin = _safe_abs_path(fm_bin, "FRAPPE_FM_BIN")
            if fm_transport == "export":
                if not fm_export_dir:
                    raise FBError("FRAPPE_FM_EXPORT_DIR is required when FRAPPE_REMOTE_MODE=fm and transport=export.", exit_code=2)
                fm_export_dir = _safe_abs_path(fm_export_dir, "FRAPPE_FM_EXPORT_DIR")
            else:
                # stream mode does not need an export dir
                fm_export_dir = None

        token = str(m.get("TELEGRAM_TOKEN", "")).strip() or None
        chat = str(m.get("TELEGRAM_CHAT_ID", "")).strip() or None

        return Config(
            remote_mode=mode,
            remote_host=host,
            remote_user=user,
            bench_path=bench,
            local_backup_root=local_root,
            docker_container=docker_container,
            remote_bench=remote_bench,
            fm_export_dir=fm_export_dir,
            fm_transport=fm_transport,
            fm_bin=fm_bin,
            telegram_token=token,
            telegram_chat_id=chat,
        )

    def as_env_mapping(self, *, redact: bool) -> dict[str, str]:
        out: dict[str, str] = {
            "FRAPPE_REMOTE_MODE": self.remote_mode,
            "FRAPPE_REMOTE_HOST": self.remote_host,
            "FRAPPE_REMOTE_USER": self.remote_user,
            "FRAPPE_BENCH_PATH": self.bench_path,
            "FRAPPE_LOCAL_BACKUP_ROOT": self.local_backup_root,
        }
        if self.docker_container:
            out["FRAPPE_DOCKER_CONTAINER"] = self.docker_container
        if self.remote_bench:
            out["FRAPPE_REMOTE_BENCH"] = self.remote_bench
        if self.fm_export_dir:
            out["FRAPPE_FM_EXPORT_DIR"] = self.fm_export_dir
        out["FRAPPE_FM_TRANSPORT"] = self.fm_transport
        out["FRAPPE_FM_BIN"] = self.fm_bin
        if self.telegram_token:
            out["TELEGRAM_TOKEN"] = redact_secret(self.telegram_token) if redact else self.telegram_token
        if self.telegram_chat_id:
            out["TELEGRAM_CHAT_ID"] = self.telegram_chat_id
        return out


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Precedence:
    1) Environment variables (FRAPPE_*, TELEGRAM_*)
    2) config.toml (same keys)
    """
    config_path = config_path or default_config_path()
    file_values: dict[str, Any] = {}
    if config_path.exists():
        try:
            raw = config_path.read_bytes()
            file_values = tomllib.loads(raw.decode("utf-8"))
        except Exception as e:
            raise FBError(f"Failed to read config: {config_path}: {e}", exit_code=2) from e

    env_values: dict[str, Any] = {}
    for k in CONFIG_ENV_KEYS:
        if k in os.environ and os.environ[k].strip() != "":
            env_values[k] = os.environ[k]

    merged = dict(file_values)
    merged.update(env_values)
    return Config.from_mapping(merged)


def read_config_file(config_path: Optional[Path] = None) -> dict[str, Any]:
    config_path = config_path or default_config_path()
    if not config_path.exists():
        return {}
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise FBError(f"Failed to read config: {config_path}: {e}", exit_code=2) from e


def _toml_quote(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def write_config_file(values: dict[str, Any], config_path: Optional[Path] = None) -> None:
    config_path = config_path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # minimal TOML writer for flat keys only (by design)
    lines: list[str] = []
    for k in sorted(values.keys()):
        v = values[k]
        if v is None:
            continue
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f"{k} = {_toml_quote(str(v))}")
    atomic_write_text(config_path, "\n".join(lines) + "\n", mode=0o600)


def config_set(key: str, value: str, *, config_path: Optional[Path] = None) -> None:
    key = key.strip()
    if key not in CONFIG_ENV_KEYS:
        raise FBError(f"Unknown config key: {key}", exit_code=2)
    values = read_config_file(config_path)
    values[key] = value
    write_config_file(values, config_path)


def config_unset(key: str, *, config_path: Optional[Path] = None) -> None:
    key = key.strip()
    if key not in CONFIG_ENV_KEYS:
        raise FBError(f"Unknown config key: {key}", exit_code=2)
    values = read_config_file(config_path)
    values.pop(key, None)
    write_config_file(values, config_path)



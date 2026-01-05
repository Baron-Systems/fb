from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


LOG = logging.getLogger("fb")


class FBError(RuntimeError):
    """Base error with an exit code suitable for CLI usage."""

    def __init__(self, message: str, *, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


SITE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_site_name(site: str) -> str:
    site = site.strip()
    if not site or not SITE_RE.fullmatch(site):
        raise FBError(
            f"Invalid site name '{site}'. Allowed: letters/digits and . _ - (no spaces/slashes).",
            exit_code=2,
        )
    if ".." in site:
        raise FBError(f"Invalid site name '{site}'.", exit_code=2)
    return site


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path, *, dry_run: bool) -> None:
    if dry_run:
        LOG.info("DRY-RUN mkdir -p %s", path)
        return
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tf:
        tf.write(content)
        temp_name = tf.name
    os.chmod(temp_name, mode)
    os.replace(temp_name, path)


def atomic_write_json(path: Path, data: object, *, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=mode)


@dataclass(frozen=True)
class CmdResult:
    argv: list[str]
    stdout: str
    stderr: str
    returncode: int


def run_cmd(
    argv: list[str],
    *,
    dry_run: bool,
    check: bool = True,
    capture: bool = True,
    env: Optional[dict[str, str]] = None,
) -> CmdResult:
    if dry_run:
        LOG.info("DRY-RUN %s", shlex.join(argv))
        return CmdResult(argv=argv, stdout="", stderr="", returncode=0)

    LOG.debug("exec: %s", shlex.join(argv))
    proc = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    if check and proc.returncode != 0:
        raise FBError(
            f"Command failed ({proc.returncode}): {shlex.join(argv)}\n{err}".rstrip(),
            exit_code=1,
        )
    return CmdResult(argv=argv, stdout=out, stderr=err, returncode=proc.returncode)


def run_pipe(
    argv_left: list[str],
    argv_right: list[str],
    *,
    dry_run: bool,
    check: bool = True,
    stdin_left: Optional[bytes] = None,
) -> int:
    """
    Run `argv_left | argv_right` with streaming pipes.
    Returns right process exit code.
    """
    import shlex as _shlex

    if dry_run:
        LOG.info("DRY-RUN %s | %s", _shlex.join(argv_left), _shlex.join(argv_right))
        return 0

    LOG.debug("pipe: %s | %s", _shlex.join(argv_left), _shlex.join(argv_right))
    left = subprocess.Popen(
        argv_left,
        stdin=subprocess.PIPE if stdin_left is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    try:
        right = subprocess.Popen(argv_right, stdin=left.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        assert left.stdout is not None
        left.stdout.close()  # allow left to receive SIGPIPE if right exits
        if stdin_left is not None:
            assert left.stdin is not None
            left.stdin.write(stdin_left)
            left.stdin.close()
        out_r, err_r = right.communicate()
        out_l, err_l = left.communicate()
    finally:
        try:
            left.kill()
        except Exception:
            pass

    if check:
        # Prefer reporting right-side failure first; it's commonly the root cause (disk/perm/extract errors),
        # and left side then sees SIGPIPE and exits non-zero.
        if right.returncode not in (0, None):
            msg = []
            if out_r:
                msg.append("stdout:\n" + out_r.decode(errors="replace").rstrip())
            if err_r:
                msg.append("stderr:\n" + err_r.decode(errors="replace").rstrip())
            # include left stderr for context (often contains remote command output)
            if err_l:
                msg.append("left_stderr:\n" + err_l.decode(errors="replace").rstrip())
            raise FBError(
                (
                    f"Pipe right command failed ({right.returncode}): {_shlex.join(argv_right)}\n"
                    + ("\n".join(msg) if msg else "(no output)")
                ).rstrip(),
                exit_code=1,
            )
        if left.returncode not in (0, None):
            msg = []
            if out_l:
                msg.append("stdout:\n" + out_l.decode(errors="replace").rstrip())
            if err_l:
                msg.append("stderr:\n" + err_l.decode(errors="replace").rstrip())
            raise FBError(
                (
                    f"Pipe left command failed ({left.returncode}): {_shlex.join(argv_left)}\n"
                    + ("\n".join(msg) if msg else "(no output)")
                ).rstrip(),
                exit_code=1,
            )

    return int(right.returncode or 0)


def require_bin(name: str) -> None:
    from shutil import which

    if which(name) is None:
        raise FBError(f"Required binary not found in PATH: {name}", exit_code=2)


def configure_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def redact_secret(value: Optional[str], *, keep_last: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep_last:
        return "*" * len(value)
    return "*" * (len(value) - keep_last) + value[-keep_last:]


def parse_date_yyyy_mm_dd(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise FBError(f"Invalid date '{s}'. Expected YYYY-MM-DD.", exit_code=2) from e


def list_date_dirs(site_root: Path) -> list[Path]:
    if not site_root.exists():
        return []
    out: list[Path] = []
    for p in site_root.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name):
            out.append(p)
    return sorted(out, key=lambda x: x.name)


def safe_relpath(path: str) -> str:
    """
    Best-effort guard: reject dangerous remote paths.
    Used only for internal, constructed paths.
    """
    if "\x00" in path or path.strip() != path:
        raise FBError("Invalid path.", exit_code=2)
    if path.startswith("~") or path.startswith("-"):
        raise FBError("Invalid path.", exit_code=2)
    if "//" in path or "/../" in path or path.endswith("/.."):
        raise FBError("Invalid path.", exit_code=2)
    return path



from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .agent_protocol import signed_headers
from .db import now_ts
from .http_client import client as http_client


def _safe_component(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_", ".", "@"))[:128] or "unknown"


def _ts_dir() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())


def backup_site_flow(*, cx, registry, backups_root: Path, agent_id: str, stack: str, site: str) -> dict[str, Any]:
    """
    Dashboard-side mandatory flow:
    - audit log
    - call agent backup_site (signed)
    - pull artifacts into /backups/<agent>/<stack>/<site>/<timestamp>/
    - write manifest.json
    - apply retention
    """
    stack_n = _safe_component(stack)
    site_n = _safe_component(site)
    backup_dir = backups_root / _safe_component(agent_id) / stack_n / site_n / _ts_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    row = cx.execute("SELECT base_url, shared_secret FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "unknown_agent"}

    base_url = row["base_url"]
    secret = row["shared_secret"]

    audit_id = None
    try:
        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "ui", "backup.request", f"{agent_id}/{stack}/{site}", 1, json.dumps({})),
        )
        cx.commit()
        audit_id = cx.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    except Exception:
        pass

    action_path = "/api/backup_site"
    body = {"stack": stack, "site": site}
    ts = now_ts()
    headers = signed_headers(secret, ts=ts, method="POST", path=action_path, body=body)
    # Agent expects X-Signature and X-Timestamp headers
    headers["X-Signature"] = headers.pop("X-FB-SIG")
    headers["X-Timestamp"] = headers.pop("X-FB-TS")

    with http_client() as hc:
        try:
            r = hc.post(base_url + action_path, json=body, headers=headers)
        except httpx.HTTPError as e:
            _audit_finish(cx, audit_id, ok=False, detail={"error": "agent_unreachable", "detail": str(e)})
            return {"ok": False, "error": "agent_unreachable"}

    if r.status_code != 200:
        _audit_finish(cx, audit_id, ok=False, detail={"error": "agent_error", "status": r.status_code, "body": _snip(r.text)})
        return {"ok": False, "error": "agent_error", "status": r.status_code}

    agent_result = r.json()
    if not agent_result.get("ok"):
        _audit_finish(cx, audit_id, ok=False, detail={"error": "backup_failed", "agent_result": agent_result})
        return {"ok": False, "error": "backup_failed", "agent_result": agent_result}

    # For now, backups are created on the agent side via bench.
    # The agent returns success/failure status, but we don't pull artifacts yet.
    # TODO: Implement artifact discovery and pull when bench backup location is known.
    artifacts = list(agent_result.get("artifacts") or [])
    pulled: list[dict[str, Any]] = []
    for a in artifacts:
        path = str(a.get("path") or "")
        if not path:
            continue
        dst = backup_dir / os.path.basename(path)
        ok = _pull_artifact(base_url=base_url, secret=secret, src_path=path, dst_path=dst)
        pulled.append({"path": path, "saved_as": str(dst), "ok": ok})

    manifest = {
        "ok": True,
        "ts": now_ts(),
        "agent_id": agent_id,
        "stack": stack,
        "site": site,
        "agent_result": agent_result,
        "pulled": pulled,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    cx.execute(
        "INSERT INTO backups(ts,agent_id,stack,site,backup_dir,manifest_json) VALUES(?,?,?,?,?,?)",
        (now_ts(), agent_id, stack, site, str(backup_dir), json.dumps(manifest)),
    )
    cx.commit()

    retention_cleanup_site(cx=cx, backups_root=backups_root, agent_id=agent_id, stack=stack, site=site, keep=14)
    _audit_finish(cx, audit_id, ok=True, detail={"backup_dir": str(backup_dir), "pulled": pulled})
    return {"ok": True, "backup_dir": str(backup_dir), "manifest": manifest}


def _pull_artifact(*, base_url: str, secret: str, src_path: str, dst_path: Path) -> bool:
    # TODO: Implement artifact pull endpoint on agent when needed
    # For now, backups remain on agent side
    return False


def retention_cleanup_site(*, cx, backups_root: Path, agent_id: str, stack: str, site: str, keep: int) -> None:
    rows = cx.execute(
        "SELECT id, ts, backup_dir FROM backups WHERE agent_id=? AND stack=? AND site=? ORDER BY ts DESC",
        (agent_id, stack, site),
    ).fetchall()
    for row in rows[keep:]:
        _delete_tree(Path(row["backup_dir"]))
        cx.execute("DELETE FROM backups WHERE id=?", (row["id"],))
    cx.commit()


def retention_cleanup_all(cx, backups_root: Path) -> None:
    # group by agent/stack/site and keep last 14
    triples = cx.execute("SELECT DISTINCT agent_id, stack, site FROM backups").fetchall()
    for t in triples:
        retention_cleanup_site(
            cx=cx,
            backups_root=backups_root,
            agent_id=t["agent_id"],
            stack=t["stack"],
            site=t["site"],
            keep=14,
        )


def backup_all_sites_flow(cx, registry, backups_root: Path) -> None:
    """
    Scheduled backup flow: call agent list_sites, then backup each site.
    """
    agents = cx.execute("SELECT agent_id, base_url, shared_secret FROM agents").fetchall()
    for a in agents:
        agent_id = a["agent_id"]
        base_url = a["base_url"]
        secret = a["shared_secret"]
        
        # Get sites from agent
        try:
            list_path = "/api/list_sites"
            ts = now_ts()
            headers = signed_headers(secret, ts=ts, method="GET", path=list_path, body={})
            # Agent expects X-Signature and X-Timestamp headers
            headers["X-Signature"] = headers.pop("X-FB-SIG")
            headers["X-Timestamp"] = headers.pop("X-FB-TS")
            
            with http_client() as hc:
                r = hc.get(base_url + list_path, headers=headers)
                if r.status_code != 200:
                    continue
                data = r.json()
                sites = data.get("sites") or []
        except Exception:
            continue
        
        # Backup each site
        for site_info in sites:
            stack = str(site_info.get("stack") or "default")
            site = str(site_info.get("site") or "")
            if not site:
                continue
            try:
                backup_site_flow(cx=cx, registry=registry, backups_root=backups_root, agent_id=agent_id, stack=stack, site=site)
            except Exception:
                continue


def _delete_tree(p: Path) -> None:
    if not p.exists():
        return
    if p.is_file():
        p.unlink(missing_ok=True)
        return
    for child in p.rglob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)
    for child in sorted(p.rglob("*"), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
            except Exception:
                pass
    try:
        p.rmdir()
    except Exception:
        pass


def _audit_finish(cx, audit_id: int | None, *, ok: bool, detail: dict[str, Any]) -> None:
    if audit_id is None:
        return
    try:
        cx.execute(
            "UPDATE audit_log SET ok=?, detail_json=? WHERE id=?",
            (1 if ok else 0, json.dumps(detail), audit_id),
        )
        cx.commit()
    except Exception:
        pass


def _snip(s: str, n: int = 2000) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"



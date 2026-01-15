from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__
from .agent_registry import AgentRegistry
from .backup_orchestrator import backup_all_sites_flow, backup_site_flow, retention_cleanup_all
from .db import connect, kv_get, kv_set, now_ts
from .paths import fb_backups_root
from .security import new_secret


def create_app(*, db_path: Path, bind_host: str, bind_port: int) -> FastAPI:
    cx = connect(db_path)

    # Zero-config secrets: generated once, persisted.
    session_secret = kv_get(cx, "dashboard.session_secret")
    if not isinstance(session_secret, str):
        session_secret = new_secret()
        kv_set(cx, "dashboard.session_secret", session_secret)

    csrf_salt = kv_get(cx, "dashboard.csrf_salt")
    if not isinstance(csrf_salt, str):
        csrf_salt = new_secret()
        kv_set(cx, "dashboard.csrf_salt", csrf_salt)

    maintenance = kv_get(cx, "dashboard.maintenance", False)
    if not isinstance(maintenance, bool):
        maintenance = False
        kv_set(cx, "dashboard.maintenance", maintenance)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    app = FastAPI(title="fb", version=__version__)
    app.add_middleware(SessionMiddleware, secret_key=session_secret)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    registry = AgentRegistry(cx=cx, bind_host=bind_host, bind_port=bind_port)
    app.state.cx = cx
    app.state.registry = registry
    app.state.templates = templates
    app.state.backups_root = fb_backups_root()

    scheduler = BackgroundScheduler(timezone="UTC")
    app.state.scheduler = scheduler

    def ensure_csrf(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = new_secret()
            request.session["csrf"] = token
        return token

    def csrf_ok_header(request: Request) -> bool:
        want = request.session.get("csrf")
        got = request.headers.get("X-CSRF-Token") or ""
        return bool(want) and want == got

    @app.on_event("startup")
    def _startup() -> None:
        # Start UDP discovery + scheduler in background threads.
        registry.start()
        scheduler.add_job(backup_all_sites_flow, "cron", hour=2, minute=0, args=[cx, registry, app.state.backups_root])
        scheduler.add_job(retention_cleanup_all, "cron", hour=3, minute=0, args=[cx, app.state.backups_root])
        scheduler.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        registry.stop()
        scheduler.shutdown(wait=False)
        cx.close()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        agents = cx.execute("SELECT agent_id, last_seen, base_url, meta_json FROM agents ORDER BY agent_id").fetchall()
        maintenance_mode = bool(kv_get(cx, "dashboard.maintenance", False))
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "csrf": csrf,
                "maintenance": maintenance_mode,
                "agents": [
                    {
                        "agent_id": a["agent_id"],
                        "last_seen": a["last_seen"],
                        "base_url": a["base_url"],
                        "meta": json.loads(a["meta_json"] or "{}"),
                    }
                    for a in agents
                ],
            },
        )

    @app.post("/api/maintenance")
    def set_maintenance(request: Request, enabled: str = Form(...), csrf_token: str = Form(...)) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse("/", status_code=303)
        value = enabled.lower() in {"1", "true", "yes", "on"}
        kv_set(cx, "dashboard.maintenance", value)
        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "ui", "maintenance", "dashboard", 1, json.dumps({"enabled": value})),
        )
        cx.commit()
        return RedirectResponse("/", status_code=303)

    @app.get("/agent/{agent_id}", response_class=HTMLResponse)
    def agent_detail(agent_id: str, request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        row = cx.execute("SELECT agent_id, last_seen, base_url, meta_json FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return templates.TemplateResponse("error.html", {"request": request, "message": "Unknown agent"})
        meta = json.loads(row["meta_json"] or "{}")
        
        # Calculate backup counts per site
        site_backup_counts: dict[str, dict[str, dict[str, int]]] = {}
        backup_rows = cx.execute("SELECT agent_id, stack, site, COUNT(*) as cnt FROM backups WHERE agent_id=? GROUP BY agent_id, stack, site", (agent_id,)).fetchall()
        for br in backup_rows:
            if br["agent_id"] not in site_backup_counts:
                site_backup_counts[br["agent_id"]] = {}
            if br["stack"] not in site_backup_counts[br["agent_id"]]:
                site_backup_counts[br["agent_id"]][br["stack"]] = {}
            site_backup_counts[br["agent_id"]][br["stack"]][br["site"]] = br["cnt"]
        
        # Format last_seen
        import time as time_module
        last_seen_ts = row["last_seen"]
        is_online = (now_ts() - last_seen_ts) < 300  # Online if seen in last 5 minutes
        last_seen_formatted = time_module.strftime("%Y-%m-%d %H:%M:%S UTC", time_module.gmtime(last_seen_ts))
        
        return templates.TemplateResponse(
            "agent.html",
            {
                "request": request,
                "csrf": csrf,
                "agent": {
                    "agent_id": row["agent_id"],
                    "base_url": row["base_url"],
                    "last_seen": last_seen_ts,
                    "last_seen_formatted": last_seen_formatted,
                    "is_online": is_online,
                    "meta": meta,
                },
                "site_backup_counts": site_backup_counts,
            },
        )

    @app.post("/api/site/{agent_id}/{stack}/{site}/backup")
    def backup_site(agent_id: str, stack: str, site: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        if bool(kv_get(cx, "dashboard.maintenance", False)):
            return JSONResponse({"ok": False, "error": "maintenance_mode"}, status_code=409)
        result = backup_site_flow(cx=cx, registry=registry, backups_root=app.state.backups_root, agent_id=agent_id, stack=stack, site=site)
        return JSONResponse(result, status_code=200 if result.get("ok") else 500)

    @app.post("/api/agents/register")
    async def agents_register(request: Request) -> JSONResponse:
        """
        TOFU registration used to establish an HMAC key without user-provided secrets.
        """
        ip = request.client.host if request.client else ""
        payload = await request.json()
        token = str(payload.get("token") or "")
        agent_id = str(payload.get("agent_id") or "")
        agent_port = int(payload.get("port") or 0)
        meta = payload.get("meta") or {}

        if not token or not agent_id or agent_port <= 0 or agent_port > 65535:
            return JSONResponse({"ok": False, "error": "bad_request"}, status_code=400)
        if not registry.claim_token(token=token, agent_id=agent_id, ip=ip):
            return JSONResponse({"ok": False, "error": "invalid_token"}, status_code=403)

        base_url = f"http://{ip}:{agent_port}"
        secret = registry.upsert_agent(agent_id=agent_id, base_url=base_url, meta=dict(meta))

        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "agent", "register", agent_id, 1, json.dumps({"ip": ip, "port": agent_port})),
        )
        cx.commit()
        return JSONResponse({"ok": True, "shared_secret": secret, "dashboard_ts": now_ts()})

    @app.get("/add-agent", response_class=HTMLResponse)
    def add_agent_page(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": csrf})

    @app.post("/api/agents/add")
    def add_agent_manual(request: Request, agent_id: str = Form(...), base_url: str = Form(...), shared_secret: str = Form(""), csrf_token: str = Form(...)) -> HTMLResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want or "", "error": "Invalid CSRF token"})

        # Validate inputs
        if not agent_id or not base_url:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": "Agent ID and Base URL are required"})

        # Validate URL format
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": "Base URL must start with http:// or https://"})

        # Generate secret if not provided
        secret = shared_secret.strip() if shared_secret.strip() else new_secret()

        # Add agent
        try:
            # Use registry to add agent, but override secret if provided
            if shared_secret.strip():
                # If secret provided, insert directly
                now = now_ts()
                cx.execute(
                    "INSERT INTO agents(agent_id,created_at,last_seen,base_url,shared_secret,meta_json) VALUES(?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET base_url=excluded.base_url, shared_secret=excluded.shared_secret, last_seen=excluded.last_seen",
                    (agent_id, now, now, base_url, secret, json.dumps({})),
                )
                cx.commit()
            else:
                # Use registry to generate secret automatically
                registry.upsert_agent(agent_id=agent_id, base_url=base_url, meta={})

            cx.execute(
                "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                (now_ts(), "ui", "agent.add_manual", agent_id, 1, json.dumps({"base_url": base_url})),
            )
            cx.commit()
            return RedirectResponse("/", status_code=303)
        except Exception as e:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": f"Failed to add agent: {str(e)}"})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        schedule_hour = kv_get(cx, "dashboard.schedule_hour", 2)
        schedule_minute = kv_get(cx, "dashboard.schedule_minute", 0)
        retention_keep = kv_get(cx, "dashboard.retention_keep", 14)
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "csrf": csrf,
                "schedule_hour": schedule_hour,
                "schedule_minute": schedule_minute,
                "retention_keep": retention_keep,
                "backup_root": str(app.state.backups_root),
                "db_path": str(db_path),
            },
        )

    @app.post("/api/settings/schedule")
    def update_schedule(request: Request, hour: str = Form(...), minute: str = Form(...), csrf_token: str = Form(...)) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse("/settings", status_code=303)
        try:
            h = int(hour)
            m = int(minute)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return RedirectResponse("/settings", status_code=303)
            kv_set(cx, "dashboard.schedule_hour", h)
            kv_set(cx, "dashboard.schedule_minute", m)
            # TODO: Update scheduler job
            cx.execute(
                "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                (now_ts(), "ui", "settings.schedule", "dashboard", 1, json.dumps({"hour": h, "minute": m})),
            )
            cx.commit()
        except ValueError:
            pass
        return RedirectResponse("/settings", status_code=303)

    @app.post("/api/settings/retention")
    def update_retention(request: Request, keep: str = Form(...), csrf_token: str = Form(...)) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse("/settings", status_code=303)
        try:
            k = int(keep)
            if not (1 <= k <= 365):
                return RedirectResponse("/settings", status_code=303)
            kv_set(cx, "dashboard.retention_keep", k)
            cx.execute(
                "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                (now_ts(), "ui", "settings.retention", "dashboard", 1, json.dumps({"keep": k})),
            )
            cx.commit()
        except ValueError:
            pass
        return RedirectResponse("/settings", status_code=303)

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        
        # Check if agent exists
        row = cx.execute("SELECT agent_id FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        # Delete agent
        cx.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "ui", "agent.delete", agent_id, 1, json.dumps({})),
        )
        cx.commit()
        return JSONResponse({"ok": True})

    @app.get("/backups", response_class=HTMLResponse)
    def backups_browser(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        # Get all backups grouped by agent/stack/site
        rows = cx.execute(
            "SELECT id, ts, agent_id, stack, site, backup_dir, manifest_json FROM backups ORDER BY ts DESC"
        ).fetchall()
        
        backups: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
        for row in rows:
            agent_id = row["agent_id"]
            stack = row["stack"]
            site = row["site"]
            
            if agent_id not in backups:
                backups[agent_id] = {}
            if stack not in backups[agent_id]:
                backups[agent_id][stack] = {}
            if site not in backups[agent_id][stack]:
                backups[agent_id][stack][site] = []
            
            manifest = json.loads(row["manifest_json"] or "{}")
            import time as time_module
            timestamp = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(row["ts"]))
            
            backups[agent_id][stack][site].append({
                "id": row["id"],
                "timestamp": timestamp,
                "backup_dir": row["backup_dir"],
                "ok": manifest.get("ok", False),
            })
        
        return templates.TemplateResponse(
            "backups.html",
            {
                "request": request,
                "csrf": csrf,
                "backups": backups,
                "total_backups": len(rows),
            },
        )

    @app.delete("/api/backups/{backup_id}")
    def delete_backup(backup_id: int, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        
        # Get backup info
        row = cx.execute("SELECT id, backup_dir, agent_id, stack, site FROM backups WHERE id=?", (backup_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        # Delete files
        from pathlib import Path
        backup_dir = Path(row["backup_dir"])
        if backup_dir.exists():
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)
        
        # Delete from DB
        cx.execute("DELETE FROM backups WHERE id=?", (backup_id,))
        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "ui", "backup.delete", f"{row['agent_id']}/{row['stack']}/{row['site']}", 1, json.dumps({"backup_id": backup_id})),
        )
        cx.commit()
        return JSONResponse({"ok": True})

    @app.post("/api/site/{agent_id}/{stack}/{site}/schedule")
    def schedule_site_backup(
        agent_id: str,
        stack: str,
        site: str,
        request: Request,
        frequency: str = Form(...),
        time: str = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse(f"/agent/{agent_id}", status_code=303)
        
        # Store schedule in kv table
        schedule_key = f"schedule.{agent_id}.{stack}.{site}"
        schedule_data = {"frequency": frequency, "time": time, "enabled": frequency != "disabled"}
        kv_set(cx, schedule_key, schedule_data)
        
        cx.execute(
            "INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
            (now_ts(), "ui", "schedule.set", f"{agent_id}/{stack}/{site}", 1, json.dumps(schedule_data)),
        )
        cx.commit()
        
        return RedirectResponse(f"/agent/{agent_id}", status_code=303)

    @app.post("/api/agents/{agent_id}/refresh")
    def refresh_agent_sites(agent_id: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        
        # Get agent info
        row = cx.execute("SELECT base_url, shared_secret FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        # Call agent list_sites API
        base_url = row["base_url"]
        secret = row["shared_secret"]
        
        list_path = "/api/list_sites"
        ts = now_ts()
        from .agent_protocol import signed_headers
        headers = signed_headers(secret, ts=ts, method="GET", path=list_path, body={})
        headers["X-Signature"] = headers.pop("X-FB-SIG")
        headers["X-Timestamp"] = headers.pop("X-FB-TS")
        
        from .http_client import client as http_client
        try:
            with http_client() as hc:
                r = hc.get(base_url + list_path, headers=headers)
                if r.status_code != 200:
                    return JSONResponse({"ok": False, "error": "agent_error"}, status_code=500)
                data = r.json()
                sites = data.get("sites") or []
                
                # Group sites by stack
                stacks_dict: dict[str, list[str]] = {}
                for site_info in sites:
                    stack_name = str(site_info.get("stack") or "default")
                    site_name = str(site_info.get("site") or "")
                    if site_name:
                        if stack_name not in stacks_dict:
                            stacks_dict[stack_name] = []
                        stacks_dict[stack_name].append(site_name)
                
                # Update agent meta
                meta = {"stacks": [{"stack": stack, "sites": sites_list} for stack, sites_list in stacks_dict.items()]}
                cx.execute("UPDATE agents SET meta_json=?, last_seen=? WHERE agent_id=?", (json.dumps(meta), now_ts(), agent_id))
                cx.commit()
                
                return JSONResponse({"ok": True, "sites": sites})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return app



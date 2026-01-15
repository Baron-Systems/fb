from __future__ import annotations

import json
import time as time_module
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__
from .agent_protocol import signed_headers
from .agent_registry import AgentRegistry
from .backup_orchestrator import backup_all_sites_flow, backup_site_flow, retention_cleanup_all
from .db import connect, kv_get, kv_set, now_ts
from .http_client import client as http_client
from .paths import fb_backups_root
from .security import new_secret


def create_app(*, db_path: Path, bind_host: str, bind_port: int) -> FastAPI:
    cx = connect(db_path)

    # Zero-config secrets: generated once, persisted.
    session_secret = kv_get(cx, "dashboard.session_secret")
    if not isinstance(session_secret, str):
        session_secret = new_secret()
        kv_set(cx, "dashboard.session_secret", session_secret)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    app = FastAPI(title="Backup Dashboard", version=__version__)
    app.add_middleware(SessionMiddleware, secret_key=session_secret)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    registry = AgentRegistry(cx=cx, bind_host=bind_host, bind_port=bind_port)
    app.state.cx = cx
    app.state.registry = registry
    app.state.templates = templates
    app.state.backups_root = fb_backups_root()
    app.state.db_path = db_path

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
        registry.start()
        # Run backup check every minute to support site-specific schedules
        scheduler.add_job(backup_all_sites_flow, "cron", minute="*", args=[cx, registry, app.state.backups_root])
        # Cleanup daily at 03:00
        scheduler.add_job(retention_cleanup_all, "cron", hour=3, minute=0, args=[cx, app.state.backups_root])
        scheduler.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        registry.stop()
        scheduler.shutdown(wait=False)
        cx.close()

    # ========== ROUTES ==========

    @app.get("/", response_class=HTMLResponse)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse("/agents", status_code=303)

    # AGENTS
    @app.get("/agents", response_class=HTMLResponse)
    def agents_list(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        rows = cx.execute("SELECT agent_id, last_seen, base_url, meta_json FROM agents ORDER BY agent_id").fetchall()
        
        agents = []
        for row in rows:
            meta = json.loads(row["meta_json"] or "{}")
            last_seen_ts = row["last_seen"]
            is_online = (now_ts() - last_seen_ts) < 300
            last_seen_formatted = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(last_seen_ts))
            
            # Count sites
            site_count = sum(len(st.get("sites", [])) for st in meta.get("stacks", []))
            hostname = meta.get("hostname", row["agent_id"])
            
            agents.append({
                "agent_id": row["agent_id"],
                "hostname": hostname,
                "base_url": row["base_url"],
                "last_seen": last_seen_ts,
                "last_seen_formatted": last_seen_formatted,
                "is_online": is_online,
                "site_count": site_count,
            })
        
        return templates.TemplateResponse("agents_list.html", {
            "request": request,
            "csrf": csrf,
            "agents": agents,
            "active_page": "agents",
        })

    @app.get("/agents/add", response_class=HTMLResponse)
    def agents_add_page(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": csrf, "active_page": "agents"})

    @app.post("/api/agents/add")
    def agents_add(
        request: Request, 
        agent_name: str = Form(...), 
        agent_id: str = Form(...), 
        base_url: str = Form(...), 
        shared_secret: str = Form(""), 
        csrf_token: str = Form(...)
    ) -> HTMLResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want or "", "error": "Invalid CSRF"})
        if not agent_id or not base_url or not agent_name:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": "Required fields missing"})
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": "Invalid URL"})
        
        secret = shared_secret.strip() if shared_secret.strip() else new_secret()
        try:
            now = now_ts()
            cx.execute(
                "INSERT INTO agents(agent_id,agent_name,created_at,last_seen,base_url,shared_secret,meta_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET agent_name=excluded.agent_name, base_url=excluded.base_url, shared_secret=excluded.shared_secret, last_seen=excluded.last_seen",
                (agent_id, agent_name.strip(), now, now, base_url, secret, json.dumps({})),
            )
            cx.commit()
            
            cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                      (now_ts(), "ui", "agent.add_manual", agent_id, 1, json.dumps({"base_url": base_url, "agent_name": agent_name})))
            cx.commit()
            return RedirectResponse("/agents", status_code=303)
        except Exception as e:
            return templates.TemplateResponse("add_agent.html", {"request": request, "csrf": want, "error": str(e)})

    @app.get("/agents/{agent_id}", response_class=HTMLResponse)
    def agent_detail(agent_id: str, request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        row = cx.execute("SELECT agent_id, agent_name, last_seen, base_url, meta_json FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return templates.TemplateResponse("error.html", {"request": request, "message": "Unknown agent"})
        
        meta = json.loads(row["meta_json"] or "{}")
        last_seen_ts = row["last_seen"]
        is_online = (now_ts() - last_seen_ts) < 300
        last_seen_formatted = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(last_seen_ts))
        
        # Get sites (flatten stacks)
        sites = []
        for st in meta.get("stacks", []):
            stack_name = st.get("stack") or st.get("name") or "default"
            for site_name in st.get("sites", []):
                sites.append({
                    "name": site_name,
                    "stack": stack_name,
                })
        
        return templates.TemplateResponse("agent_detail.html", {
            "request": request,
            "csrf": csrf,
            "agent": {
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "hostname": meta.get("hostname", row["agent_id"]),
                "base_url": row["base_url"],
                "last_seen_formatted": last_seen_formatted,
                "is_online": is_online,
                "sites": sites,
            },
            "active_page": "agents",
        })

    # SITES
    @app.get("/sites", response_class=HTMLResponse)
    def sites_list(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        # Get all sites from all agents
        rows = cx.execute("SELECT agent_id, meta_json FROM agents ORDER BY agent_id").fetchall()
        sites = []
        for row in rows:
            meta = json.loads(row["meta_json"] or "{}")
            for st in meta.get("stacks", []):
                stack_name = st.get("stack") or st.get("name") or "default"
                for site_name in st.get("sites", []):
                    # Get last backup
                    backup_row = cx.execute(
                        "SELECT ts FROM backups WHERE agent_id=? AND stack=? AND site=? ORDER BY ts DESC LIMIT 1",
                        (row["agent_id"], stack_name, site_name)
                    ).fetchone()
                    last_backup = time_module.strftime("%Y-%m-%d %H:%M", time_module.gmtime(backup_row["ts"])) if backup_row else None
                    
                    # Get schedule
                    schedule_key = f"schedule.{row['agent_id']}.{stack_name}.{site_name}"
                    schedule = kv_get(cx, schedule_key, {"enabled": False})
                    
                    sites.append({
                        "name": site_name,
                        "agent_id": row["agent_id"],
                        "stack": stack_name,
                        "last_backup": last_backup,
                        "schedule_enabled": schedule.get("enabled", False),
                    })
        
        return templates.TemplateResponse("sites_list.html", {
            "request": request,
            "csrf": csrf,
            "sites": sites,
            "active_page": "sites",
        })

    @app.get("/sites/{agent_id}/{stack}/{site}", response_class=HTMLResponse)
    def site_detail(agent_id: str, stack: str, site: str, request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        # Get schedule
        schedule_key = f"schedule.{agent_id}.{stack}.{site}"
        schedule_data = kv_get(cx, schedule_key, {})
        schedule = {
            "enabled": schedule_data.get("enabled", False),
            "frequency": schedule_data.get("frequency", "daily"),
            "time": schedule_data.get("time", "02:00"),
            "retention": schedule_data.get("retention", 14),
        }
        
        # Get backups
        backup_rows = cx.execute(
            "SELECT id, ts, backup_dir, manifest_json, rating, feedback FROM backups WHERE agent_id=? AND stack=? AND site=? ORDER BY ts DESC",
            (agent_id, stack, site)
        ).fetchall()
        
        backups = []
        for br in backup_rows:
            manifest = json.loads(br["manifest_json"] or "{}")
            timestamp = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(br["ts"]))
            
            # Calculate backup size
            backup_dir = Path(br["backup_dir"])
            size_str = "N/A"
            if backup_dir.exists():
                total_size = 0
                for file_path in backup_dir.rglob('*'):
                    if file_path.is_file():
                        total_size += file_path.stat().st_size
                
                if total_size > 0:
                    if total_size < 1024:
                        size_str = f"{total_size} B"
                    elif total_size < 1024 * 1024:
                        size_str = f"{total_size / 1024:.1f} KB"
                    else:
                        size_str = f"{total_size / (1024 * 1024):.2f} MB"
            
            backups.append({
                "id": br["id"],
                "timestamp": timestamp,
                "size": size_str,
                "components": "database, files, private files",
                "ok": manifest.get("ok", False),
                "error": manifest.get("error", ""),
                "rating": br["rating"],
                "feedback": br["feedback"],
            })
        
        return templates.TemplateResponse("site_detail.html", {
            "request": request,
            "csrf": csrf,
            "site": {
                "name": site,
                "agent_id": agent_id,
                "stack": stack,
                "schedule": schedule,
            },
            "backups": backups,
            "active_page": "sites",
        })

    # BACKUPS
    @app.get("/backups", response_class=HTMLResponse)
    def backups_list(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        rows = cx.execute("SELECT id, ts, agent_id, stack, site, backup_dir, manifest_json FROM backups ORDER BY ts DESC").fetchall()
        
        backups = []
        for row in rows:
            manifest = json.loads(row["manifest_json"] or "{}")
            timestamp = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(row["ts"]))
            
            # Calculate backup size
            backup_dir = Path(row["backup_dir"])
            size_str = "N/A"
            if backup_dir.exists():
                total_size = 0
                for file_path in backup_dir.rglob('*'):
                    if file_path.is_file():
                        total_size += file_path.stat().st_size
                
                if total_size > 0:
                    if total_size < 1024:
                        size_str = f"{total_size} B"
                    elif total_size < 1024 * 1024:
                        size_str = f"{total_size / 1024:.1f} KB"
                    else:
                        size_str = f"{total_size / (1024 * 1024):.2f} MB"
            
            backups.append({
                "id": row["id"],
                "agent_id": row["agent_id"],
                "stack": row["stack"],
                "site": row["site"],
                "timestamp": timestamp,
                "size": size_str,
                "ok": manifest.get("ok", False),
            })
        
        return templates.TemplateResponse("backups_list.html", {
            "request": request,
            "csrf": csrf,
            "backups": backups,
            "total_backups": len(backups),
            "active_page": "backups",
        })

    # AUDIT LOGS
    @app.get("/audit-logs", response_class=HTMLResponse)
    def audit_logs(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        rows = cx.execute("SELECT ts, actor, action, target, ok, detail_json FROM audit_log ORDER BY ts DESC LIMIT 1000").fetchall()
        
        logs = []
        for row in rows:
            timestamp = time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.gmtime(row["ts"]))
            details = json.loads(row["detail_json"] or "{}")
            logs.append({
                "timestamp": timestamp,
                "actor": row["actor"],
                "action": row["action"],
                "target": row["target"],
                "ok": row["ok"],
                "details": json.dumps(details) if details else "",
            })
        
        return templates.TemplateResponse("audit_logs.html", {
            "request": request,
            "csrf": csrf,
            "logs": logs,
            "active_page": "audit",
        })

    # SETTINGS
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        csrf = ensure_csrf(request)
        
        # Get Telegram settings
        telegram_row = cx.execute("SELECT bot_token, chat_id, enabled FROM telegram_settings WHERE id=1").fetchone()
        telegram_settings = {
            "bot_token": telegram_row["bot_token"] if telegram_row else "",
            "chat_id": telegram_row["chat_id"] if telegram_row else "",
            "enabled": bool(telegram_row["enabled"]) if telegram_row else False,
        }
        
        return templates.TemplateResponse("settings_page.html", {
            "request": request,
            "csrf": csrf,
            "settings": {
                "backup_root": str(app.state.backups_root),
                "db_path": str(app.state.db_path),
                "retention_keep": kv_get(cx, "dashboard.retention_keep", 14),
                "maintenance": kv_get(cx, "dashboard.maintenance", False),
            },
            "telegram": telegram_settings,
            "active_page": "settings",
        })

    # API ENDPOINTS
    @app.post("/api/agents/{agent_id}/rename")
    async def rename_agent(agent_id: str, request: Request) -> JSONResponse:
        payload = await request.json()
        new_name = str(payload.get("name") or "").strip()
        
        if not new_name or len(new_name) > 50:
            return JSONResponse({"ok": False, "error": "Invalid name"}, status_code=400)
        
        cx.execute("UPDATE agents SET agent_name=? WHERE agent_id=?", (new_name, agent_id))
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "agent.rename", agent_id, 1, json.dumps({"new_name": new_name})))
        cx.commit()
        
        return JSONResponse({"ok": True, "name": new_name})
    
    @app.post("/api/maintenance")
    def set_maintenance(request: Request, enabled: str = Form(...), csrf_token: str = Form(...)) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse("/settings", status_code=303)
        value = enabled.lower() in {"1", "true", "yes", "on"}
        kv_set(cx, "dashboard.maintenance", value)
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "maintenance", "dashboard", 1, json.dumps({"enabled": value})))
        cx.commit()
        return RedirectResponse("/settings", status_code=303)
    
    @app.post("/api/settings/telegram")
    def save_telegram_settings(
        request: Request,
        bot_token: str = Form(...),
        chat_id: str = Form(...),
        enabled: str = Form("0"),
        csrf_token: str = Form(...)
    ) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse("/settings", status_code=303)
        
        is_enabled = enabled.lower() in {"1", "true", "yes", "on"}
        
        cx.execute(
            "INSERT INTO telegram_settings(id,bot_token,chat_id,enabled) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET bot_token=excluded.bot_token, chat_id=excluded.chat_id, enabled=excluded.enabled",
            (bot_token.strip(), chat_id.strip(), int(is_enabled))
        )
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "telegram.configure", "settings", 1, json.dumps({"enabled": is_enabled})))
        cx.commit()
        
        return RedirectResponse("/settings", status_code=303)

    @app.post("/api/sites/{agent_id}/{stack}/{site}/backup")
    def backup_site_api(agent_id: str, stack: str, site: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        if bool(kv_get(cx, "dashboard.maintenance", False)):
            return JSONResponse({"ok": False, "error": "maintenance_mode"}, status_code=409)
        result = backup_site_flow(cx=cx, registry=registry, backups_root=app.state.backups_root, agent_id=agent_id, stack=stack, site=site)
        return JSONResponse(result, status_code=200 if result.get("ok") else 500)

    @app.post("/api/sites/{agent_id}/{stack}/{site}/schedule")
    def schedule_site(agent_id: str, stack: str, site: str, request: Request, frequency: str = Form(...), time: str = Form(...), retention: str = Form("14"), csrf_token: str = Form(...)) -> RedirectResponse:
        want = request.session.get("csrf")
        if not want or csrf_token != want:
            return RedirectResponse(f"/sites/{agent_id}/{stack}/{site}", status_code=303)
        
        schedule_key = f"schedule.{agent_id}.{stack}.{site}"
        schedule_data = {"frequency": frequency, "time": time, "retention": int(retention), "enabled": frequency != "disabled"}
        kv_set(cx, schedule_key, schedule_data)
        
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "schedule.set", f"{agent_id}/{stack}/{site}", 1, json.dumps(schedule_data)))
        cx.commit()
        
        return RedirectResponse(f"/sites/{agent_id}/{stack}/{site}", status_code=303)

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        cx.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "agent.delete", agent_id, 1, json.dumps({})))
        cx.commit()
        return JSONResponse({"ok": True})

    @app.post("/api/backups/{backup_id}/rate")
    async def rate_backup(backup_id: int, request: Request) -> JSONResponse:
        payload = await request.json()
        rating = int(payload.get("rating") or 0)
        feedback = str(payload.get("feedback") or "").strip()
        
        if rating < 1 or rating > 5:
            return JSONResponse({"ok": False, "error": "Invalid rating"}, status_code=400)
        
        cx.execute("UPDATE backups SET rating=?, feedback=? WHERE id=?", (rating, feedback, backup_id))
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "backup.rate", str(backup_id), 1, json.dumps({"rating": rating})))
        cx.commit()
        
        return JSONResponse({"ok": True, "rating": rating})
    
    @app.delete("/api/backups/{backup_id}")
    def delete_backup(backup_id: int, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        
        row = cx.execute("SELECT id, backup_dir, agent_id, stack, site FROM backups WHERE id=?", (backup_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        backup_dir = Path(row["backup_dir"])
        if backup_dir.exists():
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)
        
        cx.execute("DELETE FROM backups WHERE id=?", (backup_id,))
        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "ui", "backup.delete", f"{row['agent_id']}/{row['stack']}/{row['site']}", 1, json.dumps({"backup_id": backup_id})))
        cx.commit()
        return JSONResponse({"ok": True})

    @app.get("/api/backups/{backup_id}/download")
    def download_backup(backup_id: int, request: Request):
        """
        Download backup as a zip file.
        """
        from fastapi.responses import FileResponse
        import zipfile
        import tempfile
        
        row = cx.execute("SELECT id, backup_dir, agent_id, stack, site, manifest_json FROM backups WHERE id=?", (backup_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        backup_dir = Path(row["backup_dir"])
        if not backup_dir.exists():
            return JSONResponse({"ok": False, "error": "backup_files_not_found"}, status_code=404)
        
        # Create temporary zip file
        temp_zip = tempfile.NamedTemporaryFile(mode='wb', suffix='.zip', delete=False)
        temp_zip.close()
        
        try:
            files_added = []
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in backup_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(backup_dir)
                        zf.write(file_path, arcname)
                        files_added.append(str(arcname))
                        print(f"DEBUG: Added to zip: {arcname} ({file_path.stat().st_size} bytes)", flush=True)
            
            zip_size = Path(temp_zip.name).stat().st_size
            print(f"DEBUG: Created zip file: {temp_zip.name} ({zip_size} bytes) with {len(files_added)} files", flush=True)
            
            # Log download
            cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                      (now_ts(), "ui", "backup.download", f"{row['agent_id']}/{row['stack']}/{row['site']}", 1, json.dumps({"backup_id": backup_id, "files": files_added, "zip_size": zip_size})))
            cx.commit()
            
            filename = f"backup-{row['site']}-{backup_id}.zip"
            return FileResponse(
                path=temp_zip.name,
                media_type='application/zip',
                filename=filename,
                background=None  # Don't delete temp file automatically, will be cleaned up by OS
            )
        except Exception as e:
            import os
            if os.path.exists(temp_zip.name):
                os.unlink(temp_zip.name)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/agents/{agent_id}/refresh")
    def refresh_agent(agent_id: str, request: Request) -> JSONResponse:
        if not csrf_ok_header(request):
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        
        row = cx.execute("SELECT base_url, shared_secret FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        
        base_url = row["base_url"]
        secret = row["shared_secret"]
        list_path = "/api/list_sites"
        ts = now_ts()
        headers = signed_headers(secret, ts=ts, method="GET", path=list_path, body={})
        headers["X-Signature"] = headers.pop("X-FB-SIG")
        headers["X-Timestamp"] = headers.pop("X-FB-TS")
        
        try:
            with http_client() as hc:
                r = hc.get(base_url + list_path, headers=headers)
                if r.status_code != 200:
                    return JSONResponse({"ok": False, "error": "agent_error"}, status_code=500)
                data = r.json()
                sites = data.get("sites") or []
                
                stacks_dict: dict[str, list[str]] = {}
                for site_info in sites:
                    stack_name = str(site_info.get("stack") or "default")
                    site_name = str(site_info.get("site") or "")
                    if site_name:
                        if stack_name not in stacks_dict:
                            stacks_dict[stack_name] = []
                        stacks_dict[stack_name].append(site_name)
                
                meta = {"stacks": [{"stack": stack, "sites": sites_list} for stack, sites_list in stacks_dict.items()]}
                cx.execute("UPDATE agents SET meta_json=?, last_seen=? WHERE agent_id=?", (json.dumps(meta), now_ts(), agent_id))
                cx.commit()
                
                return JSONResponse({"ok": True, "sites": sites})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/agents/register")
    async def agents_register(request: Request) -> JSONResponse:
        ip = request.client.host if request.client else ""
        payload = await request.json()
        token = str(payload.get("token") or "")
        agent_id = str(payload.get("agent_id") or "")
        agent_port = int(payload.get("port") or 0)
        meta = payload.get("meta") or {}

        if not token or not agent_id or agent_port <= 0 or agent_port > 65535:
            return JSONResponse({"ok": False, "error": "bad_request"}, status_code=400)
        
        # Special handling for "reannounce" token (periodic re-registration)
        if token == "reannounce":
            # Check if agent already exists
            row = cx.execute("SELECT agent_id FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not_registered"}, status_code=404)
            
            # Update existing agent (meta and last_seen via upsert)
            base_url = f"http://{ip}:{agent_port}"
            secret = registry.upsert_agent(agent_id=agent_id, base_url=base_url, meta=dict(meta))
            
            # Don't log every reannounce (too noisy), just update timestamp
            return JSONResponse({"ok": True, "shared_secret": secret, "dashboard_ts": now_ts()})
        
        # Regular registration with token validation
        if not registry.claim_token(token=token, agent_id=agent_id, ip=ip):
            return JSONResponse({"ok": False, "error": "invalid_token"}, status_code=403)

        base_url = f"http://{ip}:{agent_port}"
        secret = registry.upsert_agent(agent_id=agent_id, base_url=base_url, meta=dict(meta))

        cx.execute("INSERT INTO audit_log(ts,actor,action,target,ok,detail_json) VALUES(?,?,?,?,?,?)",
                  (now_ts(), "agent", "register", agent_id, 1, json.dumps({"ip": ip, "port": agent_port})))
        cx.commit()
        return JSONResponse({"ok": True, "shared_secret": secret, "dashboard_ts": now_ts()})

    @app.get("/api/server-time")
    def get_server_time() -> JSONResponse:
        """Get current server time"""
        import time
        return JSONResponse({
            "timestamp": now_ts(),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "timezone": time.strftime("%Z")
        })
    
    @app.get("/api/notifications")
    def get_notifications() -> JSONResponse:
        """Get recent notifications"""
        rows = cx.execute(
            "SELECT id, ts, type, title, message, is_read FROM notifications ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        
        notifications = []
        for row in rows:
            notifications.append({
                "id": row["id"],
                "ts": row["ts"],
                "type": row["type"],
                "title": row["title"],
                "message": row["message"],
                "is_read": bool(row["is_read"]),
            })
        
        return JSONResponse({"notifications": notifications})
    
    @app.post("/api/notifications/mark-read")
    def mark_notifications_read() -> JSONResponse:
        """Mark all notifications as read"""
        cx.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
        cx.commit()
        return JSONResponse({"ok": True})
    
    return app


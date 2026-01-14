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
        return templates.TemplateResponse(
            "agent.html",
            {"request": request, "csrf": csrf, "agent": {"agent_id": row["agent_id"], "base_url": row["base_url"], "last_seen": row["last_seen"], "meta": meta}},
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

    return app



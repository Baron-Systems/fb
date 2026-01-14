from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from .db import now_ts
from .security import new_secret


DISCOVERY_PORT = 7355


@dataclass
class PendingToken:
    token: str
    agent_id: str
    ip: str
    created_at: int

    def expired(self, now: int, ttl_s: int = 30) -> bool:
        return now - self.created_at > ttl_s


class AgentRegistry:
    """
    Zero-config discovery + registration.

    - Agents broadcast UDP hello on DISCOVERY_PORT
    - Dashboard replies with a short-lived registration token and base URL
    - Agent calls HTTP /api/agents/register with token to receive shared_secret (HMAC key)
    """

    def __init__(self, *, cx, bind_host: str, bind_port: int) -> None:
        self._cx = cx
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: dict[str, PendingToken] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._udp_loop, name="fb-udp-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _local_ip_for_peer(self, peer_ip: str, peer_port: int) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((peer_ip, peer_port))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _udp_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
        sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(64 * 1024)
            except socket.timeout:
                self._cleanup_pending()
                continue
            except Exception:
                continue

            ip, port = addr[0], addr[1]
            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception:
                continue

            if payload.get("type") != "fb-agent.hello":
                continue

            agent_id = str(payload.get("agent_id") or "")
            agent_port = int(payload.get("port") or 0)
            if not agent_id or agent_port <= 0 or agent_port > 65535:
                continue

            token = new_secret()
            now = now_ts()
            with self._lock:
                self._pending[token] = PendingToken(token=token, agent_id=agent_id, ip=ip, created_at=now)

            dash_ip = self._local_ip_for_peer(ip, port)
            resp = {
                "type": "fb.dashboard.offer",
                "dashboard_url": f"http://{dash_ip}:{self._bind_port}",
                "token": token,
                "expires_in": 30,
            }
            try:
                # Reply to the same UDP socket/port the agent used to broadcast.
                sock.sendto(json.dumps(resp).encode("utf-8"), addr)
            except Exception:
                # Agent might not be listening for direct UDP; it will rebroadcast.
                pass

        sock.close()

    def _cleanup_pending(self) -> None:
        now = now_ts()
        with self._lock:
            dead = [k for k, v in self._pending.items() if v.expired(now)]
            for k in dead:
                self._pending.pop(k, None)

    def claim_token(self, *, token: str, agent_id: str, ip: str) -> bool:
        self._cleanup_pending()
        with self._lock:
            pt = self._pending.get(token)
            if not pt:
                return False
            if pt.agent_id != agent_id:
                return False
            if pt.ip != ip:
                return False
            self._pending.pop(token, None)
            return True

    def upsert_agent(self, *, agent_id: str, base_url: str, meta: dict[str, Any]) -> str:
        """
        Returns the shared_secret (created once and persisted).
        """
        now = now_ts()
        row = self._cx.execute("SELECT shared_secret FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if row:
            secret = row["shared_secret"]
            self._cx.execute(
                "UPDATE agents SET last_seen=?, base_url=?, meta_json=? WHERE agent_id=?",
                (now, base_url, json.dumps(meta), agent_id),
            )
        else:
            secret = new_secret()
            self._cx.execute(
                "INSERT INTO agents(agent_id,created_at,last_seen,base_url,shared_secret,meta_json) VALUES(?,?,?,?,?,?)",
                (agent_id, now, now, base_url, secret, json.dumps(meta)),
            )
        self._cx.commit()
        return secret



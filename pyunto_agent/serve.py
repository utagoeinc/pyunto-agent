"""Hosted mode: one long-running agent that pyunto-server can add to spaces on request.

    pyunto-agent serve --backend claude-api --listen 127.0.0.1:8788

HTTP API (JSON; every request needs `X-Agent-Secret: $PYUNTO_AGENT_SECRET`):

    GET  /whoami                      -> {user_id, display_name}
    POST /invite  {invite_link}       -> {ok, space_id}     join the space, resubscribe
    POST /leave   {space_id}          -> {ok}               leave the space, resubscribe
    GET  /status?space_id=…           -> {member, has_key}

pyunto-server's `/api/chat-spaces/:uuid/agents/claude` endpoints call this after checking that
the requester is a member and the space is premium. The agent account is a normal member; its
identity key lives only on this host, and the members' apps seal each space key for it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .bridge import Bridge

log = logging.getLogger(__name__)


def serve(bridge: Bridge, keys, listen: str, secret: str, roles=None) -> None:  # noqa: ANN001
    host, _, port = listen.rpartition(":")
    host = host or "127.0.0.1"
    client = bridge.client
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: ANN001, ANN002
            log.info("http " + fmt, *args)

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            if secret and self.headers.get("X-Agent-Secret") != secret:
                self._json(401, {"error": "unauthorized"})
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode() or "{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            if not self._auth():
                return
            u = urlparse(self.path)
            if u.path == "/whoami":
                ident = client.session.identity
                self._json(200, {"user_id": client.uuid, "display_name": ident.display_name if ident else None})
            elif u.path == "/roles":
                # What this agent has been told to be, per space. The issuer reads this to
                # check what their customers' spaces are actually getting.
                if roles is None:
                    self._json(404, {"error": "roles are not enabled"})
                    return
                from dataclasses import asdict
                self._json(200, {"roles": [asdict(r) for r in roles.all()]})
            elif u.path == "/status":
                sid = (parse_qs(u.query).get("space_id") or [""])[0].lower()
                member = sid in {str(s.get("uuid", "")).lower() for s in client.list_spaces()}
                self._json(200, {"member": member, "has_key": keys.has_key(sid) if sid else False})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._auth():
                return
            u = urlparse(self.path)
            body = self._body()
            try:
                if u.path == "/invite":
                    link = body.get("invite_link") or body.get("invite")
                    if not link:
                        self._json(400, {"error": "invite_link required"})
                        return
                    with lock:
                        sid = client.join(link)
                        bridge.reconnect()
                    self._json(200, {"ok": True, "space_id": sid, "user_id": client.uuid})
                elif u.path == "/role":
                    # Give one space a role: who runs it, what it is, and whether it speaks
                    # first. This is the product surface a business issuing an agent uses.
                    if roles is None:
                        self._json(404, {"error": "roles are not enabled"})
                        return
                    from .roles import Role
                    sid = body.get("space_id")
                    if not sid:
                        self._json(400, {"error": "space_id required"})
                        return
                    role = Role(
                        space_id=sid,
                        operator=str(body.get("operator") or ""),
                        persona=str(body.get("persona") or ""),
                        checkin_prompt=str(body.get("checkin_prompt") or ""),
                        checkin_seconds=body.get("checkin_seconds"),
                    )
                    roles.set(role)
                    self._json(200, {"ok": True, "space_id": role.space_id,
                                     "speaks_first": role.speaks_first,
                                     "checkin_seconds": role.checkin_seconds})
                elif u.path == "/role/remove":
                    if roles is None:
                        self._json(404, {"error": "roles are not enabled"})
                        return
                    sid = body.get("space_id")
                    if not sid:
                        self._json(400, {"error": "space_id required"})
                        return
                    self._json(200, {"ok": True, "removed": roles.remove(sid)})
                elif u.path == "/leave":
                    sid = body.get("space_id")
                    if not sid:
                        self._json(400, {"error": "space_id required"})
                        return
                    with lock:
                        client.leave_space(sid)
                        bridge.reconnect()
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:  # noqa: BLE001
                log.exception("request failed")
                self._json(500, {"error": str(e)})

    httpd = ThreadingHTTPServer((host, int(port or 8788)), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="agent-http").start()
    log.info("agent service listening on %s:%s", host, port or 8788)
    try:
        bridge.run()
    finally:
        httpd.shutdown()


def secret_from_env() -> str:
    return os.environ.get("PYUNTO_AGENT_SECRET", "")

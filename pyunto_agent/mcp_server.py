"""Pyunto as an MCP server (stdio), so Claude Code / Claude Desktop / any MCP client can read
and write a diary the agent account belongs to.

Implements the MCP JSON-RPC subset that tool-only servers need (initialize, ping, tools/list,
tools/call, notifications) without a dependency on the `mcp` package. Newline-delimited JSON
on stdin/stdout, logs on stderr.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from typing import Any

from .client import IncomingMessage, PyuntoClient
from .markers import (
    item_marker,
    marker_preview,
    parse_item_marker,
    reaction_marker,
    sticker_marker,
)

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"

#: Sent in the initialize response. Clients show it to the model as guidance for this server.
INSTRUCTIONS = """\
This server reads and writes a Pyunto diary: a private, end-to-end encrypted diary app for one
person, two people or a group. Entries are decrypted on this computer, never by Pyunto.

- If list_spaces is empty, the account is not in any diary yet: call `pair`, show the person the
  QR code, and ask them to scan it in the Pyunto app and choose a diary. Then they should open
  that diary in the app once, which hands this account the key.
- Start with list_spaces, then list_threads and read_thread.
- Entries with a photo, video or document show an `attachment`; call read_attachment to see it.
- Before writing, call list_members: everyone in the space reads what you post.
- Write only when the person asks you to. A diary is theirs; do not post on your own initiative.
"""


def _version() -> str:
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("pyunto-agent")
    except Exception:  # noqa: BLE001
        return "0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "whoami",
        "description": "The Pyunto account this server is connected as, how many diary spaces it "
        "is in, and whether it holds the key for each. Call first if another tool fails.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_spaces",
        "description": "Diary spaces (exchange or group diaries) this agent is a member of, with "
        "ids, names and members. Use to resolve a space name to the space_id other tools need.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_threads",
        "description": "Recent entries (threads) in a space, newest first: thread_id, title, "
        "created_at, entry count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Default 20, max 100."},
            },
            "required": ["space_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_thread",
        "description": "All entries of one thread, decrypted, oldest first, with who wrote each.",
        "inputSchema": {
            "type": "object",
            "properties": {"space_id": {"type": "string"}, "thread_id": {"type": "string"}},
            "required": ["space_id", "thread_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_message",
        "description": "Block until a new entry arrives from a human member (or timeout_seconds "
        "elapses, default 120, max 600). Returns the entry so you can reply with post_entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "Only this space (optional)."},
                "timeout_seconds": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "post_entry",
        "description": "Write into the diary. With thread_id it replies in that thread; without, "
        "it starts a new entry. silent=true skips the push notification. Keep replies short "
        "and in the writer's language.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "text": {"type": "string"},
                "thread_id": {"type": "string"},
                "silent": {"type": "boolean"},
            },
            "required": ["space_id", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "react",
        "description": "Add a reaction (one of ❤️ 👍 😂 😮 🙏) to a thread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "emoji": {"type": "string"},
            },
            "required": ["space_id", "thread_id", "emoji"],
            "additionalProperties": False,
        },
    },
    {
        "name": "post_sticker",
        "description": "Post a mascot sticker. sticker_id 000000..000004 (hello, thanks, sorry, "
        "good night, cheer up); optional caption (<=25 chars).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "sticker_id": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["space_id", "sticker_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "post_list_item",
        "description": "Log quick-list items (e.g. medicines taken, books read) as one entry. "
        "Repeat an item in `items` to count it more than once.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "list_name": {"type": "string"},
                "emoji": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
                "thread_id": {"type": "string"},
            },
            "required": ["space_id", "list_name", "items"],
            "additionalProperties": False,
        },
    },
    {
        "name": "quick_list_stats",
        "description": "How many times each quick-list item was recorded in a diary space, over "
        "a period. Quick lists are the repeated things a diary tracks -- medicines taken, books "
        "read to a child, meals, walks. This is what lets you say \"that is the third time this "
        "week\" instead of asking. Counts repeats: recording the same item twice means twice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "days": {
                    "type": "integer",
                    "description": "How far back to count. Default 7.",
                },
                "list_name": {
                    "type": "string",
                    "description": "Only this list. Omit for every list in the space.",
                },
            },
            "required": ["space_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_members",
        "description": "Who is in a diary space, and for each one whether they are a person or "
        "an agent, who runs that agent and where it runs. Call this before writing anything "
        "sensitive: it tells you who else reads what you post.",
        "inputSchema": {
            "type": "object",
            "properties": {"space_id": {"type": "string"}},
            "required": ["space_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "pair",
        "description": "Show a QR code that lets a person add this account to one of their "
        "Pyunto diaries. They scan it in the Pyunto app, choose a diary and approve. Use this "
        "when list_spaces is empty. Returns the QR as an image, as a PNG file path, and as text.",
        "inputSchema": {
            "type": "object",
            "properties": {"operator": {"type": "string", "description":
                "Who runs this assistant, shown to the person before they approve "
                "(e.g. their own name)."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_attachment",
        "description": "Open the photo, video or document on a diary entry (read_thread shows "
        "which entries have one). Photos come back as images, videos as a few frames, "
        "documents as their text. The file is decrypted on this computer.",
        "inputSchema": {
            "type": "object",
            "properties": {"space_id": {"type": "string"}, "thread_id": {"type": "string"},
                           "message_id": {"type": "string"}},
            "required": ["space_id", "thread_id", "message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "join_space",
        "description": "Join a diary space from an invite link (pyunto://invite/…, https://…/invite/…) "
        "or a short invite code the human generated in the Pyunto app.",
        "inputSchema": {
            "type": "object",
            "properties": {"invite": {"type": "string"}},
            "required": ["invite"],
            "additionalProperties": False,
        },
    },
]


def _timestamp_of(message) -> float | None:  # noqa: ANN001
    """Epoch seconds for an entry, or None when the server did not say.

    An entry with no readable timestamp is counted rather than dropped: leaving a recorded
    dose out of a tally is worse than including one that may be a little old.
    """
    raw = (getattr(message, "raw", None) or {})
    stamp = raw.get("created_at") or raw.get("timestamp") or raw.get("client_created_at")
    if not stamp:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


class Content:
    """A tool result made of MCP content blocks (text and images) rather than one JSON text."""

    def __init__(self, blocks: list[dict]):
        self.blocks = blocks


def _image(path) -> dict:  # noqa: ANN001
    import base64  # noqa: PLC0415

    from .media import sniff_image  # noqa: PLC0415

    data = path.read_bytes()
    mime = (sniff_image(data[:16]) or ("image/jpeg", ""))[0]
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}


class MCPServer:
    def __init__(self, client: PyuntoClient, key_provider, identity_public_key: str):  # noqa: ANN001
        self.client = client
        self.keys = key_provider
        self.identity_public_key = identity_public_key
        self._incoming: queue.Queue[IncomingMessage] = queue.Queue()
        self._listener: threading.Thread | None = None
        self._out_lock = threading.Lock()

    # -- transport ----------------------------------------------------------------------

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch(req)

    def _send(self, obj: dict) -> None:
        with self._out_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def _dispatch(self, req: dict) -> None:
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}
        if method == "initialize":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pyunto-agent", "title": "Pyunto Diary", "version": _version()},
                "instructions": INSTRUCTIONS,
            }})
        elif method == "notifications/initialized":
            return
        elif method == "ping":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                payload = self._call(name, args)
                if isinstance(payload, Content):
                    content = payload.blocks
                else:
                    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
                    content = [{"type": "text", "text": text}]
                self._send({"jsonrpc": "2.0", "id": rid, "result": {"content": content}})
            except Exception as e:  # noqa: BLE001
                log.exception("tool %s failed", name)
                self._send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}})
        elif rid is not None:
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})

    # -- tools ----------------------------------------------------------------------

    def _pair(self, operator: str) -> Content:
        from .pairing import encode_payload, pairing_payload, render_qr, save_qr  # noqa: PLC0415

        text = encode_payload(pairing_payload(
            user_id=self.client.uuid, display_name=self.client.display_name,
            public_key=self.identity_public_key, operator=operator, runtime="self_hosted"))
        blocks: list[dict] = []
        png = self.client.attachment_dir.parent / "pairing-qr.png"
        try:
            png.parent.mkdir(parents=True, exist_ok=True)
            save_qr(text, png)
            blocks.append(_image(png))
            where = f"The QR code is also saved at {png} ."
        except Exception:  # noqa: BLE001 - Pillow or qrcode missing: text QR still works
            where = "(Install `pyunto-agent[qr]` and Pillow for a PNG of the code.)"
        ascii_qr = render_qr(text) or ""
        blocks.append({"type": "text", "text": (
            f"Ask the person to scan this QR code in the Pyunto app, choose a diary and approve. "
            f"The app shows that {self.client.display_name} is asking and who runs it. {where}\n"
            "Afterwards they should open that diary in the app once, so this account receives "
            "the key; then list_spaces will show it.\n\n"
            + (f"```\n{ascii_qr}\n```" if ascii_qr else f"Pairing payload: {text}"))})
        return Content(blocks)

    def _read_attachment(self, space_id: str, thread_id: str, message_id: str) -> Content:
        from .media import document_text, video_frames  # noqa: PLC0415

        msg = next((m for m in self.client.get_messages(thread_id, space_id)
                    if m.uuid == message_id), None)
        if msg is None or msg.attachment is None:
            raise ValueError("that entry has no photo, video or document")
        att = msg.attachment
        path = self.client.download_attachment(att)
        if att.kind == "image":
            return Content([_image(path), {"type": "text", "text": f"Photo saved at {path}"}])
        if att.kind == "video":
            frames = video_frames(path)
            if not frames:
                return Content([{"type": "text", "text":
                                 f"A video, saved at {path}. Install ffmpeg to see frames."}])
            return Content([{"type": "text", "text": f"{len(frames)} frames from the video at {path}:"},
                            *[_image(f) for f in frames]])
        body = document_text(path, att.mime)
        if body is None:
            body = ("(This format cannot be read here; install `pyunto-agent[docs]` for Word, "
                    "Excel, PowerPoint and PDF text.)")
        return Content([{"type": "text", "text": f"Document {att.name!r}, saved at {path}:\n\n{body}"}])

    def _call(self, name: str, a: dict) -> Any:
        if name == "whoami":
            spaces = self.client.list_spaces()
            return {
                "user_id": self.client.uuid,
                "display_name": (self.client.session.identity.display_name if self.client.session.identity else None),
                "base_url": self.client.session.base_url,
                "identity_public_key": self.identity_public_key,
                "spaces": [
                    {"space_id": s.get("uuid"), "name": s.get("name"), "has_key": self.keys.has_key(str(s.get("uuid")))}
                    for s in spaces
                ],
            }
        if name == "list_spaces":
            return [
                {
                    "space_id": s.get("uuid"),
                    "name": s.get("name"),
                    "members": [
                        {"user_id": u.get("uuid"), "name": u.get("display_name")} for u in (s.get("users") or [])
                    ],
                }
                for s in self.client.list_spaces()
            ]
        if name == "list_threads":
            limit = max(1, min(int(a.get("limit") or 20), 100))
            rows = self.client.list_threads(a["space_id"], limit=limit)
            out = []
            for t in rows:
                title = t.get("title") or ""
                out.append({
                    "thread_id": t.get("uuid"),
                    "title": marker_preview(title) or title,
                    "created_at": t.get("created_at"),
                    "message_count": t.get("message_count"),
                })
            return out
        if name == "read_thread":
            msgs = self.client.get_messages(a["thread_id"], a["space_id"])
            return [
                {
                    "message_id": m.uuid,
                    "from": m.sender_name,
                    "from_user_id": m.sender_uuid,
                    "mine": m.sender_uuid.lower() == self.client.uuid,
                    "text": marker_preview(m.text) or m.text or (
                        f"({m.attachment.label})" if m.attachment else ""),
                    **({"attachment": {"kind": m.attachment.kind,
                                       "open_with": "read_attachment"}} if m.attachment else {}),
                    "created_at": (m.raw or {}).get("created_at"),
                }
                for m in msgs
            ]
        if name == "pair":
            return self._pair(a.get("operator") or "")
        if name == "read_attachment":
            return self._read_attachment(a["space_id"], a["thread_id"], a["message_id"])
        if name == "wait_for_message":
            self._ensure_listener()
            deadline = time.time() + max(1, min(int(a.get("timeout_seconds") or 120), 600))
            want = (a.get("space_id") or "").lower()
            while time.time() < deadline:
                try:
                    m = self._incoming.get(timeout=min(1.0, max(0.05, deadline - time.time())))
                except queue.Empty:
                    continue
                if want and m.chat_space_id.lower() != want:
                    continue
                return {
                    "space_id": m.chat_space_id,
                    "thread_id": m.thread_id,
                    "message_id": m.uuid,
                    "from": m.sender_name,
                    "text": marker_preview(m.text) or m.text,
                }
            return {"timeout": True}
        if name == "post_entry":
            r = self.client.send(a["space_id"], a["text"], thread_id=a.get("thread_id"), silent=bool(a.get("silent")))
            return {"ok": True, "thread_id": (r.get("data") or {}).get("thread_id") or a.get("thread_id")}
        if name == "react":
            self.client.send(a["space_id"], reaction_marker(a["emoji"]), thread_id=a["thread_id"], silent=True)
            return {"ok": True}
        if name == "post_sticker":
            marker = sticker_marker(a["sticker_id"], "black", a.get("caption") or "")
            r = self.client.send(a["space_id"], marker, thread_id=a.get("thread_id"))
            return {"ok": True, "thread_id": (r.get("data") or {}).get("thread_id") or a.get("thread_id")}
        if name == "post_list_item":
            marker = item_marker(a["list_name"], a.get("emoji") or "📋", list(a["items"]))
            r = self.client.send(a["space_id"], marker, thread_id=a.get("thread_id"))
            return {"ok": True, "thread_id": (r.get("data") or {}).get("thread_id") or a.get("thread_id")}
        if name == "quick_list_stats":
            return self._quick_list_stats(
                a["space_id"], int(a.get("days") or 7), a.get("list_name")
            )
        if name == "list_members":
            members = []
            for u in self.client.list_space_users(a["space_id"]):
                kind = "agent" if (u.get("member_type") == "agent" or u.get("is_agent")) else "person"
                entry = {
                    "user_id": u.get("uuid"),
                    "name": u.get("display_name"),
                    "kind": kind,
                }
                if kind == "agent":
                    # Unknown is reported as unknown. Calling an unrecorded operator "the person
                    # who invited it" would dress somebody else's machine up as a safe one.
                    entry["operator"] = u.get("operator") or "unknown"
                    entry["runtime"] = u.get("runtime") or "unknown"
                members.append(entry)
            return {"members": members}
        if name == "join_space":
            sid = self.client.join(a["invite"])
            return {"ok": True, "space_id": sid,
                    "note": "The key for this space arrives once a member opens it in the app."}
        raise ValueError(f"unknown tool {name}")

    def _quick_list_stats(
        self, space_id: str, days: int, only_list: str | None
    ) -> dict[str, Any]:
        """Count quick-list entries across every thread in a space.

        The counts are assembled here, on this machine, from decrypted entries. The server
        stores quick-list posts as ciphertext like any other, so there is no endpoint that
        could answer this -- which is the point: the tally exists for whoever holds the key
        and for nobody else.
        """
        cutoff = time.time() - days * 86400
        counts: dict[str, dict[str, int]] = {}
        emojis: dict[str, str] = {}
        scanned = 0

        for thread in self.client.list_threads(space_id, limit=200):
            thread_id = str(thread.get("uuid") or "")
            if not thread_id:
                continue
            try:
                messages = self.client.get_messages(thread_id, space_id)
            except Exception:  # noqa: BLE001 - one unreadable thread must not lose the rest
                log.warning("could not read thread %s", thread_id, exc_info=True)
                continue
            for m in messages:
                parsed = parse_item_marker(m.text or "")
                if parsed is None:
                    continue
                created = _timestamp_of(m)
                if created is not None and created < cutoff:
                    continue
                list_name, emoji, items = parsed
                if only_list and list_name != only_list:
                    continue
                scanned += 1
                emojis.setdefault(list_name, emoji)
                bucket = counts.setdefault(list_name, {})
                for item in items:
                    bucket[item] = bucket.get(item, 0) + 1

        lists = [
            {
                "list_name": name,
                "emoji": emojis.get(name, "📋"),
                "total": sum(items.values()),
                "items": [
                    {"item": i, "count": c}
                    for i, c in sorted(items.items(), key=lambda kv: -kv[1])
                ],
            }
            for name, items in sorted(counts.items())
        ]
        return {"days": days, "entries": scanned, "lists": lists}

    def _ensure_listener(self) -> None:
        if self._listener and self._listener.is_alive():
            return
        me = self.client.uuid

        def on_message(m: IncomingMessage) -> None:
            if m.sender_uuid.lower() != me:
                self._incoming.put(m)

        self._listener = threading.Thread(target=self.client.listen, args=(on_message,), daemon=True)
        self._listener.start()

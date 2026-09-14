"""Pyunto client: REST calls plus a Socket.IO listener for incoming messages.

Server-behaviour notes that shape this code (all verified against pyunto-server/server/src):

* Login returns `token`, not `access_token`; there is no refresh endpoint (authController.ts:441).
* REST responses are snake_case, but Socket.IO payloads are camelCase *inside* `data`
  (messageController.ts:459-474). Two different shapes for the same message.
* The server broadcasts `new_message` to every thread member's room -- including the sender.
  Without a self-filter the robot answers itself forever (messageController.ts:498-508).
* Thread membership, not space membership, controls visibility (messageController.ts:295-302).
* `chat_space_id` is uppercased server-side; user UUIDs are lowercased. Compare case-insensitively.
* GET threads without `limit` returns everything and silently changes the sort order
  (chatSpaceController.ts:1110-1177), so `limit` is always passed.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
import socketio

from .auth import Session
from .crypto import (
    ENCRYPTED_PLACEHOLDER,
    CryptoError,
    _normalize_b64,
    decrypt_message,
    encrypt,
    encrypt_message,
    encryption_metadata,
    is_encrypted,
    parse_encrypted_content,
)
from .keys import SpaceKeyProvider  # noqa: F401

log = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """A message addressed to the robot, already decrypted."""

    uuid: str
    text: str
    thread_id: str
    chat_space_id: str
    sender_uuid: str
    sender_name: str
    #: UUIDs the sender marked as mentioned. In a shared space this is *everyone*,
    #: because the field decides who can SEE the thread, not who gets notified.
    mentioned_uuids: list[str] = field(default_factory=list)
    #: UUIDs the sender explicitly addressed (server field ``notify_users``), or None
    #: when the sender addressed nobody in particular. This is the field that says
    #: "this entry is for these people" -- see messageController.ts.
    notify_uuids: list[str] | None = None
    #: True when the sender chose "leave it quietly" (no push to anyone).
    silent: bool = False
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.sender_name}] {self.text}"


class PyuntoClient:
    """Talks to Pyunto as the agent's account."""

    def __init__(
        self,
        session: Session,
        key_provider: SpaceKeyProvider,
        timeout: float = 20.0,
    ):
        self.session = session
        self.keys = key_provider
        self.timeout = timeout
        self._sio: socketio.Client | None = None
        self._on_message: Callable[[IncomingMessage], None] | None = None
        self._stop = threading.Event()

    # -- identity -----------------------------------------------------------------

    @property
    def uuid(self) -> str:
        """The agent's own user UUID (lowercased)."""
        if self.session.identity is None:
            self.session.login()
        assert self.session.identity is not None
        return self.session.identity.uuid

    @property
    def display_name(self) -> str:
        """The agent's own display name, as written in a mention (``@Claude``)."""
        if self.session.identity is None:
            self.session.login()
        assert self.session.identity is not None
        return self.session.identity.display_name

    # -- REST ---------------------------------------------------------------------

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        """Authenticated request that retries once after re-login on a 401."""
        url = f"{self.session.base_url}{path}"
        kw.setdefault("timeout", self.timeout)
        headers = {**kw.pop("headers", {}), **self.session.auth_header()}
        resp = requests.request(method, url, headers=headers, **kw)
        if resp.status_code == 401:
            self.session.invalidate()
            headers = {**headers, **self.session.auth_header()}
            resp = requests.request(method, url, headers=headers, **kw)
        return resp

    def list_spaces(self) -> list[dict[str, Any]]:
        """All chat spaces the robot belongs to."""
        r = self._request("GET", "/api/chat-spaces")
        r.raise_for_status()
        return r.json().get("data", [])

    def list_space_users(self, chat_space_id: str) -> list[dict[str, Any]]:
        """Members of a space (uuid + display name). Used to tell a two-person diary,
        where the agent may answer freely, from a group where it must be addressed."""
        r = self._request("GET", f"/api/chat-spaces/{chat_space_id}/users")
        r.raise_for_status()
        return r.json().get("data", [])

    def list_threads(self, chat_space_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Threads in a space. `limit` is always sent -- omitting it changes the sort order."""
        r = self._request(
            "GET",
            f"/api/chat-spaces/{chat_space_id}/threads",
            params={"limit": limit, "offset": offset},
        )
        r.raise_for_status()
        return r.json().get("data", [])

    def get_messages(
        self, thread_id: str, chat_space_id: str | None = None
    ) -> list[IncomingMessage]:
        """Full message history of a thread, decrypted.

        chat_space_id is a fallback for messages that carry no encryption metadata to derive it
        from (e.g. plaintext ones).
        """
        r = self._request("GET", f"/api/messages/{thread_id}")
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            msg = self._decode_rest_message(m, chat_space_id)
            if msg is not None:
                out.append(msg)
        return out

    def join_with_invite_link(self, link_or_id: str) -> str | None:
        """Join a space from an invite link the human shared from the app.

        Accepts `pyunto://invite/<id>`, `https://.../invite/<id>`, or the bare id
        (same parsing as the app's InviteLinkEntrySheet). Returns the chat space id.
        """
        invite_id = link_or_id.strip()
        for marker in ("pyunto://invite/", "/invite/"):
            if marker in invite_id:
                invite_id = invite_id.split(marker, 1)[1]
        invite_id = invite_id.strip("/").split("?", 1)[0]
        from urllib.parse import quote
        r = self._request("POST", f"/api/chat-spaces/join/{quote(invite_id, safe='')}")
        if r.status_code >= 400:
            log.error("join failed: HTTP %s %s", r.status_code, r.text[:300])
            r.raise_for_status()
        data = r.json().get("data") or {}
        return (data.get("chat_space_id") or data.get("chatSpaceId") or data.get("uuid") or None)

    def join(self, invite: str) -> str | None:
        """Join with either an invite link/id or a short invite code."""
        if "invite" in invite or len(invite) > 12:
            return self.join_with_invite_link(invite)
        return self.join_with_code(invite)

    def send_image(
        self,
        chat_space_id: str,
        image_png: bytes,
        thread_id: str | None = None,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Post a picture. The bytes are encrypted here with the space key, as the apps do.

        `POST /api/images/upload/:threadId` takes the ciphertext as a multipart file, so the
        server stores something it cannot read. A thread_id is required -- there is no
        "new thread with a photo" on this endpoint -- so pass the thread you are replying in.
        """
        if not thread_id:
            raise ValueError("send_image needs a thread_id")
        key = self.keys.get_key(chat_space_id)
        enc = encrypt(image_png, key)
        nonce = base64.b64decode(_normalize_b64(enc.nonce))
        ciphertext = base64.b64decode(_normalize_b64(enc.ciphertext))
        # The wire format is the apps' own: a 4-byte little-endian nonce length, then the
        # nonce, then the ciphertext. Plain concatenation looks right and decrypts to
        # garbage, because the reader takes the first four bytes as a length -- which is
        # exactly what "Failed to load image" in the app means.
        blob = len(nonce).to_bytes(4, "little") + nonce + ciphertext
        files = {"file": ("frame.bin", blob, "application/octet-stream")}
        metadata = dict(encryption_metadata(chat_space_id))
        # The reader needs the nonce from the metadata, as the apps send it.
        metadata["nonce"] = enc.nonce
        data = {
            "encrypted_metadata": json.dumps(metadata),
            "client_created_at": datetime.now(timezone.utc).isoformat(),
        }
        if caption:
            data["caption"] = caption
        url = f"{self.session.base_url}/api/images/upload/{thread_id}"
        headers = self.session.auth_header()
        resp = requests.post(url, files=files, data=data, headers=headers, timeout=120)
        if resp.status_code == 401:
            self.session.invalidate()
            resp = requests.post(
                url, files=files, data=data, headers=self.session.auth_header(), timeout=120
            )
        if resp.status_code >= 400:
            log.error("image upload failed: HTTP %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        return resp.json()

    def leave_space(self, chat_space_id: str) -> None:
        """Leave a space (POST /api/chat-spaces/:uuid/leave)."""
        r = self._request("POST", f"/api/chat-spaces/{chat_space_id}/leave")
        if r.status_code >= 400:
            log.error("leave failed: HTTP %s %s", r.status_code, r.text[:300])
            r.raise_for_status()

    def join_with_code(self, invite_code: str) -> str | None:
        """Join a human's chat space using an invite code from the app."""
        r = self._request("POST", "/api/chat-spaces/join-with-code", json={"invite_code": invite_code})
        if r.status_code >= 400:
            log.error("join failed: HTTP %s %s", r.status_code, r.text[:300])
            r.raise_for_status()
        body = r.json()
        space_id = (body.get("data") or {}).get("uuid") or body.get("chat_space_id")
        log.info("joined chat space %s", space_id)
        return space_id

    def send(
        self,
        chat_space_id: str,
        text: str,
        thread_id: str | None = None,
        mentioned_users: list[str] | None = None,
        silent: bool = False,
    ) -> dict[str, Any]:
        """Post an encrypted message. thread_id=None creates a new thread.

        silent=True adds mentioned_users as thread participants but sends no push (the app's
        🤫 mode). Replies into an existing thread reach every thread member either way.

        Replies into an existing thread notify every thread member, so the robot does not
        need to mention anyone to be seen (messageController.ts:607).
        """
        key = self.keys.get_key(chat_space_id)
        enc = encrypt_message(text, key)
        payload: dict[str, Any] = {
            "content": ENCRYPTED_PLACEHOLDER,
            "encrypted_content": enc.to_wire(),
            "encryption_metadata": encryption_metadata(chat_space_id),
            "chat_space_id": chat_space_id,
        }
        if thread_id:
            payload["thread_id"] = thread_id
        if mentioned_users:
            payload["mentioned_users"] = [u.lower() for u in mentioned_users]
        if silent:
            payload["silent"] = True

        r = self._request("POST", "/api/messages", json=payload)
        if r.status_code >= 400:
            log.error("send failed: HTTP %s %s", r.status_code, r.text[:300])
            r.raise_for_status()
        return r.json()

    # -- decoding -----------------------------------------------------------------

    def _decrypt(self, chat_space_id: str, encrypted: Any, metadata: Any, fallback: str) -> str:
        """Decrypt a body, falling back to the plaintext field when not encrypted."""
        if not is_encrypted(metadata):
            return fallback or ""
        enc = parse_encrypted_content(encrypted)
        if enc is None:
            return fallback or ""
        try:
            return decrypt_message(enc, self.keys.get_key(chat_space_id))
        except (CryptoError, Exception) as e:  # noqa: BLE001
            log.warning("could not decrypt message in space %s: %s", chat_space_id, e)
            return fallback or ""

    def _decode_rest_message(
        self, m: dict[str, Any], chat_space_id: str | None = None
    ) -> IncomingMessage | None:
        """REST shape: snake_case.

        GET /api/messages/:threadId does not include `chat_space_id` on each message, so it is
        recovered from encryption_metadata (which carries it uppercased) or supplied by the caller.
        """
        sender = m.get("sender") or m.get("User") or {}
        meta = m.get("encryption_metadata")
        if isinstance(meta, str):
            try:
                import json as _json

                meta = _json.loads(meta)
            except ValueError:
                meta = None
        space_id = (
            m.get("chat_space_id")
            or (meta or {}).get("chat_space_id")
            or chat_space_id
            or ""
        )
        if not space_id:
            return None
        text = self._decrypt(
            space_id, m.get("encrypted_content"), m.get("encryption_metadata"), m.get("content", "")
        )
        return IncomingMessage(
            uuid=m.get("uuid", ""),
            text=text,
            thread_id=m.get("chat_thread_id", ""),
            chat_space_id=space_id,
            sender_uuid=str(sender.get("uuid", "")).lower(),
            sender_name=sender.get("display_name", "?"),
            raw=m,
        )

    def _decode_ws_message(self, data: dict[str, Any]) -> IncomingMessage | None:
        """Socket.IO shape: camelCase inside `data`."""
        sender = data.get("sender") or {}
        space_id = data.get("chatSpaceId") or ""
        if not space_id:
            return None
        text = self._decrypt(
            space_id,
            data.get("encryptedContent"),
            data.get("encryptionMetadata"),
            data.get("content", ""),
        )
        notify = data.get("notifyUsers")
        return IncomingMessage(
            uuid=data.get("uuid", ""),
            text=text,
            thread_id=data.get("threadId") or data.get("chatThreadId") or "",
            chat_space_id=space_id,
            sender_uuid=str(sender.get("uuid", "")).lower(),
            sender_name=sender.get("display_name", "?"),
            mentioned_uuids=[str(u).lower() for u in (data.get("mentionedUsers") or [])],
            notify_uuids=[str(u).lower() for u in notify] if isinstance(notify, list) else None,
            silent=bool(data.get("silent")),
            raw=data,
        )

    # -- realtime -----------------------------------------------------------------

    def listen(self, on_message: Callable[[IncomingMessage], None]) -> None:
        """Connect to Socket.IO and dispatch incoming messages until stop() is called."""
        self._on_message = on_message
        self._stop.clear()
        me = self.uuid

        sio = socketio.Client(logger=False, engineio_logger=False, reconnection=True)
        self._sio = sio

        @sio.event
        def connect() -> None:  # noqa: ANN202
            log.info("socket.io connected")

        @sio.event
        def connect_error(err: Any) -> None:  # noqa: ANN202
            log.error("socket.io connect error: %s", err)

        @sio.event
        def disconnect() -> None:  # noqa: ANN202
            log.info("socket.io disconnected")

        @sio.on("connection_established")
        def _established(payload: Any) -> None:  # noqa: ANN202
            spaces = (payload or {}).get("connectedChatSpaces", [])
            log.info("listening on %d chat space(s)", len(spaces))

        def _handle(event: str, payload: Any) -> None:
            data = (payload or {}).get("data") or {}
            msg = self._decode_ws_message(data)
            if msg is None:
                return
            # The server echoes our own posts back to us. Without this the robot loops forever.
            if msg.sender_uuid == me:
                return
            if not msg.text.strip():
                return
            log.info("<- %s (%s)", msg, event)
            if self._on_message:
                try:
                    self._on_message(msg)
                except Exception:  # noqa: BLE001 - a handler crash must not kill the listener
                    log.exception("message handler raised")

        @sio.on("new_message")
        def _new_message(payload: Any) -> None:  # noqa: ANN202
            _handle("new_message", payload)

        @sio.on("thread_created")
        def _thread_created(payload: Any) -> None:  # noqa: ANN202
            # A new thread carries its first message inline (null for image-only threads).
            data = (payload or {}).get("data") or {}
            first = data.get("firstMessage")
            if not first:
                return
            merged = {**first, "chatSpaceId": data.get("chatSpaceId"), "threadId": data.get("threadId")}
            _handle("thread_created", {"data": merged})

        sio.connect(
            self.session.base_url,
            auth={"token": self.session.token},
            socketio_path="/socket.io",
            transports=["websocket"],
        )
        try:
            while not self._stop.is_set():
                self._stop.wait(1.0)
        finally:
            try:
                sio.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        """Break out of listen()."""
        self._stop.set()

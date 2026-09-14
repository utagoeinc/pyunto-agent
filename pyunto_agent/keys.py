"""Chat-space key resolution, in the same order as the iOS client:

    local store -> wrapped key (unseal with our X25519 identity) -> legacy raw GET

* wrapped: GET /api/chat-spaces/:uuid/wrapped-key -> {data: {wrapped_key: envelope, ...}}.
  404 `no_wrapped_key` means no member has sealed the key for us yet (the app does that when it
  sees us join, or the next time a member opens the space).
* raw: GET /api/chat-spaces/:uuid/key returns the plain key for spaces created before August
  2026. Kept only for those; the server will remove it in Phase 3.

Keys are cached in memory and in the agent's data directory so a restart does not depend on the
network.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Protocol

import requests

from .auth import Session
from .crypto import CryptoError, string_to_key
from .identity import Envelope, IdentityStore, unseal

log = logging.getLogger(__name__)


class KeyError_(Exception):
    """Could not obtain a space key."""


class SpaceKeyProvider(Protocol):
    def get_key(self, chat_space_id: str) -> bytes: ...


class SpaceKeyStore:
    """On-disk cache of space keys (0600 JSON), keyed by lowercase space id."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "space_keys.json"

    def load(self) -> dict[str, bytes]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return {k: base64.b64decode(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            log.warning("space key cache unreadable; ignoring")
            return {}

    def save(self, keys: dict[str, bytes]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({k: base64.b64encode(v).decode() for k, v in keys.items()}, f)


class WrappedSpaceKeyProvider:
    """Local cache -> wrapped key (unseal) -> legacy raw GET."""

    def __init__(
        self,
        session: Session,
        identity: IdentityStore,
        data_dir: Path,
        timeout: float = 15.0,
        allow_raw_fallback: bool = True,
    ):
        self._session = session
        self._identity = identity
        self._store = SpaceKeyStore(data_dir)
        self._timeout = timeout
        self._allow_raw = allow_raw_fallback
        self._cache: dict[str, bytes] = self._store.load()
        self._lock = threading.Lock()

    def has_key(self, chat_space_id: str) -> bool:
        return chat_space_id.lower() in self._cache

    def get_key(self, chat_space_id: str) -> bytes:
        cache_key = chat_space_id.lower()
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        key = self._fetch_wrapped(chat_space_id)
        if key is None and self._allow_raw:
            key = self._fetch_raw(chat_space_id)
        if key is None:
            raise KeyError_(
                f"no key for space {chat_space_id}: nobody has shared it with this agent yet. "
                "Ask a member to open the space in the Pyunto app once (that distributes the key)."
            )
        with self._lock:
            self._cache[cache_key] = key
            self._store.save(self._cache)
        return key

    def _get(self, path: str) -> requests.Response:
        url = f"{self._session.base_url}{path}"
        resp = requests.get(url, headers=self._session.auth_header(), timeout=self._timeout)
        if resp.status_code == 401:
            self._session.invalidate()
            resp = requests.get(url, headers=self._session.auth_header(), timeout=self._timeout)
        return resp

    def _fetch_wrapped(self, chat_space_id: str) -> bytes | None:
        try:
            resp = self._get(f"/api/chat-spaces/{chat_space_id}/wrapped-key")
        except requests.RequestException as e:
            raise KeyError_(f"could not fetch wrapped key: {e}") from e
        if resp.status_code == 404:
            log.info("no wrapped key yet for %s", chat_space_id)
            return None
        if resp.status_code == 403:
            raise KeyError_(f"not a member of chat space {chat_space_id}")
        if resp.status_code != 200:
            raise KeyError_(f"wrapped key request failed: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json().get("data") or {}
        wrapped = data.get("wrapped_key")
        if isinstance(wrapped, str):
            wrapped = json.loads(wrapped)
        try:
            plaintext = unseal(Envelope.from_wire(wrapped), self._identity.private_key)
        except CryptoError as e:
            # The envelope was sealed for a different public key (e.g. the identity file was
            # regenerated). Re-uploading our public key makes the app re-seal on next open.
            raise KeyError_(f"could not unseal space key for {chat_space_id}: {e}") from e
        key = string_to_key(plaintext.decode("utf-8"))
        log.info("unsealed space key for %s", chat_space_id)
        return key

    def _fetch_raw(self, chat_space_id: str) -> bytes | None:
        try:
            resp = self._get(f"/api/chat-spaces/{chat_space_id}/key")
        except requests.RequestException as e:
            raise KeyError_(f"could not fetch raw key: {e}") from e
        if resp.status_code != 200:
            return None
        raw = (resp.json().get("data") or {}).get("encrypted_key")
        if not raw:
            return None
        log.info("resolved legacy raw key for %s", chat_space_id)
        return string_to_key(raw)

"""The agent's E2EE identity: an X25519 key pair, registered with the server.

Mirrors the iOS implementation (E2EEKeyManager + CryptoService) byte for byte:

* identity = Curve25519 (X25519) key pair; the public key is uploaded with
  POST /api/keys/upload {identityKey, signedPreKey, signedPreKeyId, signedPreKeySignature,
  oneTimePreKeys, keyScheme: "curve25519"}.
* a space key is delivered to us as a *sealed box* envelope
  {ciphertext: b64(ct||tag16), nonce: b64(12B), ephemeralPublicKey: b64(32B raw X25519)}:
  X25519(ephemeral, our private) -> HKDF-SHA256(salt="", info="", 32B) -> AES-256-GCM.
  The plaintext is the UTF-8 of the base64 space key (keyToString), which string_to_key decodes.

The private key never leaves this machine. It is stored in a 0600 file under the agent's data
directory; the app's members seal the space key for it when it joins a space (member_joined ->
healWrappedKeys on their side), so nothing here needs the human's keys.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .crypto import CryptoError, _normalize_b64

log = logging.getLogger(__name__)


@dataclass
class Envelope:
    ciphertext: str
    nonce: str
    ephemeral_public_key: str

    @classmethod
    def from_wire(cls, obj: dict) -> "Envelope":
        try:
            return cls(
                ciphertext=obj["ciphertext"],
                nonce=obj["nonce"],
                ephemeral_public_key=obj["ephemeralPublicKey"],
            )
        except (KeyError, TypeError) as e:
            raise CryptoError(f"malformed envelope: {obj!r}") from e


def _b64d(s: str) -> bytes:
    return base64.b64decode(_normalize_b64(s))


def unseal(envelope: Envelope, private_key: X25519PrivateKey) -> bytes:
    """Open a sealed box addressed to `private_key`; returns the plaintext bytes."""
    eph = X25519PublicKey.from_public_bytes(_b64d(envelope.ephemeral_public_key))
    shared = private_key.exchange(eph)
    key = HKDF(algorithm=SHA256(), length=32, salt=b"", info=b"").derive(shared)
    ct = _b64d(envelope.ciphertext)
    nonce = _b64d(envelope.nonce)
    if len(ct) < 16 or len(nonce) != 12:
        raise CryptoError("envelope ciphertext/nonce has an unexpected length")
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as e:  # noqa: BLE001
        raise CryptoError("could not open sealed box (wrong identity key?)") from e


def seal(plaintext: bytes, recipient_public_key: X25519PublicKey) -> Envelope:
    """Seal `plaintext` for a recipient (used to hand a space key to another member)."""
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(recipient_public_key)
    key = HKDF(algorithm=SHA256(), length=32, salt=b"", info=b"").derive(shared)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return Envelope(
        ciphertext=base64.b64encode(ct).decode(),
        nonce=base64.b64encode(nonce).decode(),
        ephemeral_public_key=base64.b64encode(
            eph.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode(),
    )


class IdentityStore:
    """Loads or creates the agent's identity key pair on disk."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "identity.json"
        self._private: X25519PrivateKey | None = None

    @property
    def private_key(self) -> X25519PrivateKey:
        if self._private is None:
            self._private = self._load_or_create()
        return self._private

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw).decode()

    def _load_or_create(self) -> X25519PrivateKey:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            return X25519PrivateKey.from_private_bytes(_b64d(data["private_key"]))
        key = X25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"private_key": base64.b64encode(raw).decode(), "scheme": "curve25519"}, f)
        log.info("created new identity key at %s", self.path)
        return key

    def upload(self, client) -> None:  # noqa: ANN001 - PyuntoClient (avoid import cycle)
        """Register (or re-register) the public key so members can seal space keys for us."""
        body = {
            "identityKey": self.public_key_b64,
            # The server schema still has Signal-era fields; the iOS client fills them like this.
            "signedPreKey": self.public_key_b64,
            "signedPreKeyId": 1,
            "signedPreKeySignature": "",
            "oneTimePreKeys": [],
            "keyScheme": "curve25519",
        }
        r = client._request("POST", "/api/keys/upload", json=body)
        if r.status_code >= 400:
            raise CryptoError(f"identity upload failed: HTTP {r.status_code} {r.text[:200]}")
        log.info("identity public key registered")

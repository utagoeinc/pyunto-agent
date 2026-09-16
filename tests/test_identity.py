"""Byte-compatibility with the iOS/Android/server sealed-box envelope (E2EE_IMPLEMENTATION.md)."""

import base64

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from pyunto_agent.crypto import string_to_key
from pyunto_agent.identity import Envelope, seal, unseal
from pyunto_agent.markers import item_marker, marker_preview

FIXTURE = {
    "recipient_private_key_b64": "oKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr8=",
    "recipient_public_key_b64": "YFpyXSpK3+6xop4X7dYhwbdZPujNvESsbEq24vgF0jw=",
    "space_key_b64": "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8=",
    "envelope": {
        "ciphertext": "5M2pGTniayZngYlc8P+gWApaYFqd2XxYd69c011bXv4Vp0C9ecEIx4M0r/WZ4W/nByXhPAKhYnUd1tHb",
        "nonce": "AAECAwQFBgcICQoL",
        "ephemeralPublicKey": "3CzKMejkO72R3/fkdcyjNH60eBB9W9dlq6SuSjDDXUQ=",
    },
}


def test_unseal_matches_fixture():
    sk = X25519PrivateKey.from_private_bytes(base64.b64decode(FIXTURE["recipient_private_key_b64"]))
    pt = unseal(Envelope.from_wire(FIXTURE["envelope"]), sk)
    assert pt.decode() == FIXTURE["space_key_b64"]
    assert string_to_key(pt.decode()) == base64.b64decode(FIXTURE["space_key_b64"])


def test_seal_round_trip():
    sk = X25519PrivateKey.generate()
    env = seal(b"hello", sk.public_key())
    assert unseal(env, sk) == b"hello"


def test_item_marker_preview():
    m = item_marker("medicine", "💊", ["blood pressure", "stomach", "stomach"])
    assert m == "!item:medicine:💊:blood pressure|stomach|stomach"
    assert marker_preview(m) == "💊 blood pressure, stomach ×2 (medicine)"

"""Pairing started by the agent: it shows a code, a person scans it with the Pyunto app.

The app-first flow (the app shows a pairing code, you paste it on the machine) suits someone
setting up a robot they were handed. It suits an agent badly. Whoever is running OpenClaw or
Claude Code is already sitting at that terminal; asking them to pick up a phone, open a space,
issue a code, and type a UUID back into the terminal is three context switches to express one
intention -- "let this program into that diary".

Turned around, it is one: the agent prints a QR, the phone scans it, the person picks the
space and confirms. That is the same gesture as adding a friend, which people already know.

The payload deliberately carries no secret. It names the account asking to be let in; the
decision, and the authority to act on it, stay with the person holding the phone. A leaked
code therefore grants nothing -- the worst it can do is let someone ask.
"""

from __future__ import annotations

import json
from typing import Any

# The app matches on this. Kept distinct from "device-link" (a second device for one person)
# and from the friend QR, because the question being answered is different: this one is
# "should a program be allowed to read this diary", which deserves its own confirmation.
PAYLOAD_TYPE = "agent-pairing"
PAYLOAD_VERSION = 1


def pairing_payload(
    user_id: str,
    display_name: str,
    public_key: str,
    operator: str = "",
    runtime: str = "self_hosted",
) -> dict[str, Any]:
    """What the agent puts in the QR.

    `operator` and `runtime` are shown to the person before they confirm, and stored on the
    membership afterwards, so that everyone in the space -- not only whoever scanned -- can
    see who runs this thing and where the diary gets decrypted.
    """
    return {
        "type": PAYLOAD_TYPE,
        "version": PAYLOAD_VERSION,
        "user_id": user_id,
        "display_name": display_name,
        # The X25519 identity key. The app seals the space key to it on approval, so a person
        # can check that the key they are about to encrypt for is the one on screen.
        "public_key": public_key,
        "operator": operator,
        "runtime": runtime,
    }


def encode_payload(payload: dict[str, Any]) -> str:
    """Compact JSON. QR capacity is limited and every space costs a module."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def render_qr(text: str, big: bool = False) -> str | None:
    """The QR as terminal text, or None if no renderer is installed.

    `qrcode` is an optional dependency: an agent running headless in CI has no use for it, and
    a hard requirement would make every install pay for a feature most runs never reach.

    This payload is ~210 characters, which is a version-10 symbol: 59x59 modules, four times
    the data of an invite link. Half-block packing keeps that square small enough to fit a
    terminal, but each module ends up half a character tall, and phones struggle to resolve
    it. `big` draws one module per two spaces instead -- roughly four times the area, needing
    a wide window, and worth it when a scan will not catch.
    """
    try:
        import qrcode  # noqa: PLC0415 - optional, imported where it is used
    except ImportError:
        return None

    qr = qrcode.QRCode(border=2 if big else 1)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    if big:
        # Two spaces per module so the square is not squashed by the cell aspect ratio.
        return "\n".join(
            "".join("  " if not cell else "██" for cell in row) for row in matrix
        )

    # Two rows per line with half-block characters, so the square is not stretched to twice
    # its height by the terminal's cell aspect ratio -- a stretched QR scans poorly.
    lines = []
    for top in range(0, len(matrix), 2):
        row = ""
        for col in range(len(matrix[top])):
            upper = matrix[top][col]
            lower = matrix[top + 1][col] if top + 1 < len(matrix) else False
            if upper and lower:
                row += "█"
            elif upper:
                row += "▀"
            elif lower:
                row += "▄"
            else:
                row += " "
        lines.append(row)
    return "\n".join(lines)


def wait_for_scan(client, timeout: float = 600.0, poll: float = 2.0):  # noqa: ANN001
    """Block until somebody scans the square and lets this account into a space.

    Printing a QR code and exiting makes the person run a second command, and -- worse --
    gives them no way to tell whether the scan worked. The square just sits there. So the
    code that drew it waits for the answer, and the caller carries straight on into
    listening: scan, approve, and the agent is live.

    Returns the space id that appeared, or None if nobody scanned in time. Polls rather than
    subscribes because membership is granted server-side and there is no event for it; the
    interval is slow enough to be invisible in a log and fast enough to feel immediate.
    """
    import time

    def shared_spaces() -> set[str]:
        try:
            return {
                str(s.get("uuid"))
                for s in client.list_spaces()
                if not (s.get("is_self") or s.get("isSelf"))
            }
        except Exception:  # noqa: BLE001 - a hiccup while polling is not a failure to pair
            return set()

    before = shared_spaces()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        new = shared_spaces() - before
        if new:
            return sorted(new)[0]
    return None

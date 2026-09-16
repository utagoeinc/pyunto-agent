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
from pathlib import Path
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
    """Block until this account is in a shared space, and return it.

    Printing a QR code and exiting makes the person run a second command, and -- worse --
    gives them no way to tell whether the scan worked. The QR code just sits there. So the
    code that drew it waits for the answer, and the caller carries straight on.

    Returns a space id, or None if nobody scanned in time.

    It returns a space it was ALREADY in, immediately, rather than waiting for a new one to
    appear. That is not a shortcut: a robot re-paired into the same space -- which is what
    happens every time somebody runs this twice -- joins nothing new, so waiting for a change
    waits forever. The QR code was scanned, the app said yes, and the terminal sat there
    saying "waiting for the scan…". Being already paired is success, not a reason to block.

    Polls rather than subscribes because membership is granted server-side and there is no
    event for it; the interval is slow enough to be invisible in a log and fast enough to
    feel immediate.
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
    if before:
        return sorted(before)[0]

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        new = shared_spaces() - before
        if new:
            return sorted(new)[0]
    return None


def save_qr(text: str, path: str | Path) -> Path:
    """Write the pairing QR to a file, and return where it went.

    A terminal QR is for the person sitting at the machine. A service handing this to clients
    needs a file: on a booking page, in a welcome email, printed on a card by the door. The
    format follows the extension.

    Two formats. SVG needs nothing beyond `qrcode` and scales to any size, so it is the right
    choice for print. PNG needs Pillow, which this package does not require -- if it is
    missing, that is said plainly rather than raised as an ImportError about a module the
    reader never mentioned. Anything else is refused: `qrcode` writes PNG bytes regardless of
    the extension, so a `.jpg` would be a PNG under the wrong name.
    """
    import qrcode

    path = Path(path)
    qr = qrcode.QRCode(border=4, box_size=10)
    qr.add_data(text)
    qr.make(fit=True)

    if path.suffix.lower() == ".svg":
        import qrcode.image.svg

        qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(str(path))
        return path

    if path.suffix.lower() != ".png":
        # `qrcode` writes PNG bytes whatever the extension says, so a .jpg would be a PNG
        # wearing the wrong name -- and something that will not open where somebody uploads
        # it. Refuse rather than produce that.
        raise RuntimeError(
            f"Cannot write {path.suffix or 'a file with no extension'}. Use .svg (nothing "
            f"extra needed, scales for print) or .png (needs Pillow)."
        )

    try:
        import PIL  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "PNG needs Pillow, which pyunto-agent does not install.\n"
            "    pip install pillow\n"
            f"Or write an SVG instead, which needs nothing extra: "
            f"--image {path.with_suffix('.svg')}"
        ) from e
    qr.make_image().save(str(path))
    return path

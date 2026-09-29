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


def wait_for_scan(  # noqa: ANN201
    client,  # noqa: ANN001
    timeout: float = 600.0,
    poll: float = 2.0,
    interactive: bool | None = None,
    wait_for_enter=None,  # noqa: ANN001
):
    """Block until the QR code is scanned, and return the space it was scanned into.

    Printing a QR code and exiting makes the person run a second command, and -- worse --
    gives them no way to tell whether the scan worked. So the code that drew it waits for the
    answer, and the caller carries straight on. Returns a space id, or None if nobody scanned
    in time.

    An account that is ALREADY in a diary is the hard case, and both simple answers are wrong:

    * Returning that diary at once (the old behaviour) answers before anyone has scanned. The
      person then scans the code into a *different* diary, and the program is already
      listening to the old one -- it looks connected and ignores everything they write there.
    * Waiting only for a new diary hangs when they re-approve the same one, because joining a
      diary you are in changes nothing the program can see.

    So at a terminal it asks: scan to add another diary, or press Enter to keep the one it is
    in. Without a terminal (a service, a test) nobody can press Enter, so it keeps the old
    behaviour and returns the existing diary at once.

    Polls rather than subscribes because membership is granted server-side and there is no
    event for it; the interval is slow enough to be invisible in a log and fast enough to
    feel immediate.
    """
    import sys
    import time

    def shared_spaces() -> list[dict]:
        try:
            return [s for s in client.list_spaces() if not (s.get("is_self") or s.get("isSelf"))]
        except Exception:  # noqa: BLE001 - a hiccup while polling is not a failure to pair
            return []

    # Server order: the diary with the most recent activity first.
    before = shared_spaces()
    before_ids = {str(s.get("uuid")) for s in before}
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if before and not interactive:
        return str(before[0].get("uuid"))
    if before:
        current = before[0].get("name") or str(before[0].get("uuid"))
        others = len(before) - 1
        also = f" (and {others} other diar{'y' if others == 1 else 'ies'})" if others else ""
        print(f'Already in "{current}"{also}. Scan the code to add it to another diary,')
        print(f'or press Enter to keep using "{current}".')
    wait = wait_for_enter or _wait_for_enter

    deadline = time.time() + timeout
    while time.time() < deadline:
        if before:
            if wait(poll):
                return str(before[0].get("uuid"))
        else:
            time.sleep(poll)
        new = [s for s in shared_spaces() if str(s.get("uuid")) not in before_ids]
        if new:
            return str(new[0].get("uuid"))
    return str(before[0].get("uuid")) if before else None


def _wait_for_enter(seconds: float) -> bool:
    """Wait up to `seconds` for Enter on the terminal. True if it was pressed."""
    import sys

    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415
        import time  # noqa: PLC0415

        end = time.time() + seconds
        while time.time() < end:
            if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                return True
            time.sleep(0.05)
        return False
    import select  # noqa: PLC0415

    ready, _, _ = select.select([sys.stdin], [], [], seconds)
    if ready:
        sys.stdin.readline()
        return True
    return False


def save_qr(text: str, path: str | Path) -> Path:
    """Write the pairing QR to a file, and return where it went.

    A terminal QR is for the person sitting at the machine. A service handing this to clients
    needs a file: on a booking page, in a welcome email, printed on a card by the door. The
    format follows the extension.

    Two formats. SVG needs nothing beyond `qrcode` and scales to any size, so it is the right
    choice for print. PNG uses Pillow when it is installed and pure-Python pypng otherwise.
    Anything else is refused: `qrcode` writes PNG bytes regardless of
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

        qr.make_image().save(str(path))
    except ImportError:
        # Without Pillow, pypng (pure Python, a dependency of this package) writes the PNG.
        from qrcode.image.pure import PyPNGImage

        qr.make_image(image_factory=PyPNGImage).save(str(path))
    return path

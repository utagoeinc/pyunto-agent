"""In-body markers used by the apps (4-OS spec). Kept minimal: recognise and render as text.

* `!react:<emoji>`                      reaction toggle (not a real entry)
* `!sticker:<id>:<color>:<caption>`     sticker entry
* `!item:<list>:<emoji>:<a>|<b>|<b>`    quick-list entry (repeats = count)
* `!list:<name>:<emoji>:<a>|<b>`        legacy shared-list definition
"""

from __future__ import annotations

from collections import Counter

STICKER_CAPTIONS = {
    "000000": "hello", "000001": "thanks", "000002": "sorry", "000003": "good night", "000004": "cheer up",
}


def is_marker(text: str) -> bool:
    return text.startswith(("!react:", "!sticker:", "!item:", "!list:"))


def is_reaction(text: str) -> bool:
    return text.startswith("!react:")


def marker_preview(text: str) -> str | None:
    """Human-readable rendering of a marker, or None for non-markers."""
    if text.startswith("!sticker:"):
        parts = text[len("!sticker:"):].split(":", 2)
        caption = parts[2].strip() if len(parts) == 3 and parts[2].strip() else STICKER_CAPTIONS.get(parts[0], "")
        return f"🧸 {caption}".strip()
    if text.startswith("!item:"):
        parts = text[len("!item:"):].split(":", 2)
        if len(parts) != 3:
            return None
        items = [i.strip() for i in parts[2].split("|") if i.strip()]
        counts = Counter(items)
        rendered = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in counts.items())
        return f"{parts[1] or '📋'} {rendered} ({parts[0]})"
    if text.startswith("!list:"):
        parts = text[len("!list:"):].split(":", 2)
        if len(parts) != 3:
            return None
        items = [i.strip() for i in parts[2].split("|") if i.strip()]
        return f"📋 {parts[1]} {parts[0]}: " + ", ".join(items)
    if text.startswith("!react:"):
        return None
    return None


def sticker_marker(sticker_id: str, color: str = "black", caption: str = "") -> str:
    return f"!sticker:{sticker_id}:{color}:{caption.replace(chr(10), ' ').strip()}"


def reaction_marker(emoji: str) -> str:
    return f"!react:{emoji}"


def item_marker(list_name: str, emoji: str, items: list[str]) -> str:
    safe = [i.replace("|", "｜").replace("\n", " ").strip() for i in items if i.strip()]
    return f"!item:{list_name.replace(':', '：')}:{emoji or '📋'}:{'|'.join(safe)}"


def parse_item_marker(text: str) -> tuple[str, str, list[str]] | None:
    """`!item:<list>:<emoji>:<a>|<b>|<b>` -> (list name, emoji, items), or None.

    Repeats are meaningful: writing the same item twice means it happened twice, which is what
    makes "how many times this week" answerable at all. So the list is returned as written,
    never de-duplicated.
    """
    if not text.startswith("!item:"):
        return None
    parts = text[len("!item:"):].split(":", 2)
    if len(parts) < 3:
        return None
    list_name, emoji, joined = parts[0], parts[1], parts[2]
    items = [i.strip() for i in joined.split("|") if i.strip()]
    if not items:
        return None
    return list_name, emoji, items

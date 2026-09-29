"""Reply backends: given the conversation so far and a new entry, produce a reply.

All backends see the same `Turn` list (oldest first) and return plain text. Anything that can
turn text into text can be a diary partner:

* ClaudeAPIBackend    -- Anthropic Messages API (requests only; no SDK needed).
* CommandBackend      -- any CLI: JSON on stdin, JSON {"reply": ...} (or plain text) on stdout.
                         Works with `claude -p`, and with other agents that read stdin.
* HTTPBackend         -- POST JSON to a URL, read {"reply": ...}.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from .media import Attachment, document_text, video_frames

log = logging.getLogger(__name__)


@dataclass
class Turn:
    role: str  # "user" (a human member) or "assistant" (this agent)
    name: str
    text: str
    at: str = ""  # ISO timestamp if known
    #: Photos, videos and documents on this entry, already downloaded (see media.py).
    attachments: list[Attachment] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"role": self.role, "name": self.name, "text": self.text, "at": self.at,
                "attachments": [a.as_json() for a in self.attachments]}


@dataclass
class Context:
    space_name: str
    thread_id: str
    turns: list[Turn]
    persona: str
    language_hint: str = ""
    # Which space the entry came from. A backend that only writes text never needs this --
    # Bridge replies for it -- but one that posts on its own (a robot narrating progress)
    # does. Defaulted so existing backends and callers are untouched.
    chat_space_id: str = ""
    # Who wrote the entry being answered. A backend that posts on its own needs it to say
    # who the reply is for, so that person's phone notifies them; without it the server
    # falls back to every thread member and their own settings decide.
    sender_uuid: str = ""
    extra: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {
            "space": self.space_name,
            "thread_id": self.thread_id,
            "persona": self.persona,
            "turns": [t.as_json() for t in self.turns],
            "extra": self.extra,
        }


class Backend(Protocol):
    name: str

    #: True when `reply` moves something in the world rather than writing a sentence.
    #:
    #: The Bridge reads this to decide how strict the "is this addressed to me" gate should
    #: be: a text agent answering the wrong entry is noise, while a robot acting on the
    #: wrong entry does something physical nobody asked for. Declared on the backend rather
    #: than passed by each caller so that a new integration is safe without remembering a
    #: flag -- the default below is the harmless one.
    acts_physically: bool = False

    def reply(self, ctx: Context) -> str | None: ...


DEFAULT_PERSONA = (
    "You are the other half of a private exchange diary in the Pyunto app. "
    "Reply as a warm, attentive companion: short (1-4 sentences), in the same language as the "
    "latest entry, never lecturing, never inventing facts about the writer. "
    "You may use one emoji at most. Do not add greetings or sign-offs."
)


class ClaudeAPIBackend:
    name = "claude-api"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-5",
        max_tokens: int = 400,
        base_url: str = "https://api.anthropic.com",
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")

    def reply(self, ctx: Context) -> str | None:
        messages: list[dict] = []
        for t in ctx.turns:
            role = "assistant" if t.role == "assistant" else "user"
            text = t.text if role == "assistant" else f"[{t.name}] {t.text}"
            blocks = [{"type": "text", "text": text}]
            if role == "user":
                for att in t.attachments:
                    blocks.extend(attachment_blocks(att))
            # The API requires alternating roles; merge consecutive same-role turns.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        if not messages or messages[-1]["role"] != "user":
            return None
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": ctx.persona or DEFAULT_PERSONA,
            "messages": messages,
        }
        r = requests.post(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=120,
        )
        if r.status_code >= 400:
            log.error("claude api error %s: %s", r.status_code, r.text[:300])
            return None
        parts = r.json().get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        return text or None


# -- attachments -----------------------------------------------------------------------------

#: The Messages API takes images up to 5 MB each and PDFs up to 32 MB.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_PDF_BYTES = 32 * 1024 * 1024
_API_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _image_block(path: Path, mime: str) -> dict | None:
    if mime in _API_IMAGE_TYPES and path.stat().st_size <= _MAX_IMAGE_BYTES:
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(path)}}
    try:  # HEIC, or too large: re-encode as a JPEG if Pillow is available
        from PIL import Image  # noqa: PLC0415

        img = Image.open(path)
        img.thumbnail((2048, 2048))
        out = path.with_name(path.stem + "_api.jpg")
        img.convert("RGB").save(out, "JPEG", quality=85)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": _b64(out)}}
    except Exception as e:  # noqa: BLE001
        log.warning("cannot pass %s to the model: %s", path.name, e)
        return None


def attachment_blocks(att: Attachment) -> list[dict]:
    """Content blocks that show a model what was attached, or say plainly that it cannot."""
    if att.path is None or not att.path.exists():
        return [{"type": "text", "text": f"[{att.label} attached, but it could not be opened]"}]
    if att.kind == "image":
        block = _image_block(att.path, att.mime)
        return [block] if block else [{"type": "text", "text": "[a photo that could not be read]"}]
    if att.kind == "video":
        frames = [b for f in video_frames(att.path) if (b := _image_block(f, "image/jpeg"))]
        if not frames:
            return [{"type": "text", "text": "[a video; frames could not be extracted (install ffmpeg)]"}]
        return [{"type": "text", "text": f"[a video; {len(frames)} evenly spaced frames follow]"}, *frames]
    name = att.name or att.path.name
    if att.path.suffix.lower() == ".pdf" and att.path.stat().st_size <= _MAX_PDF_BYTES:
        return [{"type": "document", "title": name,
                 "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(att.path)}}]
    text = document_text(att.path, att.mime)
    if text is None:
        return [{"type": "text", "text": f"[attached document {name!r}; its format cannot be read here]"}]
    return [{"type": "text", "text": f"[attached document {name!r}]\n{text}"}]


class CommandBackend:
    """Run a command per message. stdin: JSON context. stdout: JSON {"reply": ...} or text.

    Example (Claude Code as the partner):
        pyunto-agent run --backend command --command 'claude -p --output-format json'
    The prompt is also passed as the last argument when `{prompt}` appears in the command, for
    tools that take it positionally.
    """

    name = "command"

    def __init__(self, command: str, timeout: float = 300.0):
        self.command = command
        self.timeout = timeout

    def reply(self, ctx: Context) -> str | None:
        prompt = self._prompt(ctx)
        argv = shlex.split(self.command)
        dirs = sorted({str(a.path.parent) for t in ctx.turns for a in t.attachments if a.path})
        if dirs and Path(argv[0]).name.startswith("claude") and "--add-dir" not in argv:
            # Claude Code reads files only inside its allowed folders; the attachments live in
            # the agent's own data folder, so name it. Verified with `claude -p` on an image.
            for d in dirs:
                argv += ["--add-dir", d]
        if any("{prompt}" in a for a in argv):
            argv = [a.replace("{prompt}", prompt) for a in argv]
            stdin = None
        else:
            stdin = json.dumps({**ctx.as_json(), "prompt": prompt}, ensure_ascii=False)
        try:
            proc = subprocess.run(
                argv, input=stdin, capture_output=True, text=True, timeout=self.timeout
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("command backend failed: %s", e)
            return None
        if proc.returncode != 0:
            log.error("command exited %s: %s", proc.returncode, proc.stderr[-500:])
            return None
        out = proc.stdout.strip()
        return _extract_reply(out)

    @staticmethod
    def _prompt(ctx: Context) -> str:
        lines = [ctx.persona or DEFAULT_PERSONA, "", f"Diary space: {ctx.space_name}", ""]
        attached = False
        for t in ctx.turns:
            who = "You" if t.role == "assistant" else t.name
            lines.append(f"{who}: {t.text}")
            for a in t.attachments:
                if a.path is None:
                    lines.append(f"  ({a.label} attached, but it could not be opened)")
                    continue
                attached = True
                lines.append(f"  (attached {a.label}: {a.path})")
                if a.kind == "video":
                    for f in video_frames(a.path):
                        lines.append(f"  (frame from that video: {f})")
        if attached:
            lines += ["", "Open the attached files listed above before replying; they are "
                          "part of what the writer shared."]
        lines += ["", "Write only your next diary reply."]
        return "\n".join(lines)


class HTTPBackend:
    """POST the context as JSON. Attachments carry their local path and, up to 8 MB each,
    their bytes as ``data_base64`` -- the service may run on another machine."""

    name = "http"
    max_inline_bytes = 8 * 1024 * 1024

    def _body(self, ctx: Context) -> dict:
        body = ctx.as_json()
        for turn, src in zip(body["turns"], ctx.turns):
            for item, att in zip(turn["attachments"], src.attachments):
                if att.path and att.path.exists() and att.path.stat().st_size <= self.max_inline_bytes:
                    item["data_base64"] = _b64(att.path)
        return body

    def __init__(self, url: str, headers: dict | None = None, timeout: float = 120.0):
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def reply(self, ctx: Context) -> str | None:
        try:
            r = requests.post(self.url, json=self._body(ctx), headers=self.headers, timeout=self.timeout)
        except requests.RequestException as e:
            log.error("http backend failed: %s", e)
            return None
        if r.status_code >= 400:
            log.error("http backend %s: %s", r.status_code, r.text[:300])
            return None
        try:
            return _extract_reply(r.text)
        except Exception:  # noqa: BLE001
            return r.text.strip() or None


def _extract_reply(out: str) -> str | None:
    """Accept {"reply": ...}, Claude Code's {"result": ...}, or plain text."""
    if not out:
        return None
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return out
    if isinstance(obj, dict):
        for k in ("reply", "result", "text", "content"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def make_backend(kind: str, **opts) -> Backend:
    if kind == "claude-api":
        return ClaudeAPIBackend(
            api_key=opts.get("api_key"),
            model=opts.get("model") or "claude-sonnet-5",
        )
    if kind == "command":
        if not opts.get("command"):
            raise ValueError("--command is required for the command backend")
        return CommandBackend(opts["command"])
    if kind == "http":
        if not opts.get("url"):
            raise ValueError("--url is required for the http backend")
        return HTTPBackend(opts["url"])
    raise ValueError(f"unknown backend: {kind}")

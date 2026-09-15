"""Reply backends: given the conversation so far and a new entry, produce a reply.

All backends see the same `Turn` list (oldest first) and return plain text. Anything that can
turn text into text can be a diary partner:

* ClaudeAPIBackend    -- Anthropic Messages API (requests only; no SDK needed).
* CommandBackend      -- any CLI: JSON on stdin, JSON {"reply": ...} (or plain text) on stdout.
                         Works with `claude -p`, and with other agents that read stdin.
* HTTPBackend         -- POST JSON to a URL, read {"reply": ...}.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

import requests

log = logging.getLogger(__name__)


@dataclass
class Turn:
    role: str  # "user" (a human member) or "assistant" (this agent)
    name: str
    text: str
    at: str = ""  # ISO timestamp if known


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
            "turns": [t.__dict__ for t in self.turns],
            "extra": self.extra,
        }


class Backend(Protocol):
    name: str

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
        messages = []
        for t in ctx.turns:
            role = "assistant" if t.role == "assistant" else "user"
            content = t.text if role == "assistant" else f"[{t.name}] {t.text}"
            # The API requires alternating roles; merge consecutive same-role turns.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + content
            else:
                messages.append({"role": role, "content": content})
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
        for t in ctx.turns:
            who = "You" if t.role == "assistant" else t.name
            lines.append(f"{who}: {t.text}")
        lines += ["", "Write only your next diary reply."]
        return "\n".join(lines)


class HTTPBackend:
    name = "http"

    def __init__(self, url: str, headers: dict | None = None, timeout: float = 120.0):
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def reply(self, ctx: Context) -> str | None:
        try:
            r = requests.post(self.url, json=ctx.as_json(), headers=self.headers, timeout=self.timeout)
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

"""Agents with a job, defined per space and able to speak first.

A general-purpose assistant reachable through a diary is the assistant the person already has
on their phone. What a diary can offer that a chat app cannot is an agent that (a) has been
given a role by whoever issued it, (b) has the space's own record to draw on, and (c) can open
its mouth at an agreed time instead of only answering.

That is the difference between "a model you can talk to" and "the clinic's nutrition agent,
which the couple let into their food diary, which asks on Sunday evening how the week went".

A `Role` is the issuer's half of that: a persona, when to check in, and what it is allowed to
look at. It is per space because the same hosted agent serves many spaces for many issuers,
and each of them is a separate agreement with a separate set of people.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# A check-in that fires more often than this is not a diary companion, it is a notification
# generator. Whoever issues a role can pick any cadence above it.
MIN_CHECKIN_SECONDS = 3600


@dataclass
class Role:
    """What one agent is, in one space."""

    space_id: str
    # Who the person sees as responsible. Shown in the member list; not authorisation.
    operator: str = ""
    # The instruction the issuer gives the model. This is the substance of the product: a
    # nutrition role and a coaching role differ here and almost nowhere else.
    persona: str = ""
    # What to say when it speaks first, unprompted. Empty means it never does -- which is the
    # right default: an agent that starts talking before being asked is an intrusion.
    checkin_prompt: str = ""
    # How often to consider speaking first. None means never.
    checkin_seconds: int | None = None
    # Epoch seconds of the last check-in, so a restart does not fire a fresh round immediately.
    last_checkin: float = 0.0

    def __post_init__(self) -> None:
        self.space_id = str(self.space_id).lower()
        if self.checkin_seconds is not None:
            self.checkin_seconds = max(int(self.checkin_seconds), MIN_CHECKIN_SECONDS)

    @property
    def speaks_first(self) -> bool:
        return bool(self.checkin_prompt and self.checkin_seconds)

    def due(self, now: float | None = None) -> bool:
        """Whether a check-in is owed.

        A role that has never checked in is not due immediately. Being let into a diary and
        immediately being spoken at reads as an alarm, not a companion; the first check-in
        lands one full interval later, which is also when there is something to remark on.
        """
        if not self.speaks_first:
            return False
        now = time.time() if now is None else now
        if self.last_checkin <= 0:
            self.last_checkin = now
            return False
        return now - self.last_checkin >= float(self.checkin_seconds or 0)


class RoleStore:
    """Roles on disk, keyed by space.

    A file rather than a database: a hosted agent serves tens of spaces, not millions, and the
    operator of that agent should be able to read and edit what their agent has been told to be
    without a query tool.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._roles: dict[str, Role] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except Exception:  # noqa: BLE001 - a corrupt file must not stop the agent replying
            log.exception("could not read roles from %s; continuing with none", self.path)
            return
        with self._lock:
            self._roles = {
                str(k).lower(): Role(**v) for k, v in (raw.get("roles") or {}).items()
            }
        log.info("loaded %d role(s)", len(self._roles))

    def save(self) -> None:
        with self._lock:
            payload = {"roles": {k: asdict(v) for k, v in self._roles.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        # Replace atomically: a half-written roles file read at startup would silently give
        # every space the wrong persona, which is worse than having none.
        tmp.replace(self.path)

    def get(self, space_id: str) -> Role | None:
        with self._lock:
            return self._roles.get(str(space_id).lower())

    def set(self, role: Role) -> None:
        with self._lock:
            self._roles[role.space_id] = role
        self.save()

    def remove(self, space_id: str) -> bool:
        with self._lock:
            existed = self._roles.pop(str(space_id).lower(), None) is not None
        if existed:
            self.save()
        return existed

    def all(self) -> list[Role]:
        with self._lock:
            return list(self._roles.values())

    def due(self, now: float | None = None) -> list[Role]:
        """Roles owed a check-in. Marks them done, so a slow round does not fire twice."""
        now = time.time() if now is None else now
        ready = []
        with self._lock:
            for role in self._roles.values():
                if role.due(now):
                    role.last_checkin = now
                    ready.append(role)
        if ready:
            self.save()
        return ready


class RoleAwareBackend:
    """Wraps a backend so each space gets its issuer's persona.

    `Bridge` carries one persona for every space it serves. That is right for a personal agent
    and wrong for a hosted one, where two spaces may belong to different businesses with
    different agreements. The persona has to be chosen per entry, not per process.
    """

    def __init__(self, inner, roles: RoleStore, default_persona: str = ""):  # noqa: ANN001
        self.inner = inner
        self.roles = roles
        self.default_persona = default_persona

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "role-aware")

    def reply(self, ctx) -> str | None:  # noqa: ANN001
        role = self.roles.get(ctx.chat_space_id)
        if role and role.persona:
            ctx.persona = role.persona
        elif self.default_persona:
            ctx.persona = self.default_persona
        return self.inner.reply(ctx)


def run_checkins(
    roles: RoleStore,
    client,  # noqa: ANN001
    backend,  # noqa: ANN001
    stop: threading.Event,
    poll_seconds: int = 60,
) -> None:
    """Speak first, on the schedule each role was given.

    Runs on its own thread and posts through the ordinary client, so a check-in is an entry in
    the diary like any other: visible to everyone in the space, and answerable.
    """
    from .backends import Context, Turn

    while not stop.is_set():
        try:
            for role in roles.due():
                ctx = Context(
                    space_name="",
                    thread_id="",
                    turns=[Turn(role="user", name="", text=role.checkin_prompt)],
                    persona=role.persona,
                    chat_space_id=role.space_id,
                )
                text = backend.reply(ctx)
                if not text:
                    continue
                # A new thread, not a reply into an old one. A check-in starts a subject;
                # appending it to whatever was last discussed would misfile it.
                client.send(role.space_id, text)
                log.info("checked in on %s", role.space_id)
        except Exception:  # noqa: BLE001 - a failed check-in must not end the loop
            log.exception("check-in round failed")
        stop.wait(poll_seconds)

"""Push bridge: listen for entries, ask the backend, post the reply into the same thread."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from .backends import Backend, Context, Turn
from .client import IncomingMessage, PyuntoClient
from .crypto import ENCRYPTED_PLACEHOLDER
from .markers import is_marker, is_reaction, marker_preview

log = logging.getLogger(__name__)

#: Characters a robot account's display name may start with to mark it as a robot.
#: Kept in sync with ROBOT_NAME_PREFIX in pyunto_robotics/connect.py (and the server's
#: agentController). Only used to recognise a hand-typed mention that omits the marker.
ROBOT_NAME_MARKERS = "🤖 "



class Bridge:
    def __init__(
        self,
        client: PyuntoClient,
        backend: Backend,
        persona: str,
        space_ids: set[str] | None = None,
        history: int = 12,
        dry_run: bool = False,
        max_replies_per_hour: int = 60,
        silent: bool = False,
        on_idle: Callable[[], None] | None = None,
    ):
        self.client = client
        self.backend = backend
        self.persona = persona
        self.space_ids = {s.lower() for s in space_ids} if space_ids else None
        self.history = history
        self.dry_run = dry_run
        self.max_per_hour = max_replies_per_hour
        self.silent = silent
        # Called while waiting for the next message. A backend that owns a window (the robot
        # SDK's simulator) redraws here: the reply loop runs on this thread, so nothing else
        # can. When set, the queue wait shortens to a frame so the window stays responsive.
        self.on_idle = on_idle
        self._work: queue.Queue[IncomingMessage] = queue.Queue()
        self._stop = threading.Event()
        self._sent: deque[float] = deque()
        self._space_names: dict[str, str] = {}
        self._seen: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=200))
        # space id -> number of members, cached because it is needed on every entry.
        # Refreshed by refresh_spaces() and whenever a space is first seen.
        self._space_member_counts: dict[str, int] = {}

    # -- setup ----------------------------------------------------------------------

    def refresh_spaces(self) -> None:
        for s in self.client.list_spaces():
            sid = str(s.get("uuid", "")).lower()
            self._space_names[sid] = s.get("name") or "diary"
            # The list endpoint usually embeds the members; fall back to the users
            # endpoint only for spaces where it does not (see _member_count).
            users = s.get("users")
            if isinstance(users, list) and users:
                self._space_member_counts[sid] = len(users)

    def _member_count(self, chat_space_id: str) -> int:
        """How many members this space has, **counting the agent itself**.

        Counting the agent is deliberate, not an oversight: one person + the agent is a
        two-person diary (it answers freely), and two people + the agent is already a
        group of three (it must be addressed). Do not change this to count only humans --
        that would let the agent answer everything in a two-person shared diary, which is
        the behaviour this gate exists to stop.

        An occasional stale count changes nothing but how eagerly the agent speaks.
        """
        sid = chat_space_id.lower()
        cached = self._space_member_counts.get(sid)
        if cached is not None:
            return cached
        try:
            count = len(self.client.list_space_users(sid))
        except Exception:  # noqa: BLE001 - never let this stop a reply path
            log.exception("could not load members of %s; assuming a small space", sid)
            count = 2
        self._space_member_counts[sid] = count
        return count

    def _should_reply(self, m: IncomingMessage) -> bool:
        """Whether this entry is addressed to the agent.

        In a one-on-one diary -- one person and the agent, i.e. two members in total --
        everything written is addressed to the agent by definition, so it answers freely.

        From three members up, mentions decide the audience. Two people plus the agent
        already counts as a group: people are mostly writing to each other, and an agent
        that answers everything talks over them. There it speaks only when:

          1. it was mentioned by name (``@Claude``), or
          2. the entry was sent to everyone -- the "notify everyone" mode, i.e. the
             sender addressed nobody in particular and did not choose to leave it quietly.

        ``mentioned_uuids`` cannot carry this decision on its own: in a shared space it
        holds *every* member because that field decides who can SEE the thread.
        ``notify_uuids`` (server field ``notify_users``) is the one that says who the
        entry was actually for.
        """
        if self._member_count(m.chat_space_id) <= 2:
            return True

        me = self.client.uuid

        # 1) Mentioned by name. Checked against the text as well as notify_users, because
        #    older clients do not send notify_users at all -- there the text is the only
        #    evidence that the agent was the one being addressed.
        if self._is_mentioned_by_name(m.text):
            return True
        if m.notify_uuids is not None:
            # The sender addressed specific people. The agent speaks only if it is one.
            return me in m.notify_uuids

        # 2) Sent to everyone. "Leave it quietly" was meant to reach nobody, so the
        #    agent stays out of it; anything else in a group goes to all members.
        return not m.silent

    def _is_mentioned_by_name(self, text: str) -> bool:
        """Does the text address this member by name?

        Both forms of the name count. A robot registers as ``🤖 Momo`` (connect.py adds
        the marker), and the app's mention picker inserts that whole string -- but a
        person typing by hand writes ``@Momo``, which no parser will resolve to the
        account. Accepting the marker-less form means a hand-typed mention still reaches
        the robot; hosted Claude has no marker, so for it both forms are the same string.
        """
        name = (self.client.display_name or "").strip()
        if not name:
            return False
        haystack = text.lower()
        candidates = {name.lower()}
        # Strip a leading robot marker (and the space after it) to get the plain name.
        bare = name.lstrip(ROBOT_NAME_MARKERS).strip()
        if bare:
            candidates.add(bare.lower())
        return any(f"@{c}" in haystack for c in candidates)

    # -- Socket.IO callback -------------------------------------------------------------

    def _on_message(self, m: IncomingMessage) -> None:
        if m.sender_uuid.lower() == self.client.uuid:
            return
        if self.space_ids and m.chat_space_id.lower() not in self.space_ids:
            return
        if m.uuid in self._seen[m.thread_id]:
            return
        self._seen[m.thread_id].append(m.uuid)
        # Not decryptable yet (no key shared for this space) or a reaction: nothing to answer.
        if not m.text or m.text == ENCRYPTED_PLACEHOLDER or is_reaction(m.text):
            log.info("skipping entry from %s (undecryptable or reaction)", m.sender_name)
            return
        if not self._should_reply(m):
            log.info("skipping entry from %s (not addressed to me in this group)", m.sender_name)
            return
        self._work.put(m)

    # -- main loop ----------------------------------------------------------------------

    def _start_listener(self) -> threading.Thread:
        t = threading.Thread(
            target=self.client.listen, args=(self._on_message,), daemon=True, name="pyunto-listener"
        )
        t.start()
        return t

    def run(self) -> None:
        self.refresh_spaces()
        self._listener = self._start_listener()
        log.info("bridge online as %s (backend=%s, dry_run=%s)", self.client.uuid, self.backend.name, self.dry_run)
        try:
            while not self._stop.is_set():
                try:
                    m = self._work.get(timeout=1 / 60 if self.on_idle else 0.5)
                except queue.Empty:
                    if self.on_idle is not None:
                        self.on_idle()
                    continue
                try:
                    self.handle(m)
                except Exception:  # noqa: BLE001 - keep listening whatever happens
                    log.exception("failed to handle a message")
                finally:
                    self._work.task_done()
        finally:
            self._stop.set()
            self.client.stop()
            self._listener.join(timeout=2.0)

    def reconnect(self) -> None:
        """Re-subscribe after joining/leaving a space (the server assigns Socket.IO rooms at
        connect time from current membership, so a new space needs a fresh connection)."""
        self.refresh_spaces()
        self.client.stop()
        old = getattr(self, "_listener", None)
        if old is not None:
            old.join(timeout=3.0)
        self._listener = self._start_listener()
        log.info("listener reconnected (%d spaces)", len(self._space_names))

    def stop(self) -> None:
        self._stop.set()

    # -- one message ----------------------------------------------------------------

    def handle(self, m: IncomingMessage) -> str | None:
        if not self._rate_ok():
            log.warning("rate limit reached; skipping reply")
            return None
        log.info("<- [%s] %s", m.sender_name, m.text[:120])
        ctx = self.build_context(m)
        reply = self.backend.reply(ctx)
        if not reply:
            log.info("backend produced no reply")
            return None
        reply = reply.strip()
        if self.dry_run:
            log.info("(dry-run) -> %s", reply)
            return reply
        # Notify the person who wrote to us, by name.
        #
        # Without this the server falls back to "everyone in the thread", and each of their
        # notification settings then decides -- a phone set to mentions-only shows nothing.
        # The agent answers correctly and the person never learns it replied, which is exactly
        # what happened: the reply was in the diary, and no notification arrived.
        self.client.send(
            m.chat_space_id, reply, thread_id=m.thread_id,
            notify_users=[m.sender_uuid] if m.sender_uuid else None,
            silent=self.silent,
        )
        self._sent.append(time.time())
        log.info("-> %s", reply[:120])
        return reply

    def build_context(self, m: IncomingMessage) -> Context:
        me = self.client.uuid
        turns: list[Turn] = []
        try:
            history = self.client.get_messages(m.thread_id, m.chat_space_id)
        except Exception:  # noqa: BLE001
            log.exception("could not load thread history; replying to the single entry")
            history = [m]
        for h in history[-self.history :]:
            text = h.text or ""
            if is_marker(text):
                text = marker_preview(text) or text
            if not text.strip():
                continue
            turns.append(
                Turn(
                    role="assistant" if h.sender_uuid.lower() == me else "user",
                    name=h.sender_name or "them",
                    text=text,
                    at=str((h.raw or {}).get("created_at") or ""),
                )
            )
        # Make sure the triggering entry is the last user turn even if history lagged.
        if not turns or turns[-1].text != (marker_preview(m.text) or m.text):
            turns.append(Turn(role="user", name=m.sender_name or "them", text=marker_preview(m.text) or m.text))
        space_name = self._space_names.get(m.chat_space_id.lower())
        if space_name is None:
            self.refresh_spaces()
            space_name = self._space_names.get(m.chat_space_id.lower(), "diary")
        return Context(space_name=space_name, thread_id=m.thread_id, turns=turns,
                       persona=self.persona, chat_space_id=m.chat_space_id,
                       sender_uuid=m.sender_uuid)

    def _rate_ok(self) -> bool:
        now = time.time()
        while self._sent and now - self._sent[0] > 3600:
            self._sent.popleft()
        return len(self._sent) < self.max_per_hour

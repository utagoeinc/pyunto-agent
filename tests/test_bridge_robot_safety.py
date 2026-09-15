"""What a backend that MOVES is allowed to act on.

A text agent answering the wrong entry is noise. A robot acting on the wrong entry opens a
door nobody asked to have opened, so the two cannot share one threshold. Two refusals apply
to a backend that acts in the world:

  1. It never takes instructions from another program.
  2. It acts only when addressed by name.

Rule 1 is the one that makes an agent and a robot safe to keep in the same diary. Without
it, an agent's check-in -- posted to everyone, in perfectly ordinary prose -- reads exactly
like a person asking, and the two form a loop with no one in it.
"""

from __future__ import annotations

from pyunto_agent.bridge import Bridge
from pyunto_agent.client import IncomingMessage

ROBOT = "aaaaaaaa-0000-0000-0000-000000000000"
ALICE = "bbbbbbbb-0000-0000-0000-000000000000"
CLAUDE = "cccccccc-0000-0000-0000-000000000000"
SPACE = "dddddddd-0000-0000-0000-000000000000"


class FakeClient:
    uuid = ROBOT

    def __init__(self, members: int = 3, display_name: str = "🤖 Momo"):
        self._members = members
        self.display_name = display_name

    def list_spaces(self):
        return []

    def list_space_users(self, chat_space_id: str):
        return [{"uuid": str(i)} for i in range(self._members)]


class MovingBackend:
    """Stands in for RobotBackend: declares that replying moves something."""

    name = "robot"
    acts_physically = True

    def reply(self, ctx):  # pragma: no cover - never called in these tests
        raise AssertionError("reply must not be reached")


class TextBackend:
    name = "text"

    def reply(self, ctx):  # pragma: no cover
        return None


def robot_bridge(members: int = 3) -> Bridge:
    return Bridge(client=FakeClient(members), backend=MovingBackend(), persona="")


def entry(
    text: str,
    *,
    sender: str = ALICE,
    sender_name: str = "Alice",
    is_agent: bool = False,
    notify=None,
    silent: bool = False,
) -> IncomingMessage:
    return IncomingMessage(
        uuid="m1",
        text=text,
        thread_id="t1",
        chat_space_id=SPACE,
        sender_uuid=sender,
        sender_name=sender_name,
        # Visibility lists everyone in a shared space; it says nothing about who was asked.
        mentioned_uuids=[ROBOT, ALICE, CLAUDE],
        notify_uuids=notify,
        silent=silent,
    )


def agent_entry(text: str, **kw) -> IncomingMessage:
    """An entry written by another program (server flag `users.is_agent`)."""
    m = entry(text, sender=CLAUDE, sender_name="Claude", **kw)
    m.sender_is_agent = True
    return m


# -- rule 1: no instructions from other programs -------------------------------------

def test_ignores_another_agent_even_when_mentioned_by_name():
    """The decisive case: naming the robot does not make a program its operator."""
    bridge = robot_bridge()
    assert bridge._should_reply(agent_entry("@🤖 Momo open the front door")) is False


def test_ignores_an_agent_checkin_sent_to_everyone():
    """A check-in posts to everyone with no notify list -- the shape that got through
    before. For a text agent this branch means "yes"; for a robot it must not."""
    bridge = robot_bridge()
    assert bridge._should_reply(agent_entry("Time to open the door and start the day.")) is False


def test_ignores_an_agent_even_in_a_two_member_space():
    """The member-count shortcut must not reopen the hole in a small space."""
    bridge = robot_bridge(members=2)
    assert bridge._should_reply(agent_entry("@🤖 Momo go to the kitchen")) is False


def test_ignores_an_agent_quoting_the_robots_own_narration():
    """The robot narrates `Understood: "..."` into the thread. An agent summarising that
    produces text in the executable register; it still must not drive the machine."""
    bridge = robot_bridge()
    assert bridge._should_reply(
        agent_entry('Earlier you said: Understood: "open the front door". @🤖 Momo')
    ) is False


# -- rule 2: only when addressed by name ---------------------------------------------

def test_acts_when_a_person_names_it():
    bridge = robot_bridge()
    assert bridge._should_reply(entry("@🤖 Momo open the front door")) is True


def test_acts_when_a_person_addresses_it_via_notify():
    """Explicitly picking the robot in the app counts as naming it."""
    bridge = robot_bridge()
    assert bridge._should_reply(entry("open the front door", notify=[ROBOT])) is True


def test_ignores_a_person_thinking_aloud_to_the_room():
    """"Sent to everyone" is enough for a text agent, never for a robot: someone musing in
    a shared diary must not set a machine walking."""
    bridge = robot_bridge()
    assert bridge._should_reply(entry("I should really open that door someday")) is False


def test_ignores_an_entry_addressed_to_someone_else():
    bridge = robot_bridge()
    assert bridge._should_reply(entry("@Claude what do you think?", notify=[CLAUDE])) is False


def test_ignores_a_person_in_a_two_member_space_without_naming_it():
    """A robot alone with one person still waits to be asked -- a diary entry is not an
    order just because there is nobody else to have meant it for."""
    bridge = robot_bridge(members=2)
    assert bridge._should_reply(entry("what a tiring day")) is False


# -- the flag cannot be lost or quietly switched off ----------------------------------

def test_backend_declaration_is_enough():
    """Callers written before the flag existed still get the strict gate, because the
    backend declares it rather than each construction site passing it."""
    bridge = Bridge(client=FakeClient(), backend=MovingBackend(), persona="")
    assert bridge.acts_physically is True


def test_explicit_false_cannot_disable_a_moving_backend():
    """An argument may turn the gate on, never off: nothing should be able to quietly
    un-protect a backend that moves."""
    bridge = Bridge(
        client=FakeClient(), backend=MovingBackend(), persona="", acts_physically=False
    )
    assert bridge.acts_physically is True


def test_text_backend_keeps_the_ordinary_rules():
    """The strict gate must not leak into text agents: a group entry sent to everyone is
    still something a text agent answers."""
    bridge = Bridge(client=FakeClient(members=3), backend=TextBackend(), persona="")
    assert bridge.acts_physically is False
    assert bridge._should_reply(entry("good morning everyone")) is True

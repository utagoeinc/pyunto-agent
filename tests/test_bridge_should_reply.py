"""When the agent is allowed to speak.

The rule under test: in a two-person diary the agent answers everything; in a group of
three or more it answers only when it was mentioned by name, or when the entry was sent
to everyone. An entry addressed to some other member must not wake it.
"""

from __future__ import annotations

from pyunto_agent.bridge import Bridge
from pyunto_agent.client import IncomingMessage

AGENT = "aaaaaaaa-0000-0000-0000-000000000000"
ALICE = "bbbbbbbb-0000-0000-0000-000000000000"
BOB = "cccccccc-0000-0000-0000-000000000000"
SPACE = "dddddddd-0000-0000-0000-000000000000"


class FakeClient:
    uuid = AGENT

    def __init__(self, members: int, display_name: str = "Claude"):
        self._members = members
        self.display_name = display_name

    def list_spaces(self):
        return []

    def list_space_users(self, chat_space_id: str):
        return [{"uuid": str(i)} for i in range(self._members)]


def make_bridge(members: int, display_name: str = "Claude") -> Bridge:
    return Bridge(client=FakeClient(members, display_name), backend=None, persona="")


def entry(text: str, *, notify=None, silent: bool = False) -> IncomingMessage:
    return IncomingMessage(
        uuid="m1",
        text=text,
        thread_id="t1",
        chat_space_id=SPACE,
        sender_uuid=ALICE,
        sender_name="Alice",
        # Visibility always lists everyone in a shared space -- that is the point of
        # having a separate notify list.
        mentioned_uuids=[AGENT, ALICE, BOB],
        notify_uuids=notify,
        silent=silent,
    )


# -- two-person diary ---------------------------------------------------------------

def test_two_person_diary_answers_everything():
    b = make_bridge(members=2)
    assert b._should_reply(entry("a tiring day")) is True


def test_two_person_diary_answers_even_when_quiet():
    b = make_bridge(members=2)
    assert b._should_reply(entry("a quiet note", silent=True)) is True


# -- two humans + the agent is already a group --------------------------------------

def test_two_humans_plus_agent_is_a_group_not_a_one_on_one():
    """The agent counts towards the group size.

    Two people writing to each other with the agent present is a group: the agent must
    be addressed. Counting only humans here would make it answer everything again.
    """
    b = make_bridge(members=3)  # Alice + Bob + Claude
    assert b._should_reply(entry("@Bob well done", notify=[BOB])) is False
    assert b._should_reply(entry("@Claude well done", notify=[AGENT])) is True


# -- group of three or more ---------------------------------------------------------

def test_group_ignores_entry_addressed_to_someone_else():
    b = make_bridge(members=3)
    assert b._should_reply(entry("@Bob take a look", notify=[BOB])) is False


def test_group_answers_when_mentioned_by_name():
    b = make_bridge(members=3)
    assert b._should_reply(entry("@Claude what do you think?", notify=[AGENT])) is True


def test_group_answers_when_mentioned_alongside_a_person():
    b = make_bridge(members=3)
    assert b._should_reply(entry("@Bob @Claude take a look", notify=[BOB, AGENT])) is True


def test_group_answers_when_sent_to_everyone():
    b = make_bridge(members=3)
    assert b._should_reply(entry("well done everyone")) is True


def test_group_stays_out_of_quiet_entries():
    b = make_bridge(members=3)
    assert b._should_reply(entry("thinking aloud", silent=True)) is False


def test_group_answers_mention_from_a_client_without_notify_users():
    # Older clients send no notify_users at all; the text is then the only evidence.
    b = make_bridge(members=3)
    assert b._should_reply(entry("@Claude tell me", notify=None)) is True


def test_member_count_is_cached():
    client = FakeClient(3)
    b = Bridge(client=client, backend=None, persona="")
    calls = []
    original = client.list_space_users
    client.list_space_users = lambda sid: (calls.append(sid), original(sid))[1]
    b._should_reply(entry("one"))
    b._should_reply(entry("two"))
    assert len(calls) == 1


# -- robots follow exactly the same rules as hosted Claude ---------------------------
#
# A robot joins through pyunto_robotics and runs the same Bridge, so these are the same
# code paths; what differs is only its display name, which carries a 🤖 marker.

ROBOT_NAME = "🤖 Momo"


def test_robot_in_two_person_diary_answers_everything():
    b = make_bridge(members=2, display_name=ROBOT_NAME)
    assert b._should_reply(entry("I am home")) is True


def test_robot_in_group_ignores_entry_addressed_to_someone_else():
    b = make_bridge(members=3, display_name=ROBOT_NAME)
    assert b._should_reply(entry("@Bob take a look", notify=[BOB])) is False


def test_robot_in_group_answers_when_addressed():
    b = make_bridge(members=3, display_name=ROBOT_NAME)
    assert b._should_reply(entry("@🤖 Momo come here", notify=[AGENT])) is True


def test_robot_answers_a_hand_typed_mention_without_the_marker():
    """The picker inserts "@🤖 Momo", but a person types "@Momo".

    No parser resolves the marker-less form to the account, so notify_users will not
    contain the robot; the text is the only evidence it was being addressed.
    """
    b = make_bridge(members=3, display_name=ROBOT_NAME)
    assert b._should_reply(entry("@Momo come over here", notify=None)) is True


def test_robot_in_group_stays_out_of_quiet_entries():
    b = make_bridge(members=3, display_name=ROBOT_NAME)
    assert b._should_reply(entry("thinking aloud", silent=True)) is False


def test_robot_in_group_answers_when_sent_to_everyone():
    b = make_bridge(members=3, display_name=ROBOT_NAME)
    assert b._should_reply(entry("well done all")) is True

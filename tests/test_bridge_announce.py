"""At start-up the agent says which diaries it is in, by name."""
from __future__ import annotations

from pyunto_agent.bridge import Bridge


class Keys:
    def __init__(self, readable: set[str]):
        self.readable = readable

    def get_key(self, sid):
        if sid in self.readable:
            return b"k" * 32
        raise KeyError("no key yet")


class FakeClient:
    uuid = "agent"
    display_name = "Claude"

    def __init__(self, spaces, readable):
        self._spaces = spaces
        self.keys = Keys(readable)

    def list_spaces(self):
        return self._spaces

    def list_space_users(self, sid):
        return [{"uuid": "x"}] * 2


def test_names_membership_mode_and_key_state_are_listed():
    spaces = [
        {"uuid": "aaaaaaaa-1", "name": "Training log", "users": [{}, {}]},
        {"uuid": "bbbbbbbb-2", "name": "Family", "users": [{}, {}, {}]},
    ]
    b = Bridge(client=FakeClient(spaces, readable={"aaaaaaaa-1"}), backend=None, persona="")
    b.refresh_spaces()
    lines = b.describe_spaces()
    assert lines[0].startswith("  Family  [3 members, answers only when mentioned]  bbbbbbbb")
    assert "open this space in the Pyunto app once" in lines[0]
    assert lines[1] == "  Training log  [2 members, answers every entry]  aaaaaaaa"


def test_only_the_pinned_space_is_listed():
    spaces = [{"uuid": "a1", "name": "A", "users": [{}, {}]}, {"uuid": "b2", "name": "B", "users": [{}, {}]}]
    b = Bridge(client=FakeClient(spaces, readable={"a1", "b2"}), backend=None, persona="", space_ids={"b2"})
    b.refresh_spaces()
    assert [l.split()[0] for l in b.describe_spaces()] == ["B"]

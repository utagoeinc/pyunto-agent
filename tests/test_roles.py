"""Roles: what makes a hosted agent worth paying for rather than a chat window.

Two things separate "the clinic's nutrition agent in our food diary" from "a model you can
message": it was given a job by whoever issued it, and it can speak first. Both are per space,
because one hosted process serves several issuers under several separate agreements.
"""

from __future__ import annotations

import time

import pytest

from pyunto_agent.roles import MIN_CHECKIN_SECONDS, Role, RoleAwareBackend, RoleStore


def test_a_role_belongs_to_one_space():
    """Space ids arrive in both cases from different clients; the store must not care."""
    assert Role(space_id="ABC-123").space_id == "abc-123"


def test_check_in_cadence_has_a_floor():
    """Faster than hourly is a notification generator, not a diary companion."""
    role = Role(space_id="s", checkin_prompt="how was the week", checkin_seconds=60)
    assert role.checkin_seconds == MIN_CHECKIN_SECONDS


def test_an_agent_without_a_prompt_never_speaks_first():
    """The default has to be silence. Speaking before being asked is an intrusion."""
    assert Role(space_id="s", checkin_seconds=7200).speaks_first is False
    assert Role(space_id="s", checkin_prompt="hi").speaks_first is False


def test_the_first_check_in_waits_a_full_interval():
    """Being let in and immediately spoken at reads as an alarm."""
    role = Role(space_id="s", checkin_prompt="hi", checkin_seconds=7200)
    assert role.due() is False
    assert role.last_checkin > 0


def test_a_check_in_comes_due_after_its_interval():
    role = Role(space_id="s", checkin_prompt="hi", checkin_seconds=7200)
    role.due()  # arm it
    role.last_checkin = time.time() - 7201
    assert role.due() is True


def test_roles_survive_a_restart(tmp_path):
    """The issuer configures this once; a restart must not silently drop it."""
    path = tmp_path / "roles.json"
    RoleStore(path).set(Role(space_id="s1", persona="nutritionist", operator="A Clinic"))
    reloaded = RoleStore(path).get("s1")
    assert reloaded is not None
    assert reloaded.persona == "nutritionist"
    assert reloaded.operator == "A Clinic"


def test_a_corrupt_store_does_not_stop_the_agent(tmp_path):
    """Replying matters more than personalising the reply."""
    path = tmp_path / "roles.json"
    path.write_text("{ this is not json")
    assert RoleStore(path).all() == []


def test_due_marks_roles_so_a_slow_round_does_not_repeat(tmp_path):
    store = RoleStore(tmp_path / "roles.json")
    role = Role(space_id="s", checkin_prompt="hi", checkin_seconds=7200)
    role.last_checkin = time.time() - 7201
    store.set(role)
    assert [r.space_id for r in store.due()] == ["s"]
    assert store.due() == [], "a second pass must not fire the same check-in again"


def test_removing_a_role_is_reported_honestly(tmp_path):
    store = RoleStore(tmp_path / "roles.json")
    store.set(Role(space_id="s"))
    assert store.remove("s") is True
    assert store.remove("s") is False


class Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def reply(self, ctx):  # noqa: ANN001
        self.seen.append(ctx.persona)
        return "ok"


def context(space_id: str):  # noqa: ANN001
    from pyunto_agent.backends import Context, Turn

    return Context(space_name="d", thread_id="t", turns=[Turn("user", "x", "hi")],
                   persona="", chat_space_id=space_id)


def test_each_space_gets_its_own_issuers_persona(tmp_path):
    """The point of the whole module: one process, several issuers, separate agreements."""
    store = RoleStore(tmp_path / "roles.json")
    store.set(Role(space_id="clinic", persona="You are a nutritionist."))
    store.set(Role(space_id="coach", persona="You are a running coach."))
    inner = Recorder()
    backend = RoleAwareBackend(inner, store)

    backend.reply(context("clinic"))
    backend.reply(context("coach"))
    assert inner.seen == ["You are a nutritionist.", "You are a running coach."]


def test_a_space_with_no_role_falls_back(tmp_path):
    inner = Recorder()
    backend = RoleAwareBackend(inner, RoleStore(tmp_path / "roles.json"),
                               default_persona="a diary companion")
    backend.reply(context("unknown"))
    assert inner.seen == ["a diary companion"]


@pytest.mark.parametrize("seconds", [None, 0])
def test_no_schedule_means_never_due(seconds):
    role = Role(space_id="s", checkin_prompt="hi", checkin_seconds=seconds)
    assert role.due() is False

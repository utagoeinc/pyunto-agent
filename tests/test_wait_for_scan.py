"""Being already paired is success, not a reason to wait.

`wait_for_scan` only reported a space that was NEW since the wait began. A robot re-paired
into the same space -- which is what happens every time somebody runs `showqr` twice -- joins
nothing new, so the terminal sat at "waiting for the scan…" after the square had been scanned
and the app had said yes.
"""

from __future__ import annotations

import time

from pyunto_agent.pairing import wait_for_scan


class Client:
    """A list_spaces that can change between polls."""

    def __init__(self, *rounds):
        self._rounds = list(rounds)

    def list_spaces(self):
        current = self._rounds[0]
        if len(self._rounds) > 1:
            self._rounds.pop(0)
        return current


def space(uuid: str, is_self: bool = False):
    return {"uuid": uuid, "is_self": is_self, "name": uuid}


def test_a_space_it_is_already_in_returns_at_once():
    """The reported fault: scanned, approved, and the terminal never moved."""
    started = time.monotonic()
    found = wait_for_scan(Client([space("already-here")]), timeout=5.0, poll=0.05)
    assert found == "already-here"
    assert time.monotonic() - started < 1.0, "an already-paired robot must not wait"


def test_a_space_that_appears_later_is_picked_up():
    """The original case still works: nothing at first, then somebody scans."""
    client = Client([], [], [space("arrived")])
    assert wait_for_scan(client, timeout=5.0, poll=0.01) == "arrived"


def test_a_self_space_alone_is_not_pairing():
    """Every account has its own diary; that is not somebody letting the robot in."""
    client = Client([space("mine", is_self=True)])
    assert wait_for_scan(client, timeout=0.2, poll=0.05) is None


def test_nobody_scanning_times_out_rather_than_hanging():
    started = time.monotonic()
    assert wait_for_scan(Client([]), timeout=0.3, poll=0.05) is None
    assert time.monotonic() - started < 2.0


def test_a_server_hiccup_does_not_end_the_wait():
    """A failed poll is "not yet", not "never"."""

    class Flaky:
        def __init__(self):
            self.calls = 0

        def list_spaces(self):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("boom")
            return [space("eventually")]

    assert wait_for_scan(Flaky(), timeout=5.0, poll=0.01) == "eventually"

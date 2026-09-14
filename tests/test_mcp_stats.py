"""Quick-list statistics: the thing an agent can say that a chat model cannot.

"You have taken the morning dose three times this week" requires a continuous record and the
key to read it. The count is assembled on this machine from decrypted entries -- the server
stores these posts as ciphertext, so no endpoint could answer it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pyunto_agent.markers import item_marker, parse_item_marker
from pyunto_agent.mcp_server import MCPServer

NOW = time.time()


def entry(text: str, days_ago: float = 0) -> SimpleNamespace:
    stamp = datetime.fromtimestamp(NOW - days_ago * 86400, timezone.utc).isoformat()
    return SimpleNamespace(text=text, raw={"created_at": stamp})


class FakeClient:
    uuid = "me"

    def __init__(self, threads: dict[str, list]) -> None:
        self.threads = threads

    def list_threads(self, space_id, limit=50, offset=0):  # noqa: ANN001
        return [{"uuid": t} for t in self.threads]

    def get_messages(self, thread_id, space_id):  # noqa: ANN001
        return self.threads[thread_id]


def server(threads: dict[str, list]) -> MCPServer:
    s = MCPServer.__new__(MCPServer)
    s.client = FakeClient(threads)
    return s


def test_repeats_are_counted_not_collapsed():
    """Writing the same item twice means it happened twice. That is the whole feature."""
    s = server({"t1": [entry(item_marker("薬", "💊", ["朝の薬", "朝の薬", "夜の薬"]), 1)]})
    result = s._quick_list_stats("sp", 7, None)
    items = {i["item"]: i["count"] for i in result["lists"][0]["items"]}
    assert items == {"朝の薬": 2, "夜の薬": 1}


def test_counts_span_threads():
    """A diary records across days, so the tally has to cross threads."""
    s = server({
        "t1": [entry(item_marker("薬", "💊", ["朝の薬"]), 1)],
        "t2": [entry(item_marker("薬", "💊", ["朝の薬"]), 2)],
    })
    assert s._quick_list_stats("sp", 7, None)["lists"][0]["total"] == 2


def test_entries_outside_the_window_are_excluded():
    s = server({"t1": [
        entry(item_marker("薬", "💊", ["朝の薬"]), 1),
        entry(item_marker("薬", "💊", ["朝の薬"]), 40),
    ]})
    assert s._quick_list_stats("sp", 7, None)["lists"][0]["total"] == 1
    assert s._quick_list_stats("sp", 60, None)["lists"][0]["total"] == 2


def test_ordinary_entries_are_ignored():
    """Only quick-list markers count; a diary is mostly prose."""
    s = server({"t1": [entry("今日はよく歩いた", 1)]})
    assert s._quick_list_stats("sp", 7, None)["lists"] == []


def test_one_list_can_be_singled_out():
    s = server({"t1": [
        entry(item_marker("薬", "💊", ["朝の薬"]), 1),
        entry(item_marker("絵本", "📖", ["ぐりとぐら"]), 1),
    ]})
    result = s._quick_list_stats("sp", 7, "薬")
    assert [lst["list_name"] for lst in result["lists"]] == ["薬"]


def test_an_unreadable_thread_does_not_lose_the_others():
    class Broken(FakeClient):
        def get_messages(self, thread_id, space_id):  # noqa: ANN001
            if thread_id == "bad":
                raise RuntimeError("no key for this thread")
            return super().get_messages(thread_id, space_id)

    s = MCPServer.__new__(MCPServer)
    s.client = Broken({"bad": [], "t1": [entry(item_marker("薬", "💊", ["朝の薬"]), 1)]})
    assert s._quick_list_stats("sp", 7, None)["lists"][0]["total"] == 1


def test_an_undated_entry_is_counted_rather_than_dropped():
    """Leaving a recorded dose out of a tally is worse than counting a slightly old one."""
    s = server({"t1": [SimpleNamespace(text=item_marker("薬", "💊", ["朝の薬"]), raw={})]})
    assert s._quick_list_stats("sp", 7, None)["lists"][0]["total"] == 1


def test_items_are_ordered_by_how_often_they_happened():
    s = server({"t1": [entry(item_marker("薬", "💊", ["夜の薬", "朝の薬", "朝の薬"]), 1)]})
    items = s._quick_list_stats("sp", 7, None)["lists"][0]["items"]
    assert [i["item"] for i in items] == ["朝の薬", "夜の薬"]


@pytest.mark.parametrize("text", ["", "!item:", "!item:薬", "!item:薬:💊", "!item:薬:💊:"])
def test_malformed_markers_are_ignored(text):
    assert parse_item_marker(text) is None

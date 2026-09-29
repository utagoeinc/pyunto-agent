"""MCP: pairing from inside the client, opening attachments, and the handshake."""
from __future__ import annotations

import json

from pyunto_agent import mcp_server
from pyunto_agent.client import IncomingMessage
from pyunto_agent.media import Attachment

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeClient:
    uuid = "agent-uuid"
    display_name = "Claude"

    def __init__(self, tmp_path):
        self.attachment_dir = tmp_path / "attachments"
        self.photo = tmp_path / "m1.png"
        self.photo.write_bytes(PNG)

    def get_messages(self, thread_id, space_id=None):
        att = Attachment("image", "m1", space_id)
        return [IncomingMessage(uuid="m1", text="", thread_id=thread_id, chat_space_id=space_id,
                                sender_uuid="u1", sender_name="Aiko", attachment=att,
                                message_type="image")]

    def download_attachment(self, att):
        att.path = self.photo
        return self.photo


def server(tmp_path, capsys=None):
    return mcp_server.MCPServer(FakeClient(tmp_path), key_provider=None, identity_public_key="PK")


def test_pair_shows_the_qr_as_an_image_and_says_what_to_do(tmp_path):
    result = server(tmp_path)._call("pair", {"operator": "tom"})
    kinds = [b["type"] for b in result.blocks]
    assert "image" in kinds and result.blocks[0]["mimeType"] == "image/png"
    assert "scan" in result.blocks[-1]["text"].lower()


def test_read_attachment_returns_the_photo_as_an_image(tmp_path):
    result = server(tmp_path)._call("read_attachment",
                                    {"space_id": "s", "thread_id": "t", "message_id": "m1"})
    assert result.blocks[0] == {"type": "image", "mimeType": "image/png",
                                "data": result.blocks[0]["data"]}


def test_read_thread_marks_entries_that_have_an_attachment(tmp_path):
    rows = server(tmp_path)._call("read_thread", {"space_id": "s", "thread_id": "t"})
    assert rows[0]["text"] == "(photo)" and rows[0]["attachment"]["kind"] == "image"


def test_initialize_sends_instructions_and_a_title(tmp_path, capsys):
    server(tmp_path)._dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    result = json.loads(capsys.readouterr().out)["result"]
    assert "pair" in result["instructions"] and result["serverInfo"]["title"] == "Pyunto Diary"

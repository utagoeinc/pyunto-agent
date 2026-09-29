"""Photos, videos and documents: decrypted here, and shown to the model.

The blob layout is the apps' own (FILE_ATTACHMENT_SPEC): a 4-byte little-endian nonce length,
the nonce, then ciphertext || tag. A caption is a separate entry after the media, so the
agent waits briefly and answers the photo and its caption once.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pyunto_agent import media
from pyunto_agent.backends import CommandBackend, Context, Turn, attachment_blocks
from pyunto_agent.bridge import Bridge
from pyunto_agent.client import IncomingMessage, PyuntoClient

KEY = bytes(range(32))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def seal(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return len(nonce).to_bytes(4, "little") + nonce + AESGCM(KEY).encrypt(nonce, data, None)


def test_the_apps_blob_layout_decrypts():
    assert media.decrypt_blob(seal(b"hello"), KEY) == b"hello"


def test_file_info_gives_the_documents_name_and_type():
    info = {"name": "essay.docx", "mime": "application/vnd.openxmlformats", "size": 12}
    meta = {"file_info": base64.b64encode(seal(json.dumps(info).encode())).decode()}
    assert media.file_info(meta, KEY)["name"] == "essay.docx"


def test_image_type_is_read_from_the_bytes_not_assumed():
    assert media.sniff_image(PNG)[0] == "image/png"   # a robot's camera frame
    assert media.sniff_image(JPEG)[0] == "image/jpeg"  # a phone photo


class Keys:
    def get_key(self, sid):
        return KEY


class Resp:
    def __init__(self, content):
        self.content, self.status_code = content, 200

    def raise_for_status(self):
        pass


def test_download_decrypts_into_the_attachment_folder(tmp_path, monkeypatch):
    client = PyuntoClient.__new__(PyuntoClient)
    client.keys, client.attachment_dir = Keys(), tmp_path
    monkeypatch.setattr(client, "_request", lambda *a, **k: Resp(seal(PNG)), raising=False)
    att = media.Attachment(kind="image", message_uuid="m1", chat_space_id="s1")
    path = client.download_attachment(att)
    assert path == tmp_path / "m1.png" and path.read_bytes() == PNG
    assert att.mime == "image/png"


def test_a_photo_message_decodes_as_an_attachment_not_as_text():
    client = PyuntoClient.__new__(PyuntoClient)
    m = client._decode_ws_message({
        "uuid": "m1", "content": "[Image]", "chatSpaceId": "s1", "chatThreadId": "t1",
        "messageType": "image", "encryptedMetadata": {"nonce": "x"},
        "sender": {"uuid": "u1", "display_name": "Aiko", "is_agent": False},
    })
    assert m.text == "" and m.attachment.kind == "image" and m.attachment.message_uuid == "m1"


def test_claude_api_gets_an_image_block_and_a_pdf_document(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(PNG)
    blocks = attachment_blocks(media.Attachment("image", "m", "s", mime="image/png", path=img))
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")
    blocks = attachment_blocks(media.Attachment("file", "m", "s", name="b.pdf", path=pdf))
    assert blocks[0]["type"] == "document"


def test_a_text_document_is_passed_as_its_text(tmp_path):
    doc = tmp_path / "essay.md"
    doc.write_text("Their going to the park tomorow.")
    blocks = attachment_blocks(media.Attachment("file", "m", "s", name="essay.md", mime="text/markdown", path=doc))
    assert "tomorow" in blocks[0]["text"]


def test_claude_code_is_told_the_paths_and_allowed_to_read_the_folder(tmp_path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(JPEG)
    ctx = Context(space_name="d", thread_id="t", persona="p", turns=[
        Turn("user", "Aiko", "what do you think?",
             attachments=[media.Attachment("image", "m", "s", mime="image/jpeg", path=img)])])
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["stdin"] = kw.get("input")
        return type("P", (), {"returncode": 0, "stdout": '{"result": "nice"}', "stderr": ""})()

    monkeypatch.setattr("pyunto_agent.backends.subprocess.run", fake_run)
    assert CommandBackend("claude -p --output-format json").reply(ctx) == "nice"
    assert seen["argv"][-2:] == ["--add-dir", str(tmp_path)]
    assert str(img) in json.loads(seen["stdin"])["prompt"]


class FakeClient:
    uuid = "agent"
    display_name = "Claude"

    def __init__(self, history):
        self.history = history
        self.downloaded = []

    def list_spaces(self):
        return []

    def get_messages(self, thread_id, chat_space_id=None):
        return self.history

    def download_attachment(self, att):
        self.downloaded.append(att.message_uuid)


def entry(uuid, text="", kind=None, sender="u1"):
    att = media.Attachment(kind, uuid, "s1") if kind else None
    return IncomingMessage(uuid=uuid, text=text, thread_id="t1", chat_space_id="s1",
                           sender_uuid=sender, sender_name="Aiko", attachment=att,
                           message_type=kind or "text")


def test_a_photo_followed_by_a_caption_is_answered_once_via_the_caption():
    photo, caption = entry("p1", kind="image"), entry("c1", "our new garden")
    b = Bridge(client=FakeClient([photo, caption]), backend=None, persona="")
    b.media_wait = 0
    assert b._followed_by_same_writer(photo) is True
    ctx = b.build_context(caption)
    assert [t.text for t in ctx.turns] == ["(photo)", "our new garden"]
    assert ctx.turns[0].attachments and "p1" in b.client.downloaded


def test_only_the_newest_attachments_are_downloaded():
    history = [entry(f"p{i}", kind="image") for i in range(8)]
    b = Bridge(client=FakeClient(history), backend=None, persona="")
    b.build_context(history[-1])
    assert sorted(b.client.downloaded) == ["p4", "p5", "p6", "p7"]

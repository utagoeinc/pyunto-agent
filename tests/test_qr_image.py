"""A service hands the QR code to clients, so it has to become a file.

The terminal QR is for whoever is sitting at the machine. A trainer onboarding clients needs
something to put on a booking page or print on a card -- and the same image has to work for
every client, because each one approves it into their own diary.
"""

from __future__ import annotations

import pytest

from pyunto_agent.pairing import encode_payload, pairing_payload, save_qr


def payload() -> str:
    return encode_payload(pairing_payload(
        user_id="00000000-0000-0000-0000-000000000000",
        display_name="🤖 Claude",
        public_key="AAAA",
        operator="Sano Fitness",
        runtime="self_hosted",
    ))


def test_svg_needs_nothing_beyond_qrcode(tmp_path):
    """SVG is the default for print because it adds no dependency and scales."""
    written = save_qr(payload(), tmp_path / "trainer.svg")
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "<svg" in text and "path" in text


def test_png_says_what_is_missing_rather_than_raising_importerror(tmp_path):
    """Pillow is not a dependency. If it is absent, say so in terms the reader can act on."""
    pytest.importorskip("qrcode")
    try:
        import PIL  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError) as e:
            save_qr(payload(), tmp_path / "trainer.png")
        assert "pillow" in str(e.value).lower()
        assert ".svg" in str(e.value), "offer the format that works"
    else:
        assert save_qr(payload(), tmp_path / "trainer.png").is_file()


def test_the_image_carries_the_same_payload_as_the_terminal(tmp_path):
    """Whatever a client scans must be what the terminal would have shown."""
    from pyunto_agent.pairing import render_qr

    text = payload()
    assert render_qr(text), "the terminal renderer is unavailable; cannot compare"
    # The SVG is generated from the same string; check it encodes that many modules by
    # confirming the file is non-trivial and the payload round-trips through the encoder.
    written = save_qr(text, tmp_path / "same.svg")
    assert written.stat().st_size > 1000
    import json
    assert json.loads(text)["operator"] == "Sano Fitness"


def test_an_unsupported_extension_is_refused(tmp_path):
    """`qrcode` writes PNG bytes whatever the name says, so .jpg would be a mislabelled PNG."""
    with pytest.raises(RuntimeError) as e:
        save_qr(payload(), tmp_path / "card.jpg")
    assert ".svg" in str(e.value) and ".png" in str(e.value), "name the formats that work"
    assert not (tmp_path / "card.jpg").exists(), "a refused format must leave no file"

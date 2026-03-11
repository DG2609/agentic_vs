"""
Tests for agent/tools/image_input.py — image encoding for multimodal LLMs.
"""
import os
import pytest
from pydantic import ValidationError


# ── Schema validation ────────────────────────────────────────

def test_image_input_args_defaults():
    from agent.tools.image_input import ImageInputArgs
    args = ImageInputArgs(image_path="/tmp/test.png")
    assert args.image_path == "/tmp/test.png"
    assert args.detail == "auto"


def test_image_input_args_requires_path():
    from agent.tools.image_input import ImageInputArgs
    with pytest.raises(ValidationError):
        ImageInputArgs()  # image_path is required


def test_image_input_args_detail_override():
    from agent.tools.image_input import ImageInputArgs
    args = ImageInputArgs(image_path="x.png", detail="high")
    assert args.detail == "high"


# ── Tool error paths ─────────────────────────────────────────

def test_image_input_nonexistent_file(workspace):
    from agent.tools.image_input import image_input
    result = image_input.invoke({"image_path": os.path.join(workspace, "no_such_file.png")})
    assert "Error" in result
    assert "not found" in result


def test_image_input_unsupported_extension(workspace):
    from agent.tools.image_input import image_input
    bad_file = os.path.join(workspace, "test.xyz")
    with open(bad_file, "wb") as f:
        f.write(b"\x00" * 10)
    result = image_input.invoke({"image_path": bad_file})
    assert "Error" in result
    assert "Unsupported" in result


def test_image_input_empty_file(workspace):
    from agent.tools.image_input import image_input
    empty = os.path.join(workspace, "empty.png")
    with open(empty, "wb") as f:
        pass  # 0 bytes
    result = image_input.invoke({"image_path": empty})
    assert "Error" in result
    assert "empty" in result.lower()


# ── Happy path ───────────────────────────────────────────────

def test_image_input_valid_png(workspace):
    from agent.tools.image_input import image_input
    # Minimal valid-ish PNG bytes (tool only checks extension + size, not format)
    png_path = os.path.join(workspace, "screenshot.png")
    with open(png_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    result = image_input.invoke({"image_path": png_path})
    assert "Image loaded" in result
    assert "screenshot.png" in result
    assert "image/png" in result


# ── encode_image_for_message ─────────────────────────────────

def test_encode_image_returns_dict(tmp_path):
    from agent.tools.image_input import encode_image_for_message
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)  # JPEG header-ish
    result = encode_image_for_message(str(img))
    assert result is not None
    assert result["type"] == "image_url"
    assert "image_url" in result
    assert result["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result["image_url"]["detail"] == "auto"


def test_encode_image_detail_param(tmp_path):
    from agent.tools.image_input import encode_image_for_message
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 50)
    result = encode_image_for_message(str(img), detail="high")
    assert result["image_url"]["detail"] == "high"


def test_encode_image_missing_file():
    from agent.tools.image_input import encode_image_for_message
    result = encode_image_for_message("/nonexistent/path/img.png")
    assert result is None


def test_encode_image_unsupported_ext(tmp_path):
    from agent.tools.image_input import encode_image_for_message
    txt = tmp_path / "test.txt"
    txt.write_bytes(b"not an image")
    result = encode_image_for_message(str(txt))
    assert result is None


def test_max_image_size_enforcement(tmp_path):
    from agent.tools.image_input import encode_image_for_message, MAX_IMAGE_SIZE
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x89PNG" + b"\x00" * (MAX_IMAGE_SIZE + 1))
    result = encode_image_for_message(str(big))
    assert result is None

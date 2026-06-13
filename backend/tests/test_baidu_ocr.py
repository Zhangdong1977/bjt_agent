"""Unit + integration tests for BaiduOcrTool (understand_image via 百度云 OCR)."""

from io import BytesIO
from pathlib import Path

import pytest

from backend.agent.tools import baidu_ocr
from backend.agent.tools.baidu_ocr import BaiduOcrTool


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _make_fake_client(token_payload, ocr_payload, recorder):
    """Build a fake httpx.AsyncClient replacement routing token vs OCR by URL."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            recorder.append(url)
            if "oauth/2.0/token" in url:
                return _FakeResp(token_payload)
            return _FakeResp(ocr_payload)

    return _FakeClient


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """Clear module-level token cache before/after each test to avoid pollution."""
    baidu_ocr._token_cache.clear()
    yield
    baidu_ocr._token_cache.clear()


def _make_png(path: Path, size=(50, 50)) -> Path:
    """Write a minimal valid PNG via Pillow."""
    from PIL import Image

    Image.new("RGB", size, color="white").save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_ocr_success_joins_words(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {
        "words_result": [{"words": "第一行"}, {"words": "第二行"}],
        "words_result_num": 2,
        "log_id": 1,
    }
    token_payload = {"access_token": "tok-123", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    tool = BaiduOcrTool()
    result = await tool.execute(prompt="审查", image_source=str(img))

    assert result.success is True
    assert result.content == "第一行\n第二行"
    assert result.data["provider"] == "baidu"
    assert result.data["words_result_num"] == 2
    assert any("oauth/2.0/token" in u for u in recorder)
    assert any("accurate_basic" in u for u in recorder)


@pytest.mark.unit
async def test_access_token_cached_across_calls(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "x"}], "words_result_num": 1}
    token_payload = {"access_token": "tok-123", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    tool = BaiduOcrTool()
    await tool.execute(prompt="p", image_source=str(img))
    await tool.execute(prompt="p", image_source=str(img))

    token_hits = [u for u in recorder if "oauth/2.0/token" in u]
    assert len(token_hits) == 1, (
        f"token endpoint should be hit once (cached), got {len(token_hits)}"
    )


@pytest.mark.unit
async def test_ocr_api_error_returns_failure(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"error_code": 110, "error_msg": "Access token invalid"}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is False
    assert "110" in result.error
    assert "Access token invalid" in result.error


@pytest.mark.unit
async def test_token_fetch_failure(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    token_payload = {"error": "invalid_client", "error_description": "unknown client"}
    ocr_payload = {"words_result": []}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is False
    assert "access_token" in result.error  # "获取百度access_token失败: ..."


@pytest.mark.unit
async def test_missing_credentials():
    tool = BaiduOcrTool()
    tool._api_key = ""
    tool._secret_key = ""

    result = await tool.execute(prompt="p", image_source="/tmp/whatever.png")

    assert result.success is False
    assert "凭证" in result.error or "BAIDU_OCR" in result.error


@pytest.mark.unit
async def test_file_not_found():
    result = await BaiduOcrTool().execute(prompt="p", image_source="/no/such/file_xyz.png")
    assert result.success is False
    assert "不存在" in result.error


@pytest.mark.unit
async def test_unsupported_format(tmp_path):
    bad = tmp_path / "img.gif"
    bad.write_bytes(b"not really a gif")

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(bad))

    assert result.success is False
    assert "格式" in result.error


@pytest.mark.unit
async def test_missing_prompt_or_image_source():
    tool = BaiduOcrTool()
    r1 = await tool.execute(image_source="/tmp/a.png")
    assert r1.success is False and "prompt" in r1.error
    r2 = await tool.execute(prompt="p")
    assert r2.success is False and "image_source" in r2.error


@pytest.mark.unit
async def test_at_prefix_is_stripped(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "ok"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    # MCP-style @ prefix must be tolerated
    result = await BaiduOcrTool().execute(prompt="p", image_source=f"@{img}")

    assert result.success is True
    assert result.content == "ok"


@pytest.mark.unit
def test_maybe_compress_returns_jpeg():
    """Compression helper (Pillow optional dep) returns JPEG bytes deterministically."""
    import os

    from PIL import Image

    raw = os.urandom(300 * 300 * 3)
    img = Image.frombytes("RGB", (300, 300), raw)
    buf = BytesIO()
    img.save(buf, format="PNG")
    original = buf.getvalue()

    compressed = baidu_ocr._maybe_compress(original)
    assert compressed is not None
    assert compressed[:3] == b"\xff\xd8\xff"  # JPEG magic bytes


@pytest.mark.unit
async def test_large_image_compressed_then_recognized(monkeypatch, tmp_path):
    """Image exceeding MAX_IMAGE_SIZE_BYTES is compressed and still recognized."""
    import os

    from PIL import Image

    raw = os.urandom(1800 * 1800 * 3)  # noisy image -> large PNG (>4MB)
    Image.frombytes("RGB", (1800, 1800), raw).save(tmp_path / "big.png", format="PNG")
    img_path = tmp_path / "big.png"
    assert img_path.stat().st_size > baidu_ocr.MAX_IMAGE_SIZE_BYTES

    ocr_payload = {"words_result": [{"words": "ok"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img_path))

    # compression kicked in (otherwise we'd get the "图片过大" error)
    assert result.success is True
    assert any("accurate_basic" in u for u in recorder)


# ---------------------------------------------------------------------------
# integration test (live, gated)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_live_ocr_real_image():
    """Live OCR against Baidu. Gated by BAIDU_OCR_TEST_IMAGE (path to a real image)."""
    import os

    from backend.config import get_settings

    img = os.environ.get("BAIDU_OCR_TEST_IMAGE")
    if not img or not Path(img).exists():
        pytest.skip("set BAIDU_OCR_TEST_IMAGE=<path> to run live OCR test")

    get_settings.cache_clear()
    baidu_ocr._token_cache.clear()
    tool = BaiduOcrTool()
    if not tool._api_key or not tool._secret_key:
        pytest.skip("BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY not configured")

    result = await tool.execute(prompt="识别文字", image_source=img)

    assert result.success is True, f"live OCR failed: {result.error}"
    assert isinstance(result.content, str)

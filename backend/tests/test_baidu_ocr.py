"""Unit + integration tests for BaiduOcrTool (understand_image via 百度云 OCR)."""

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
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


def _make_fake_client(token_payload, ocr_payload, recorder, seal_payload=None):
    """Build a fake httpx.AsyncClient replacement routing token vs OCR/seal by URL."""

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
            if "/ocr/v1/seal" in url:
                return _FakeResp(ocr_payload if seal_payload is None else seal_payload)
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
    # OCR 文字 + 印章检测小节（fake client 未提供印章响应时按 0 印章处理）
    assert result.content.startswith("第一行\n第二行")
    assert "【印章检测】未检出印章" in result.content
    assert result.data["provider"] == "baidu"
    assert result.data["words_result_num"] == 2
    assert result.data["seal_detection"]["status"] == "success"
    assert result.data["seal_detection"]["seals"] == []
    assert any("oauth/2.0/token" in u for u in recorder)
    assert any("accurate_basic" in u for u in recorder)
    assert any("/ocr/v1/seal" in u for u in recorder)


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
    monkeypatch.setattr(baidu_ocr.asyncio, "sleep", AsyncMock())

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is False
    assert "110" in result.error
    assert "Access token invalid" in result.error
    assert len([url for url in recorder if "accurate_basic" in url]) == 4


@pytest.mark.unit
async def test_ocr_timeout_retries_three_times_then_succeeds(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    recorder: list[str] = []
    ocr_attempts = 0

    class _RetryClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            nonlocal ocr_attempts
            recorder.append(url)
            if "oauth/2.0/token" in url:
                return _FakeResp({"access_token": "tok", "expires_in": 2592000})
            if "/ocr/v1/seal" in url:
                # 印章识别一次性成功，不参与 OCR 重试计数
                return _FakeResp({"result": [], "result_num": 0, "log_id": 2})
            ocr_attempts += 1
            if ocr_attempts <= 3:
                raise httpx.TimeoutException("temporary timeout")
            return _FakeResp({"words_result": [{"words": "ok"}], "words_result_num": 1})

    sleep = AsyncMock()
    monkeypatch.setattr(baidu_ocr, "AsyncClient", _RetryClient)
    monkeypatch.setattr(baidu_ocr.asyncio, "sleep", sleep)

    tool = BaiduOcrTool()
    tool._normalization_cache_dir = tmp_path / "cache"
    result = await tool.execute(prompt="p", image_source=str(img))

    assert result.success is True
    assert result.content.startswith("ok")
    assert ocr_attempts == 4
    assert sleep.await_count == 3


@pytest.mark.unit
async def test_ocr_format_error_gets_one_forced_jpeg_retry(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr,
        "AsyncClient",
        _make_fake_client(
            {"access_token": "tok", "expires_in": 2592000},
            {"error_code": 216201, "error_msg": "image format error"},
            recorder,
        ),
    )

    tool = BaiduOcrTool()
    tool._normalization_cache_dir = tmp_path / "cache"
    result = await tool.execute(prompt="p", image_source=str(img))

    assert result.success is False
    assert len([url for url in recorder if "accurate_basic" in url]) == 2


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
async def test_jpx_is_converted_before_baidu_upload(monkeypatch, tmp_path):
    from PIL import Image, features

    if not features.check("jpg_2000"):
        pytest.skip("Pillow was built without JPEG 2000 support")
    image = tmp_path / "certificate.jpx"
    Image.new("RGB", (320, 180), "white").save(image, format="JPEG2000")
    request_images: list[bytes] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if "oauth/2.0/token" in url:
                return _FakeResp({"access_token": "tok", "expires_in": 2592000})
            import base64

            request_images.append(base64.b64decode(kwargs["data"]["image"]))
            return _FakeResp({"words_result": [{"words": "证书"}], "words_result_num": 1})

    monkeypatch.setattr(baidu_ocr, "AsyncClient", _Client)
    tool = BaiduOcrTool()
    tool._normalization_cache_dir = tmp_path / "cache"

    result = await tool.execute(prompt="识别文字", image_source=str(image))

    assert result.success is True
    assert result.data["source_format"] == "JPEG2000"
    assert result.data["normalized_format"] in {"PNG", "JPEG"}
    assert request_images
    assert request_images[0].startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))


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
    assert result.content.startswith("ok")


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

    tool = BaiduOcrTool()
    tool._normalization_cache_dir = tmp_path / "cache"
    result = await tool.execute(prompt="p", image_source=str(img_path))

    # compression kicked in (otherwise we'd get the "图片过大" error)
    assert result.success is True
    assert any("accurate_basic" in u for u in recorder)


# ---------------------------------------------------------------------------
# seal detection tests（understand_image 附带印章识别）
# ---------------------------------------------------------------------------

_SEAL_PAYLOAD_TWO = {
    "result": [
        {
            "type": "circle",
            "color": "red",
            "probability": 0.9982,
            "major": {"words": "深圳般若海科技有限公司", "probability": 0.98},
            "minor": [{"words": "4403040793820", "probability": 0.99}],
            "location": {"left": 736, "top": 1161, "width": 480, "height": 471},
        },
        {
            "type": "perforation",
            "color": "blue",
            "probability": 0.91,
            "major": {"words": "某某监理公司", "probability": 0.9},
            "minor": [],
            "location": {"left": 10, "top": 20, "width": 300, "height": 60},
        },
    ],
    "result_num": 2,
    "log_id": 3,
}


@pytest.mark.unit
async def test_seal_results_appended_to_content(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "投标函"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr,
        "AsyncClient",
        _make_fake_client(token_payload, ocr_payload, recorder, seal_payload=_SEAL_PAYLOAD_TWO),
    )

    result = await BaiduOcrTool().execute(prompt="检查是否盖章", image_source=str(img))

    assert result.success is True
    assert result.content.startswith("投标函")
    assert "【印章检测】共检出 2 个印章" in result.content
    assert "红色圆形印章" in result.content
    assert "章面文字「深圳般若海科技有限公司」" in result.content
    assert "编号/其他文字「4403040793820」" in result.content
    assert "蓝色骑缝章" in result.content
    assert "位置(左上角x=736,y=1161" in result.content
    assert "置信度0.998" in result.content
    seals = result.data["seal_detection"]["seals"]
    assert len(seals) == 2
    assert seals[0]["major"] == "深圳般若海科技有限公司"
    assert seals[1]["type"] == "perforation"
    assert any("/ocr/v1/seal" in u for u in recorder)


@pytest.mark.unit
async def test_seal_zero_seals_reports_explicitly(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "正文"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr,
        "AsyncClient",
        _make_fake_client(
            token_payload, ocr_payload, recorder, seal_payload={"result": [], "result_num": 0}
        ),
    )

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is True
    assert "【印章检测】未检出印章" in result.content


@pytest.mark.unit
async def test_seal_api_error_degrades_but_ocr_succeeds(monkeypatch, tmp_path):
    """印章接口业务错误只降级为提示行，不影响 OCR 成功返回。"""
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "证书内容"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr,
        "AsyncClient",
        _make_fake_client(
            token_payload,
            ocr_payload,
            recorder,
            seal_payload={"error_code": 17, "error_msg": "Open api daily request limit reached"},
        ),
    )

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is True
    assert result.content.startswith("证书内容")
    assert "【印章检测】不可用" in result.content
    assert "百度错误[17]" in result.content
    assert result.data["seal_detection"]["status"] == "error"
    assert result.data["seal_detection"]["error_code"] == "17"


@pytest.mark.unit
async def test_seal_network_error_degrades_but_ocr_succeeds(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "ok"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            recorder.append(url)
            if "oauth/2.0/token" in url:
                return _FakeResp(token_payload)
            if "/ocr/v1/seal" in url:
                raise httpx.ConnectError("seal endpoint down")
            return _FakeResp(ocr_payload)

    monkeypatch.setattr(baidu_ocr, "AsyncClient", _Client)
    monkeypatch.setattr(baidu_ocr.asyncio, "sleep", AsyncMock())

    result = await BaiduOcrTool().execute(prompt="p", image_source=str(img))

    assert result.success is True
    assert result.content.startswith("ok")
    assert "【印章检测】失败" in result.content
    assert result.data["seal_detection"]["status"] == "error"


@pytest.mark.unit
async def test_seal_disabled_skips_seal_call(monkeypatch, tmp_path):
    """BAIDU_SEAL_DETECT_ENABLED=false 时不发印章请求，行为与旧版纯 OCR 一致。"""
    img = _make_png(tmp_path / "a.png")
    ocr_payload = {"words_result": [{"words": "ok"}], "words_result_num": 1}
    token_payload = {"access_token": "tok", "expires_in": 2592000}
    recorder: list[str] = []
    monkeypatch.setattr(
        baidu_ocr, "AsyncClient", _make_fake_client(token_payload, ocr_payload, recorder)
    )

    tool = BaiduOcrTool()
    tool._seal_enabled = False
    result = await tool.execute(prompt="p", image_source=str(img))

    assert result.success is True
    assert result.content == "ok"
    assert not any("/ocr/v1/seal" in u for u in recorder)
    assert result.data["seal_detection"] == {"enabled": False}


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

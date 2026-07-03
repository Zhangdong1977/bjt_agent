"""Unit tests for the stateless image captcha service.

These cover the signing/verification logic without touching the DB or HTTP
layer. ``generate_captcha`` is verified for structure and plaintext-leak
safety; ``verify_captcha`` is exercised via forged tokens (using the real
``_make_digest``) across every branch.
"""

import base64
import json
import time

import pytest
from jose import jwt

from backend.config import get_settings
from backend.services import captcha_service as cs


def _forge_token(code: str, *, nonce: str = "abc12345", ttl: int = 60,
                 digest_override: str | None = None, secret: str | None = None,
                 token_type: str = cs._TOKEN_TYPE, issued_in_past: int = 0):
    """Build a captcha JWT identical to how the service signs one.

    Lets a test pin the plaintext ``code`` (which the real generator hides) and
    tamper with individual claims (digest / type / secret / expiry).
    """
    settings = get_settings()
    now = int(time.time()) - issued_in_past
    digest = digest_override if digest_override is not None else cs._make_digest(nonce, code)
    payload = {
        "type": token_type,
        "nonce": nonce,
        "digest": digest,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret if secret is not None else settings.secret_key,
                      algorithm=settings.algorithm)


class TestGenerateCaptcha:
    def test_returns_signed_jwt_and_png_data_url(self):
        art = cs.generate_captcha()

        # captcha_id is a JWT (header.payload.signature)
        assert art.captcha_id.count(".") == 2
        assert art.image.startswith("data:image/png;base64,")
        assert art.expires_in == cs.CAPTCHA_TTL_SECONDS

    def test_payload_does_not_leak_plaintext_code(self):
        """JWT payload is base64-decodable, so it must never carry the 4-digit code."""
        art = cs.generate_captcha()
        payload = json.loads(base64.urlsafe_b64decode(art.captcha_id.split(".")[1] + "=="))

        assert set(payload) >= {"type", "nonce", "digest", "exp", "iat"}
        for key, value in payload.items():
            assert not (isinstance(value, str) and value.isdigit() and len(value) == cs.CAPTCHA_LENGTH), (
                f"plaintext captcha code leaked under {key!r}: {value!r}"
            )

    def test_generated_image_is_valid_png(self):
        raw = cs._render_png("0413")
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(raw)) as im:
            assert im.format == "PNG"
            assert im.size == (cs.CAPTCHA_WIDTH, cs.CAPTCHA_HEIGHT)


class TestVerifyCaptcha:
    def test_correct_code_verifies(self):
        assert cs.verify_captcha(_forge_token("0413"), "0413") is True

    def test_wrong_code_rejected(self):
        assert cs.verify_captcha(_forge_token("0413"), "9999") is False

    def test_surrounding_whitespace_is_tolerated(self):
        assert cs.verify_captcha(_forge_token("0413"), "  0413  ") is True

    @pytest.mark.parametrize("captcha_id, code", [
        (None, "0413"),
        ("", "0413"),
        (_forge_token("0413"), ""),
        (_forge_token("0413"), None),
    ])
    def test_empty_inputs_rejected(self, captcha_id, code):
        assert cs.verify_captcha(captcha_id, code) is False

    def test_tampered_digest_rejected(self):
        token = _forge_token("0413", digest_override="0" * 64)
        assert cs.verify_captcha(token, "0413") is False

    def test_bad_signature_rejected(self):
        token = _forge_token("0413", secret="not-the-app-secret")
        assert cs.verify_captcha(token, "0413") is False

    def test_expired_token_rejected(self):
        # issued 100s ago, expired 10s ago
        token = _forge_token("0413", ttl=90, issued_in_past=100)
        assert cs.verify_captcha(token, "0413") is False

    def test_wrong_token_type_rejected(self):
        token = _forge_token("0413", token_type="access")
        assert cs.verify_captcha(token, "0413") is False

    def test_garbage_token_rejected(self):
        assert cs.verify_captcha("not-a-jwt", "0413") is False

"""
Security hardening tests for main.py.

Covers the defensive controls added in the security review:
- Audio upload size limit (FIND-02)
- TTS text length cap (FIND-03)
- Language allowlist validation (FIND-09)
- similarity_threshold bounds validation (FIND-08)
- /api/config body size limit (FIND-12)
- /health endpoint (FIND-10)
- Security response headers (FIND-06)

Strategy: same as test_api.py — AsyncMock for TTS/STT, real words_db.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import MAX_AUDIO_BYTES, MAX_CONFIG_BODY, MAX_TEXT_LEN, VALID_LANGUAGES, app

FAKE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" + b"\x00" * 64

MOCK_RECOGNIZE_RESULT = {
    "transcribed": "pilli",
    "expected": "పిల్లి",
    "similarity": 92.0,
    "script_similarity": 85.0,
    "roman_similarity": 92.0,
    "is_correct": True,
    "language": "telugu",
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_returns_ok_status(self, client):
        resp = client.get("/health")
        assert resp.json() == {"status": "ok"}

    def test_fast_response(self, client):
        """Health check must not hit any slow dependencies."""
        import time
        start = time.monotonic()
        client.get("/health")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, "Health check took too long"


# ---------------------------------------------------------------------------
# Security response headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Every response should carry hardening headers."""

    EXPECTED_HEADERS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
    }

    def test_headers_on_health(self, client):
        resp = client.get("/health")
        for header, value in self.EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing/wrong header: {header}"

    def test_headers_on_home_page(self, client):
        resp = client.get("/")
        for header, value in self.EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing/wrong header: {header}"

    def test_headers_on_api_config(self, client):
        resp = client.get("/api/config")
        for header, value in self.EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing/wrong header: {header}"

    def test_csp_present(self, client):
        resp = client.get("/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src" in csp
        assert "'self'" in csp

    def test_hsts_present(self, client):
        resp = client.get("/health")
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=" in hsts


# ---------------------------------------------------------------------------
# /api/tts — text length and language validation
# ---------------------------------------------------------------------------


class TestTTSInputValidation:
    def test_text_at_max_length_accepted(self, client):
        text = "a" * MAX_TEXT_LEN
        with patch("main.generate_tts", new_callable=AsyncMock) as mock_tts:
            mock_tts.return_value = FAKE_MP3
            resp = client.get(f"/api/tts?text={text}&language=telugu")
        assert resp.status_code == 200

    def test_text_over_max_length_rejected(self, client):
        text = "a" * (MAX_TEXT_LEN + 1)
        resp = client.get(f"/api/tts?text={text}&language=telugu")
        assert resp.status_code == 400
        assert "too long" in resp.json()["detail"].lower()

    def test_invalid_language_rejected(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock):
            resp = client.get("/api/tts?text=hello&language=klingon")
        assert resp.status_code == 400
        assert "invalid language" in resp.json()["detail"].lower()

    def test_valid_languages_accepted(self, client):
        for lang in VALID_LANGUAGES:
            with patch("main.generate_tts", new_callable=AsyncMock) as mock_tts:
                mock_tts.return_value = FAKE_MP3
                resp = client.get(f"/api/tts?text=test&language={lang}")
            assert resp.status_code == 200, f"Language '{lang}' was unexpectedly rejected"


# ---------------------------------------------------------------------------
# /api/dino-voice — text length validation
# ---------------------------------------------------------------------------


class TestDinoVoiceInputValidation:
    def test_text_at_max_length_accepted(self, client):
        text = "a" * MAX_TEXT_LEN
        with patch("main.generate_tts", new_callable=AsyncMock) as mock_tts:
            mock_tts.return_value = FAKE_MP3
            resp = client.get(f"/api/dino-voice?text={text}")
        assert resp.status_code == 200

    def test_text_over_max_length_rejected(self, client):
        text = "a" * (MAX_TEXT_LEN + 1)
        resp = client.get(f"/api/dino-voice?text={text}")
        assert resp.status_code == 400
        assert "too long" in resp.json()["detail"].lower()

    def test_empty_text_rejected(self, client):
        resp = client.get("/api/dino-voice?text=   ")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/recognize — audio size, language, threshold validation
# ---------------------------------------------------------------------------


class TestRecognizeInputValidation:
    def _post_audio(self, client, audio_data: bytes, language: str = "telugu",
                    threshold: str = "50") -> object:
        return client.post(
            "/api/recognize",
            data={
                "language": language,
                "expected_word": "పిల్లి",
                "romanized": "pilli",
                "audio_format": "audio/webm",
                "similarity_threshold": threshold,
            },
            files={"audio": ("test.webm", audio_data, "audio/webm")},
        )

    def test_audio_within_size_limit_accepted(self, client):
        small_audio = b"\x00" * 1024  # 1 KB
        with patch("main.recognize_speech", new_callable=AsyncMock) as mock_rec:
            mock_rec.return_value = MOCK_RECOGNIZE_RESULT
            resp = self._post_audio(client, small_audio)
        assert resp.status_code == 200

    def test_audio_over_size_limit_rejected(self, client):
        # Send a body larger than MAX_AUDIO_BYTES
        oversized = b"\x00" * (MAX_AUDIO_BYTES + 1)
        resp = self._post_audio(client, oversized)
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_invalid_language_rejected(self, client):
        resp = self._post_audio(client, b"\x00" * 100, language="martian")
        assert resp.status_code == 400
        assert "invalid language" in resp.json()["detail"].lower()

    def test_valid_languages_accepted(self, client):
        for lang in ("telugu", "assamese"):
            with patch("main.recognize_speech", new_callable=AsyncMock) as mock_rec:
                mock_rec.return_value = {**MOCK_RECOGNIZE_RESULT, "language": lang}
                resp = self._post_audio(client, b"\x00" * 100, language=lang)
            assert resp.status_code == 200, f"Language '{lang}' was unexpectedly rejected"

    def test_threshold_below_zero_rejected(self, client):
        resp = self._post_audio(client, b"\x00" * 100, threshold="-1")
        assert resp.status_code == 400
        assert "between 0 and 100" in resp.json()["detail"].lower()

    def test_threshold_above_100_rejected(self, client):
        resp = self._post_audio(client, b"\x00" * 100, threshold="101")
        assert resp.status_code == 400
        assert "between 0 and 100" in resp.json()["detail"].lower()

    def test_threshold_not_a_number_rejected(self, client):
        resp = self._post_audio(client, b"\x00" * 100, threshold="abc")
        assert resp.status_code == 400
        assert "must be a number" in resp.json()["detail"].lower()

    def test_threshold_zero_accepted(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock) as mock_rec:
            mock_rec.return_value = MOCK_RECOGNIZE_RESULT
            resp = self._post_audio(client, b"\x00" * 100, threshold="0")
        assert resp.status_code == 200

    def test_threshold_100_accepted(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock) as mock_rec:
            mock_rec.return_value = MOCK_RECOGNIZE_RESULT
            resp = self._post_audio(client, b"\x00" * 100, threshold="100")
        assert resp.status_code == 200

    def test_empty_audio_rejected(self, client):
        resp = self._post_audio(client, b"")
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/config POST — body size limit
# ---------------------------------------------------------------------------


class TestConfigBodySizeLimit:
    def test_small_body_accepted(self, client):
        resp = client.post("/api/config", json={"child_name": "Myra"})
        assert resp.status_code == 200

    def test_oversized_body_rejected(self, client):
        # Build a payload larger than MAX_CONFIG_BODY
        big_name = "x" * (MAX_CONFIG_BODY + 100)
        resp = client.post(
            "/api/config",
            json={"child_name": big_name},
            headers={"content-length": str(MAX_CONFIG_BODY + 1000)},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestSecurityConstants:
    def test_max_audio_bytes_is_10mb(self):
        assert MAX_AUDIO_BYTES == 10 * 1024 * 1024

    def test_max_text_len_is_200(self):
        assert MAX_TEXT_LEN == 200

    def test_valid_languages_set(self):
        assert VALID_LANGUAGES == {"telugu", "assamese", "english"}

    def test_max_config_body_is_4kb(self):
        assert MAX_CONFIG_BODY == 4096

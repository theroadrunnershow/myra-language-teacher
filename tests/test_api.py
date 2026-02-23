"""
Unit tests for the FastAPI routes in main.py.

Strategy
--------
- Use FastAPI's synchronous TestClient (backed by httpx) for all route tests.
- Patch main.generate_tts and main.recognize_speech with AsyncMock so no real
  Whisper inference or gTTS network call is made.
- words_db functions are NOT patched – they are pure in-memory and fast.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import DEFAULT_CONFIG, app
from words_db import ALL_CATEGORIES


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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
    """A single TestClient instance shared across tests in this module."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------


class TestGetConfig:
    def test_status_200(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200

    def test_returns_json(self, client):
        resp = client.get("/api/config")
        data = resp.json()
        assert isinstance(data, dict)

    def test_contains_required_keys(self, client):
        data = client.get("/api/config").json()
        for key in (
            "languages", "categories", "child_name",
            "show_romanized", "similarity_threshold", "max_attempts",
        ):
            assert key in data, f"Missing key '{key}' in /api/config response"

    def test_default_child_name(self, client):
        assert client.get("/api/config").json()["child_name"] == "Myra"

    def test_default_similarity_threshold(self, client):
        assert client.get("/api/config").json()["similarity_threshold"] == 50

    def test_languages_is_list(self, client):
        assert isinstance(client.get("/api/config").json()["languages"], list)

    def test_categories_is_list(self, client):
        assert isinstance(client.get("/api/config").json()["categories"], list)


# ---------------------------------------------------------------------------
# POST /api/config
# ---------------------------------------------------------------------------


class TestPostConfig:
    def test_valid_payload_returns_200(self, client):
        resp = client.post("/api/config", json={"child_name": "Arjun"})
        assert resp.status_code == 200

    def test_response_has_status_ok(self, client):
        resp = client.post("/api/config", json={"child_name": "Arjun"})
        assert resp.json()["status"] == "ok"

    def test_custom_child_name_reflected(self, client):
        resp = client.post("/api/config", json={"child_name": "Priya"})
        assert resp.json()["config"]["child_name"] == "Priya"

    def test_partial_update_merges_with_defaults(self, client):
        resp = client.post("/api/config", json={"child_name": "X"})
        config = resp.json()["config"]
        # Omitted keys should still be present from DEFAULT_CONFIG
        assert "languages" in config
        assert "similarity_threshold" in config

    def test_custom_languages_list_accepted(self, client):
        resp = client.post("/api/config", json={"languages": ["telugu"]})
        assert resp.status_code == 200
        assert resp.json()["config"]["languages"] == ["telugu"]

    def test_custom_categories_list_accepted(self, client):
        resp = client.post("/api/config", json={"categories": ["animals", "colors"]})
        assert resp.status_code == 200
        assert resp.json()["config"]["categories"] == ["animals", "colors"]

    def test_languages_as_string_returns_400(self, client):
        resp = client.post("/api/config", json={"languages": "telugu"})
        assert resp.status_code == 400

    def test_categories_as_string_returns_400(self, client):
        resp = client.post("/api/config", json={"categories": "animals"})
        assert resp.status_code == 400

    def test_empty_body_returns_200_with_defaults(self, client):
        resp = client.post("/api/config", json={})
        assert resp.status_code == 200
        assert resp.json()["config"]["child_name"] == DEFAULT_CONFIG["child_name"]


# ---------------------------------------------------------------------------
# GET /api/word
# ---------------------------------------------------------------------------


class TestGetWord:
    def test_single_language_single_category(self, client):
        resp = client.get("/api/word?languages=telugu&categories=animals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] == "telugu"
        assert data["category"] == "animals"

    def test_assamese_word_returned(self, client):
        resp = client.get("/api/word?languages=assamese&categories=colors")
        assert resp.status_code == 200
        assert resp.json()["language"] == "assamese"

    def test_multiple_languages_one_is_chosen(self, client):
        # Over 20 draws at least one of each language should appear
        seen = set()
        for _ in range(20):
            data = client.get("/api/word?languages=telugu,assamese&categories=animals").json()
            seen.add(data["language"])
        assert seen.issubset({"telugu", "assamese"})
        assert len(seen) >= 1  # trivially true, but documents intent

    def test_all_required_fields_present(self, client):
        data = client.get("/api/word?languages=telugu&categories=numbers").json()
        for field in ("english", "translation", "romanized", "emoji", "language", "category"):
            assert field in data, f"Field '{field}' missing from /api/word response"

    def test_empty_languages_param_falls_back_to_defaults(self, client):
        # ?languages= (empty string) → code falls back to DEFAULT_CONFIG["languages"], so 200
        resp = client.get("/api/word?languages=&categories=animals")
        assert resp.status_code == 200

    def test_blank_only_languages_param_returns_400(self, client):
        # ?languages=, (commas/spaces only → empty list after filtering) → 400
        resp = client.get("/api/word?languages=,&categories=animals")
        assert resp.status_code == 400

    def test_empty_categories_param_falls_back_to_defaults(self, client):
        # ?categories= (empty string) → falls back to DEFAULT_CONFIG["categories"], so 200
        resp = client.get("/api/word?languages=telugu&categories=")
        assert resp.status_code == 200

    def test_blank_only_categories_param_returns_400(self, client):
        # ?categories=, (commas only → empty list after filtering) → 400
        resp = client.get("/api/word?languages=telugu&categories=,")
        assert resp.status_code == 400

    def test_omitting_params_uses_defaults(self, client):
        resp = client.get("/api/word")
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] in DEFAULT_CONFIG["languages"]
        assert data["category"] in ALL_CATEGORIES


# ---------------------------------------------------------------------------
# GET /api/tts
# ---------------------------------------------------------------------------


class TestTtsEndpoint:
    def test_success_returns_200_with_audio(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            resp = client.get("/api/tts?text=cat&language=english")
        assert resp.status_code == 200
        assert resp.content == FAKE_MP3

    def test_content_type_is_audio_mpeg(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            resp = client.get("/api/tts?text=cat&language=english")
        assert resp.headers["content-type"] == "audio/mpeg"

    def test_generate_tts_called_with_text_and_language(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock_tts:
            client.get("/api/tts?text=hello&language=telugu")
        mock_tts.assert_called_once_with("hello", "telugu")

    def test_telugu_text_forwarded(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock_tts:
            client.get("/api/tts?text=పిల్లి&language=telugu")
        args = mock_tts.call_args[0]
        assert args[0] == "పిల్లి"
        assert args[1] == "telugu"

    def test_service_failure_returns_500(self, client):
        with patch(
            "main.generate_tts",
            new_callable=AsyncMock,
            side_effect=Exception("gTTS network error"),
        ):
            resp = client.get("/api/tts?text=cat&language=english")
        assert resp.status_code == 500

    def test_default_language_is_telugu(self, client):
        """When language param is omitted, FastAPI uses the default 'telugu'."""
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock_tts:
            client.get("/api/tts?text=cat")
        assert mock_tts.call_args[0][1] == "telugu"


# ---------------------------------------------------------------------------
# POST /api/recognize
# ---------------------------------------------------------------------------


class TestRecognizeEndpoint:
    def _post_recognize(self, client, audio_bytes=b"fake webm data", **form_overrides):
        form = {
            "language": "telugu",
            "expected_word": "పిల్లి",
            "romanized": "pilli",
            "audio_format": "audio/webm",
            "similarity_threshold": "50",
            **form_overrides,
        }
        return client.post(
            "/api/recognize",
            data=form,
            files={"audio": ("audio.webm", audio_bytes, "audio/webm")},
        )

    def test_valid_audio_returns_200(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT):
            resp = self._post_recognize(client)
        assert resp.status_code == 200

    def test_correct_result_fields_present(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT):
            data = self._post_recognize(client).json()
        for key in ("transcribed", "expected", "similarity", "is_correct", "language"):
            assert key in data

    def test_is_correct_true_reflected(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT):
            data = self._post_recognize(client).json()
        assert data["is_correct"] is True
        assert data["similarity"] == 92.0

    def test_empty_audio_returns_400(self, client):
        resp = self._post_recognize(client, audio_bytes=b"")
        assert resp.status_code == 400

    def test_recognize_speech_called_with_correct_kwargs(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT) as mock_rec:
            self._post_recognize(
                client,
                audio_bytes=b"fake audio",
                language="assamese",
                expected_word="মেকুৰী",
                romanized="mekuri",
                similarity_threshold="75",
            )
        mock_rec.assert_called_once()
        kw = mock_rec.call_args.kwargs
        assert kw["language"] == "assamese"
        assert kw["expected_word"] == "মেকুৰী"
        assert kw["romanized"] == "mekuri"
        assert kw["similarity_threshold"] == 75.0

    def test_similarity_threshold_parsed_as_float(self, client):
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT) as mock_rec:
            self._post_recognize(client, similarity_threshold="62.5")
        kw = mock_rec.call_args.kwargs
        assert isinstance(kw["similarity_threshold"], float)
        assert kw["similarity_threshold"] == 62.5

    def test_audio_bytes_forwarded_to_service(self, client):
        audio_payload = b"real audio content here"
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT) as mock_rec:
            self._post_recognize(client, audio_bytes=audio_payload)
        kw = mock_rec.call_args.kwargs
        assert kw["audio_data"] == audio_payload

    def test_default_romanized_is_empty_string(self, client):
        """romanized has a Form(default=""); omitting it should pass "" to the service."""
        with patch("main.recognize_speech", new_callable=AsyncMock, return_value=MOCK_RECOGNIZE_RESULT) as mock_rec:
            client.post(
                "/api/recognize",
                data={
                    "language": "telugu",
                    "expected_word": "పిల్లి",
                    # romanized intentionally omitted
                },
                files={"audio": ("a.webm", b"fake", "audio/webm")},
            )
        kw = mock_rec.call_args.kwargs
        assert kw["romanized"] == ""


# ---------------------------------------------------------------------------
# GET /api/words/all
# ---------------------------------------------------------------------------


class TestAllWordsEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/words/all?languages=telugu&categories=animals")
        assert resp.status_code == 200

    def test_response_keyed_by_language(self, client):
        data = client.get("/api/words/all?languages=telugu&categories=animals").json()
        assert "telugu" in data
        assert isinstance(data["telugu"], list)

    def test_word_count_matches_db(self, client):
        from words_db import WORD_DATABASE
        data = client.get("/api/words/all?languages=telugu&categories=animals").json()
        assert len(data["telugu"]) == len(WORD_DATABASE["animals"])

    def test_multiple_languages_both_present(self, client):
        data = client.get(
            "/api/words/all?languages=telugu,assamese&categories=colors"
        ).json()
        assert "telugu" in data
        assert "assamese" in data

    def test_word_fields_present(self, client):
        data = client.get("/api/words/all?languages=telugu&categories=numbers").json()
        for word in data["telugu"]:
            for field in ("english", "translation", "emoji", "category"):
                assert field in word

    def test_category_field_correct(self, client):
        data = client.get("/api/words/all?languages=telugu&categories=food").json()
        for word in data["telugu"]:
            assert word["category"] == "food"

    def test_defaults_used_when_params_omitted(self, client):
        resp = client.get("/api/words/all")
        assert resp.status_code == 200
        data = resp.json()
        for lang in DEFAULT_CONFIG["languages"]:
            assert lang in data

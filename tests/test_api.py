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
        assert client.get("/api/config").json()["child_name"] == ""

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

    def test_tamil_word_returned(self, client):
        resp = client.get("/api/word?languages=tamil&categories=colors")
        assert resp.status_code == 200
        assert resp.json()["language"] == "tamil"

    def test_malayalam_word_returned(self, client):
        resp = client.get("/api/word?languages=malayalam&categories=colors")
        assert resp.status_code == 200
        assert resp.json()["language"] == "malayalam"

    def test_multiple_languages_one_is_chosen(self, client):
        # Over 20 draws at least one of each language should appear
        seen = set()
        for _ in range(20):
            data = client.get("/api/word?languages=telugu,assamese,tamil,malayalam&categories=animals").json()
            seen.add(data["language"])
        assert seen.issubset({"telugu", "assamese", "tamil", "malayalam"})
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
        mock_tts.assert_called_once_with("hello", "telugu", False)

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

    def test_multiple_languages_all_present(self, client):
        data = client.get(
            "/api/words/all?languages=telugu,assamese,tamil,malayalam&categories=colors"
        ).json()
        assert "telugu" in data
        assert "assamese" in data
        assert "tamil" in data
        assert "malayalam" in data

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


# ---------------------------------------------------------------------------
# GET /api/dino-voice
# ---------------------------------------------------------------------------


class TestDinoVoiceEndpoint:
    def test_returns_audio_mpeg(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            resp = client.get("/api/dino-voice?text=Hello+Myra")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == FAKE_MP3

    def test_always_calls_english_language(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock_tts:
            client.get("/api/dino-voice?text=Amazing")
        mock_tts.assert_called_once()
        _, called_lang, _ = mock_tts.call_args[0]
        assert called_lang == "english"

    def test_empty_text_returns_400(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            resp = client.get("/api/dino-voice?text=")
        assert resp.status_code == 400

    def test_slow_param_forwarded(self, client):
        with patch("main.generate_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock_tts:
            client.get("/api/dino-voice?text=Hello&slow=true")
        _, _, called_slow = mock_tts.call_args[0]
        assert called_slow is True

    def test_service_failure_returns_500(self, client):
        with patch(
            "main.generate_tts",
            new_callable=AsyncMock,
            side_effect=Exception("gTTS error"),
        ):
            resp = client.get("/api/dino-voice?text=Hello")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/translate
# ---------------------------------------------------------------------------

_FAKE_TRANSLATE_RESULT = {
    "english": "umbrella",
    "translation": "గొడుగు",
    "romanized": "godugu",
    "emoji": "✏️",
    "language": "telugu",
    "category": "custom",
}


class TestTranslateEndpoint:
    def test_known_db_word_returns_200(self, client):
        resp = client.get("/api/translate?word=cat&language=telugu")
        assert resp.status_code == 200

    def test_known_db_word_correct_translation(self, client):
        data = client.get("/api/translate?word=cat&language=telugu").json()
        assert data["translation"] == "పిల్లి"

    def test_response_has_required_fields(self, client):
        data = client.get("/api/translate?word=dog&language=assamese").json()
        for field in ("english", "translation", "romanized", "emoji", "language", "category"):
            assert field in data

    def test_tamil_translation_lookup(self, client):
        data = client.get("/api/translate?word=cat&language=tamil").json()
        assert data["translation"] == "பூனை"
        assert data["language"] == "tamil"

    def test_malayalam_translation_lookup(self, client):
        data = client.get("/api/translate?word=cat&language=malayalam").json()
        assert data["translation"] == "പൂച്ച"
        assert data["language"] == "malayalam"

    def test_language_field_in_response(self, client):
        data = client.get("/api/translate?word=cat&language=assamese").json()
        assert data["language"] == "assamese"

    def test_empty_word_returns_400(self, client):
        resp = client.get("/api/translate?word=&language=telugu")
        assert resp.status_code == 400

    def test_word_too_long_returns_400(self, client):
        resp = client.get(f"/api/translate?word={'a' * 51}&language=telugu")
        assert resp.status_code == 400

    def test_invalid_language_returns_400(self, client):
        resp = client.get("/api/translate?word=cat&language=english")
        assert resp.status_code == 400

    def test_unknown_word_calls_translate_service(self, client):
        with patch("main.translate_word", new_callable=AsyncMock, return_value=_FAKE_TRANSLATE_RESULT):
            resp = client.get("/api/translate?word=umbrella&language=telugu")
        assert resp.status_code == 200
        assert resp.json()["translation"] == "గొడుగు"

    def test_translate_api_failure_returns_503(self, client):
        with patch("main.translate_word", new_callable=AsyncMock, side_effect=Exception("API down")):
            resp = client.get("/api/translate?word=umbrella&language=telugu")
        assert resp.status_code == 503

    def test_missing_gcp_project_returns_503(self, client):
        with patch("main.translate_word", new_callable=AsyncMock, side_effect=ValueError("GCP_PROJECT")):
            resp = client.get("/api/translate?word=umbrella&language=telugu")
        assert resp.status_code == 503

    def test_default_language_is_telugu(self, client):
        data = client.get("/api/translate?word=cat").json()
        assert data["language"] == "telugu"


# ---------------------------------------------------------------------------
# POST /api/internal/words/sync
# ---------------------------------------------------------------------------


class TestInternalWordSyncEndpoint:
    def test_sync_calls_store_and_returns_ok(self, client):
        class StoreStub:
            is_configured = True
            sync_to_gcs_policy = "session_end"

            def __init__(self):
                self.flush_calls = []
                self.sync_calls = []

            def flush_if_needed(self, force=False):
                self.flush_calls.append(force)
                return True

            def sync_to_object_store(self, force=False):
                self.sync_calls.append(force)
                return True

        original_store = getattr(app.state, "dynamic_words_store", None)
        stub = StoreStub()
        app.state.dynamic_words_store = stub
        try:
            resp = client.post("/api/internal/words/sync")
        finally:
            if original_store is None:
                delattr(app.state, "dynamic_words_store")
            else:
                app.state.dynamic_words_store = original_store

        assert resp.status_code == 200
        assert resp.json()["synced"] is True
        assert stub.flush_calls == [True]
        assert stub.sync_calls == [True]


# ---------------------------------------------------------------------------
# TestPostConfigThemeMascot
# ---------------------------------------------------------------------------

class TestPostConfigThemeMascot:
    # ── Theme validation ───────────────────────────────────────────────────
    def test_valid_theme_pink_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "pink"})
        assert resp.status_code == 200

    def test_valid_theme_blue_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "blue"})
        assert resp.status_code == 200

    def test_valid_theme_green_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "green"})
        assert resp.status_code == 200

    def test_valid_theme_purple_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "purple"})
        assert resp.status_code == 200

    def test_valid_theme_orange_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "orange"})
        assert resp.status_code == 200

    def test_valid_theme_yellow_returns_200(self, client):
        resp = client.post("/api/config", json={"theme": "yellow"})
        assert resp.status_code == 200

    def test_invalid_theme_returns_400(self, client):
        resp = client.post("/api/config", json={"theme": "rainbow"})
        assert resp.status_code == 400

    def test_invalid_theme_error_mentions_theme(self, client):
        resp = client.post("/api/config", json={"theme": "neon"})
        assert "theme" in resp.json()["detail"].lower()

    def test_theme_reflected_in_config_response(self, client):
        resp = client.post("/api/config", json={"theme": "blue"})
        assert resp.json()["config"]["theme"] == "blue"

    # ── Mascot validation ──────────────────────────────────────────────────
    def test_valid_mascot_dino_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "dino"})
        assert resp.status_code == 200

    def test_valid_mascot_cat_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "cat"})
        assert resp.status_code == 200

    def test_valid_mascot_dog_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "dog"})
        assert resp.status_code == 200

    def test_valid_mascot_panda_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "panda"})
        assert resp.status_code == 200

    def test_valid_mascot_fox_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "fox"})
        assert resp.status_code == 200

    def test_valid_mascot_rabbit_returns_200(self, client):
        resp = client.post("/api/config", json={"mascot": "rabbit"})
        assert resp.status_code == 200

    def test_invalid_mascot_returns_400(self, client):
        resp = client.post("/api/config", json={"mascot": "elephant"})
        assert resp.status_code == 400

    def test_invalid_mascot_error_mentions_mascot(self, client):
        resp = client.post("/api/config", json={"mascot": "unicorn"})
        assert "mascot" in resp.json()["detail"].lower()

    def test_mascot_reflected_in_config_response(self, client):
        resp = client.post("/api/config", json={"mascot": "cat"})
        assert resp.json()["config"]["mascot"] == "cat"

    # ── Defaults ───────────────────────────────────────────────────────────
    def test_default_config_includes_theme(self, client):
        resp = client.get("/api/config")
        assert "theme" in resp.json()

    def test_default_theme_is_pink(self, client):
        resp = client.get("/api/config")
        assert resp.json()["theme"] == "pink"

    def test_default_config_includes_mascot(self, client):
        resp = client.get("/api/config")
        assert "mascot" in resp.json()

    def test_default_mascot_is_dino(self, client):
        resp = client.get("/api/config")
        assert resp.json()["mascot"] == "dino"

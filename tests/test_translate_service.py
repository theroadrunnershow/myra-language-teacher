"""
Unit tests for translate_service.py.

Strategy
--------
- Stub google.cloud.translate_v3 at import time so tests run without the package.
- Use real words_db for DB lookup tests.
- Patch _translate_and_romanize_sync for async path tests (avoids gRPC).
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub google.cloud.translate_v3 so tests run without the package installed.
# ---------------------------------------------------------------------------
_translate_v3_stub = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.cloud", MagicMock())
sys.modules["google.cloud.translate_v3"] = _translate_v3_stub

import translate_service  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache_and_client():
    """Isolate each test from cache and client state."""
    translate_service._translation_cache.clear()
    translate_service._translate_client = None
    translate_service._dynamic_words_store = None
    yield
    translate_service._translation_cache.clear()
    translate_service._translate_client = None
    translate_service._dynamic_words_store = None


# ---------------------------------------------------------------------------
# DB lookup tests
# ---------------------------------------------------------------------------

class TestDbLookup:
    def test_known_telugu_word(self):
        result = translate_service._lookup_in_db("cat", "telugu")
        assert result is not None
        assert result["translation"] == "పిల్లి"

    def test_known_assamese_word(self):
        result = translate_service._lookup_in_db("cat", "assamese")
        assert result is not None
        assert result["translation"] == "মেকুৰী"

    def test_known_tamil_word(self):
        result = translate_service._lookup_in_db("cat", "tamil")
        assert result is not None
        assert result["translation"] == "பூனை"

    def test_known_malayalam_word(self):
        result = translate_service._lookup_in_db("cat", "malayalam")
        assert result is not None
        assert result["translation"] == "പൂച്ച"

    def test_case_insensitive(self):
        assert translate_service._lookup_in_db("CAT", "telugu") is not None
        assert translate_service._lookup_in_db("Cat", "telugu") is not None

    def test_unknown_word_returns_none(self):
        assert translate_service._lookup_in_db("zymurgy", "telugu") is None

    def test_result_shape(self):
        result = translate_service._lookup_in_db("dog", "telugu")
        for field in ("english", "translation", "romanized", "emoji", "language", "category"):
            assert field in result

    def test_telugu_romanized(self):
        result = translate_service._lookup_in_db("dog", "telugu")
        assert result["romanized"] == "kukka"

    def test_assamese_romanized(self):
        result = translate_service._lookup_in_db("dog", "assamese")
        assert result["romanized"] == "kukur"

    def test_tamil_romanized(self):
        result = translate_service._lookup_in_db("dog", "tamil")
        assert result["romanized"] == "naai"

    def test_malayalam_romanized(self):
        result = translate_service._lookup_in_db("dog", "malayalam")
        assert result["romanized"] == "naaya"

    def test_language_field(self):
        result = translate_service._lookup_in_db("cat", "assamese")
        assert result["language"] == "assamese"

    def test_category_field(self):
        result = translate_service._lookup_in_db("cat", "telugu")
        assert result["category"] == "animals"


# ---------------------------------------------------------------------------
# translate_word async tests
# ---------------------------------------------------------------------------

_FAKE_API_RESULT = {
    "english": "zymurgy",
    "translation": "జ్యమర్జీ",
    "romanized": "zymarji",
    "emoji": "✏️",
    "language": "telugu",
    "category": "custom",
}


class TestTranslateWord:
    @pytest.mark.anyio
    async def test_db_word_no_api_call(self):
        with patch.object(translate_service, "_translate_and_romanize_sync") as mock_api:
            result = await translate_service.translate_word("cat", "telugu")
        mock_api.assert_not_called()
        assert result["translation"] == "పిల్లి"

    @pytest.mark.anyio
    async def test_dynamic_store_hit_no_api_call(self):
        dynamic_result = {
            "english": "umbrella",
            "translation": "గొడుగు",
            "romanized": "godugu",
            "emoji": "✏️",
            "language": "telugu",
            "category": "custom",
        }

        class StoreStub:
            def lookup(self, english_word, language):
                if english_word.lower() == "umbrella" and language == "telugu":
                    return dynamic_result
                return None

            def upsert(self, word):
                return None

        translate_service.set_dynamic_words_store(StoreStub())
        with patch.object(translate_service, "_translate_and_romanize_sync") as mock_api:
            result = await translate_service.translate_word("umbrella", "telugu")

        mock_api.assert_not_called()
        assert result["translation"] == "గొడుగు"

    @pytest.mark.anyio
    async def test_unknown_word_calls_api(self):
        with patch.dict("os.environ", {"GCP_PROJECT": "test-project"}):
            with patch.object(
                translate_service, "_translate_and_romanize_sync", return_value=_FAKE_API_RESULT
            ) as mock_api:
                await translate_service.translate_word("zymurgy", "telugu")
        mock_api.assert_called_once()

    @pytest.mark.anyio
    async def test_unknown_word_upserts_dynamic_store(self):
        class StoreStub:
            def __init__(self):
                self.upserts = []

            def lookup(self, english_word, language):
                return None

            def upsert(self, word):
                self.upserts.append(word)

        store = StoreStub()
        translate_service.set_dynamic_words_store(store)

        with patch.dict("os.environ", {"GCP_PROJECT": "test-project"}):
            with patch.object(
                translate_service, "_translate_and_romanize_sync", return_value=_FAKE_API_RESULT
            ):
                await translate_service.translate_word("zymurgy", "telugu")

        assert len(store.upserts) == 1
        assert store.upserts[0]["english"] == "zymurgy"

    @pytest.mark.anyio
    async def test_cache_prevents_second_api_call(self):
        with patch.dict("os.environ", {"GCP_PROJECT": "test-project"}):
            with patch.object(
                translate_service, "_translate_and_romanize_sync", return_value=_FAKE_API_RESULT
            ) as mock_api:
                await translate_service.translate_word("zymurgy", "telugu")
                await translate_service.translate_word("zymurgy", "telugu")
        assert mock_api.call_count == 1

    @pytest.mark.anyio
    async def test_cache_is_language_specific(self):
        fake_asm = {**_FAKE_API_RESULT, "language": "assamese", "translation": "অস"}
        with patch.dict("os.environ", {"GCP_PROJECT": "test-project"}):
            with patch.object(
                translate_service,
                "_translate_and_romanize_sync",
                side_effect=[_FAKE_API_RESULT, fake_asm],
            ) as mock_api:
                r1 = await translate_service.translate_word("zymurgy", "telugu")
                r2 = await translate_service.translate_word("zymurgy", "assamese")
        assert mock_api.call_count == 2
        assert r1["language"] == "telugu"
        assert r2["language"] == "assamese"

    @pytest.mark.anyio
    async def test_missing_gcp_project_raises(self):
        import os
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="GCP_PROJECT"):
                await translate_service.translate_word("zymurgy", "telugu")

    @pytest.mark.anyio
    async def test_db_result_cached(self):
        await translate_service.translate_word("cat", "telugu")
        assert ("cat", "telugu") in translate_service._translation_cache


# ---------------------------------------------------------------------------
# Romanization fallback tests
# ---------------------------------------------------------------------------

class TestRomanizationFallback:
    def test_romanize_exception_falls_back_to_indic(self):
        """When romanize_text throws, indic_transliteration fallback is used for Telugu."""
        client_mock = MagicMock()
        client_mock.translate_text.return_value.translations = [
            MagicMock(translated_text="ఏనుగు")
        ]
        client_mock.romanize_text.side_effect = Exception("not supported")

        with patch.object(translate_service, "_get_translate_client", return_value=client_mock):
            result = translate_service._translate_and_romanize_sync(
                "elephant", "telugu", "test-project"
            )
        # Indic fallback should produce a non-empty romanization (e.g. "enugu")
        assert result["romanized"] != ""
        assert result["translation"] == "ఏనుగు"

    def test_romanize_exception_returns_empty_when_fallback_also_fails(self):
        """When both romanize_text and indic fallback fail, romanized is empty."""
        client_mock = MagicMock()
        client_mock.translate_text.return_value.translations = [
            MagicMock(translated_text="ఏనుగు")
        ]
        client_mock.romanize_text.side_effect = Exception("not supported")

        with patch.object(translate_service, "_get_translate_client", return_value=client_mock):
            with patch.object(translate_service, "_romanize_indic_fallback", return_value=""):
                result = translate_service._translate_and_romanize_sync(
                    "elephant", "telugu", "test-project"
                )
        assert result["romanized"] == ""
        assert result["translation"] == "ఏనుగు"

    def test_romanize_empty_string_stays_empty(self):
        client_mock = MagicMock()
        client_mock.translate_text.return_value.translations = [
            MagicMock(translated_text="ঘোঁৰা")
        ]
        client_mock.romanize_text.return_value.romanizations = [
            MagicMock(romanized_text="")
        ]

        with patch.object(translate_service, "_get_translate_client", return_value=client_mock):
            result = translate_service._translate_and_romanize_sync(
                "horse", "assamese", "test-project"
            )
        assert result["romanized"] == ""

    def test_custom_word_emoji(self):
        client_mock = MagicMock()
        client_mock.translate_text.return_value.translations = [
            MagicMock(translated_text="ఛత్రం")
        ]
        client_mock.romanize_text.return_value.romanizations = [
            MagicMock(romanized_text="chatram")
        ]

        with patch.object(translate_service, "_get_translate_client", return_value=client_mock):
            result = translate_service._translate_and_romanize_sync(
                "umbrella", "telugu", "test-project"
            )
        assert result["emoji"] == "✏️"
        assert result["category"] == "custom"

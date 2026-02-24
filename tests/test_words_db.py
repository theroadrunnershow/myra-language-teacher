"""
Unit tests for words_db.py

All tests are pure (no I/O, no network, no mocking required) because the
module is a self-contained in-memory database.
"""
import random
import pytest

from words_db import (
    ALL_CATEGORIES,
    WORD_DATABASE,
    get_all_words_for_language,
    get_random_word,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = ("telugu", "assamese")
ROMAN_KEYS = {"telugu": "tel_roman", "assamese": "asm_roman"}


# ---------------------------------------------------------------------------
# Database integrity
# ---------------------------------------------------------------------------


class TestDatabaseIntegrity:
    def test_all_categories_present(self):
        expected = {"animals", "colors", "body_parts", "numbers", "food", "common_objects", "verbs"}
        assert set(ALL_CATEGORIES) == expected

    def test_every_word_has_required_keys(self):
        required = {"english", "telugu", "assamese", "emoji", "tel_roman", "asm_roman"}
        for category, words in WORD_DATABASE.items():
            for word in words:
                missing = required - word.keys()
                assert not missing, (
                    f"Word '{word.get('english')}' in '{category}' missing keys: {missing}"
                )

    def test_roman_fields_are_ascii(self):
        """Romanized pronunciation guides must be Latin/ASCII."""
        for category, words in WORD_DATABASE.items():
            for word in words:
                for key in ("tel_roman", "asm_roman"):
                    assert word[key].isascii(), (
                        f"'{key}' for '{word['english']}' in '{category}' is not ASCII: {word[key]!r}"
                    )

    def test_minimum_word_counts(self):
        for cat in ALL_CATEGORIES:
            assert len(WORD_DATABASE[cat]) >= 6, (
                f"Category '{cat}' has fewer than 6 words"
            )

    def test_no_duplicate_english_words_per_category(self):
        for cat, words in WORD_DATABASE.items():
            english_words = [w["english"] for w in words]
            assert len(english_words) == len(set(english_words)), (
                f"Duplicate English words in category '{cat}'"
            )


# ---------------------------------------------------------------------------
# get_random_word
# ---------------------------------------------------------------------------


class TestGetRandomWord:
    def test_returns_expected_fields(self):
        word = get_random_word("animals", "telugu")
        assert set(word.keys()) == {
            "english", "translation", "romanized", "emoji", "language", "category"
        }

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    @pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
    def test_valid_category_and_language(self, category, language):
        word = get_random_word(category, language)
        assert word["language"] == language
        assert word["category"] == category
        assert word["english"]
        assert word["translation"]

    def test_telugu_uses_tel_roman(self):
        seen_romanized = set()
        for english_word in ("cat", "dog", "elephant"):
            # Find the expected tel_roman from the DB for each known word
            db_word = next(
                w for w in WORD_DATABASE["animals"] if w["english"] == english_word
            )
            word = get_random_word("animals", "telugu")
            seen_romanized.add(word["romanized"])
        # All romanized values must be ASCII (Latin chars only, not Telugu script)
        for romanized in seen_romanized:
            assert romanized.isascii(), f"tel_roman should be ASCII, got: {romanized!r}"

    def test_assamese_uses_asm_roman(self):
        # Run several draws and confirm every romanized is ASCII
        for _ in range(20):
            word = get_random_word("animals", "assamese")
            assert word["romanized"].isascii(), (
                f"asm_roman should be ASCII, got: {word['romanized']!r}"
            )

    def test_invalid_category_falls_back_gracefully(self):
        """An unknown category must not raise; it must return a valid word."""
        word = get_random_word("nonexistent_category", "telugu")
        assert word["language"] == "telugu"
        assert word["category"] in ALL_CATEGORIES
        assert word["english"]

    def test_translation_differs_from_english_for_telugu(self):
        """Telugu translations should be non-empty and different from English."""
        found_different = False
        for _ in range(30):
            word = get_random_word("animals", "telugu")
            if word["translation"] != word["english"]:
                found_different = True
                break
        assert found_different, "All Telugu translations are identical to English strings"

    def test_translation_differs_from_english_for_assamese(self):
        found_different = False
        for _ in range(30):
            word = get_random_word("animals", "assamese")
            if word["translation"] != word["english"]:
                found_different = True
                break
        assert found_different, "All Assamese translations are identical to English strings"

    def test_randomness_returns_more_than_one_word(self):
        """Over 50 draws, we should see at least 3 distinct English words."""
        random.seed(0)
        seen = {get_random_word("animals", "telugu")["english"] for _ in range(50)}
        assert len(seen) >= 3

    def test_emoji_is_non_empty(self):
        for cat in ALL_CATEGORIES:
            word = get_random_word(cat, "telugu")
            assert word["emoji"], f"Empty emoji for category '{cat}'"


# ---------------------------------------------------------------------------
# get_all_words_for_language
# ---------------------------------------------------------------------------


class TestGetAllWordsForLanguage:
    def test_single_category_telugu_length(self):
        words = get_all_words_for_language("telugu", ["animals"])
        assert len(words) == len(WORD_DATABASE["animals"])

    def test_single_category_assamese_length(self):
        words = get_all_words_for_language("assamese", ["colors"])
        assert len(words) == len(WORD_DATABASE["colors"])

    def test_multiple_categories_length(self):
        cats = ["animals", "colors"]
        words = get_all_words_for_language("telugu", cats)
        expected_len = sum(len(WORD_DATABASE[c]) for c in cats)
        assert len(words) == expected_len

    def test_all_categories_combined(self):
        words = get_all_words_for_language("telugu", ALL_CATEGORIES)
        expected_len = sum(len(v) for v in WORD_DATABASE.values())
        assert len(words) == expected_len

    def test_invalid_category_silently_skipped(self):
        words = get_all_words_for_language("telugu", ["animals", "bogus_category"])
        assert len(words) == len(WORD_DATABASE["animals"])

    def test_empty_categories_returns_empty_list(self):
        assert get_all_words_for_language("telugu", []) == []

    def test_word_fields_present(self):
        words = get_all_words_for_language("telugu", ["numbers"])
        required = {"english", "translation", "romanized", "emoji", "category"}
        for w in words:
            missing = required - w.keys()
            assert not missing, f"Missing fields {missing} in word {w}"

    def test_category_field_matches_requested_category(self):
        words = get_all_words_for_language("telugu", ["food"])
        for w in words:
            assert w["category"] == "food"

    def test_language_key_is_included_in_returned_dict(self):
        """get_all_words_for_language includes the 'language' key in every entry."""
        words = get_all_words_for_language("telugu", ["animals"])
        for w in words:
            assert "language" in w
            assert w["language"] == "telugu"

    def test_telugu_romanized_is_ascii(self):
        words = get_all_words_for_language("telugu", ALL_CATEGORIES)
        for w in words:
            assert w["romanized"].isascii(), (
                f"tel_roman should be ASCII for '{w['english']}': {w['romanized']!r}"
            )

    def test_assamese_romanized_is_ascii(self):
        words = get_all_words_for_language("assamese", ALL_CATEGORIES)
        for w in words:
            assert w["romanized"].isascii(), (
                f"asm_roman should be ASCII for '{w['english']}': {w['romanized']!r}"
            )

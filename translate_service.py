"""
On-demand translation via Google Cloud Translate v3.

Lookup order:
  1. In-memory cache (keyed by (english_lower, language))
  2. words_db.WORD_DATABASE — case-insensitive English match (no API call)
  3. Google Cloud Translate API — translate + romanize
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory cache: (english_lower, language) -> word dict
_translation_cache: dict = {}

# Language codes for Cloud Translate
_TRANSLATE_LANG_CODES = {
    "telugu":   "te",
    "assamese": "as",
}

# Lazy-initialized client (mirrors Whisper pattern in speech_service.py)
_translate_client = None


def _get_translate_client():
    global _translate_client
    if _translate_client is None:
        from google.cloud import translate_v3  # type: ignore
        _translate_client = translate_v3.TranslationServiceClient()
    return _translate_client


def _lookup_in_db(english_word: str, language: str) -> Optional[dict]:
    """Case-insensitive lookup in WORD_DATABASE. Returns None if not found."""
    from words_db import WORD_DATABASE
    english_lower = english_word.lower().strip()
    roman_key = "tel_roman" if language == "telugu" else "asm_roman"
    for category, words in WORD_DATABASE.items():
        for entry in words:
            if entry["english"].lower() == english_lower:
                return {
                    "english":    entry["english"],
                    "translation": entry.get(language, entry["english"]),
                    "romanized":  entry.get(roman_key, ""),
                    "emoji":      entry.get("emoji", ""),
                    "language":   language,
                    "category":   category,
                }
    return None


def _translate_and_romanize_sync(english_word: str, language: str, project_id: str) -> dict:
    """Blocking Google Translate call — run via run_in_executor."""
    client = _get_translate_client()
    parent = f"projects/{project_id}/locations/global"
    lang_code = _TRANSLATE_LANG_CODES[language]

    # Step 1: translate English → native script
    translate_resp = client.translate_text(
        request={
            "contents": [english_word],
            "source_language_code": "en",
            "target_language_code": lang_code,
            "parent": parent,
        }
    )
    translation = translate_resp.translations[0].translated_text

    # Step 2: romanize native script → phonetic (best-effort; empty string on failure)
    romanized = ""
    try:
        roman_resp = client.romanize_text(
            request={
                "contents": [translation],
                "source_language_code": lang_code,
                "parent": parent,
            }
        )
        romanized = roman_resp.romanizations[0].romanized_text or ""
    except Exception as exc:
        logger.warning(f"romanize_text failed for '{translation}' ({lang_code}): {exc}")

    return {
        "english":    english_word,
        "translation": translation,
        "romanized":  romanized,
        "emoji":      "✏️",
        "language":   language,
        "category":   "custom",
    }


async def translate_word(english_word: str, language: str) -> dict:
    """
    Main entry point. Returns a word-shaped dict compatible with displayWord().

    Lookup order: cache → words_db → Google Translate API.
    """
    cache_key = (english_word.lower().strip(), language)

    if cache_key in _translation_cache:
        logger.info(f"[translate] cache hit: {cache_key}")
        return _translation_cache[cache_key]

    db_result = _lookup_in_db(english_word, language)
    if db_result:
        logger.info(f"[translate] db hit: '{english_word}' → '{db_result['translation']}'")
        _translation_cache[cache_key] = db_result
        return db_result

    project_id = os.environ.get("GCP_PROJECT", "")
    if not project_id:
        raise ValueError("GCP_PROJECT environment variable is not set")

    logger.info(f"[translate] API call: '{english_word}' ({language})")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _translate_and_romanize_sync, english_word, language, project_id
    )
    _translation_cache[cache_key] = result
    logger.info(f"[translate] result: '{english_word}' → '{result['translation']}' (roman='{result['romanized']}')")
    return result

import io
import asyncio
import logging
from functools import lru_cache
from gtts import gTTS

logger = logging.getLogger(__name__)

LANGUAGE_CODES = {
    "telugu": "te",
    "assamese": "as",
    "english": "en",
}


def _generate_tts_sync(text: str, lang_code: str, slow: bool = True) -> bytes:
    """Synchronous TTS generation using gTTS."""
    buffer = io.BytesIO()
    tts = gTTS(text=text, lang=lang_code, slow=slow)
    tts.write_to_fp(buffer)
    buffer.seek(0)
    return buffer.read()


async def generate_tts(text: str, language: str, slow: bool = True) -> bytes:
    """Generate TTS audio bytes for the given text in the specified language."""
    lang_code = LANGUAGE_CODES.get(language, "en")
    loop = asyncio.get_event_loop()
    try:
        audio_bytes = await loop.run_in_executor(
            None, _generate_tts_sync, text, lang_code, slow
        )
        return audio_bytes
    except Exception as e:
        logger.error(f"TTS generation failed for '{text}' in {language}: {e}")
        # Fall back to English if target language fails
        try:
            audio_bytes = await loop.run_in_executor(
                None, _generate_tts_sync, text, "en", slow
            )
            return audio_bytes
        except Exception as e2:
            logger.error(f"Fallback TTS also failed: {e2}")
            raise

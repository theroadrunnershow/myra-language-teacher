import os
import tempfile
import asyncio
import logging
import unicodedata

from pydub import AudioSegment
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Whisper language codes
LANGUAGE_CODES = {
    "telugu": "te",
    "assamese": "as",
    "english": "en",
}

# Lazy-loaded Whisper model
_whisper_model = None
_whisper_model_size = "base"  # "base" is ~140MB; upgrade to "small" for better accuracy


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info(f"Loading Whisper model '{_whisper_model_size}'...")
        _whisper_model = whisper.load_model(_whisper_model_size)
        logger.info("Whisper model loaded.")
    return _whisper_model


def normalize_text(text: str) -> str:
    """Normalize unicode text for comparison."""
    text = unicodedata.normalize("NFC", text)
    text = text.strip().lower()
    # Keep only alphanumeric and spaces (works for Unicode scripts too)
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return text


def calculate_similarity(expected: str, actual: str) -> float:
    """Return fuzzy similarity ratio (0-100) between two strings."""
    exp_norm = normalize_text(expected)
    act_norm = normalize_text(actual)
    if not act_norm:
        return 0.0
    # Use token_sort_ratio for robustness against word order / partial speech
    return fuzz.token_sort_ratio(exp_norm, act_norm)


async def _convert_to_wav(audio_data: bytes, src_format: str = "webm") -> str:
    """Convert audio bytes to a temporary WAV file. Returns the path."""
    loop = asyncio.get_event_loop()

    def _convert():
        with tempfile.NamedTemporaryFile(suffix=f".{src_format}", delete=False) as tmp_in:
            tmp_in.write(audio_data)
            tmp_in_path = tmp_in.name

        tmp_wav_path = tmp_in_path.rsplit(".", 1)[0] + ".wav"
        try:
            audio = AudioSegment.from_file(tmp_in_path)
            # Whisper works best with 16kHz mono
            audio = audio.set_frame_rate(16000).set_channels(1)
            audio.export(tmp_wav_path, format="wav")
        finally:
            os.unlink(tmp_in_path)
        return tmp_wav_path

    return await loop.run_in_executor(None, _convert)


async def recognize_speech(
    audio_data: bytes,
    language: str,
    expected_word: str,
    similarity_threshold: float = 50.0,
) -> dict:
    """
    Transcribe audio using Whisper and compare to the expected word.

    Returns a dict with:
        transcribed     – what Whisper heard
        expected        – the target word
        similarity      – 0-100 score
        is_correct      – bool
        language        – language used
        error           – error message if something went wrong (optional)
    """
    lang_code = LANGUAGE_CODES.get(language, "te")
    wav_path = None

    try:
        wav_path = await _convert_to_wav(audio_data)

        loop = asyncio.get_event_loop()

        def _transcribe():
            model = get_whisper_model()
            result = model.transcribe(wav_path, language=lang_code, fp16=False)
            return result["text"].strip()

        transcribed = await loop.run_in_executor(None, _transcribe)
        similarity = calculate_similarity(expected_word, transcribed)
        is_correct = similarity >= similarity_threshold

        logger.info(
            f"Recognized: '{transcribed}' | Expected: '{expected_word}' | "
            f"Similarity: {similarity:.1f}% | Correct: {is_correct}"
        )

        return {
            "transcribed": transcribed,
            "expected": expected_word,
            "similarity": round(similarity, 1),
            "is_correct": is_correct,
            "language": language,
        }

    except Exception as e:
        logger.error(f"Speech recognition error: {e}")
        return {
            "transcribed": "",
            "expected": expected_word,
            "similarity": 0.0,
            "is_correct": False,
            "language": language,
            "error": str(e),
        }
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)

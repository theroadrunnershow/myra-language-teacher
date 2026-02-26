import os
import tempfile
import asyncio
import logging
import unicodedata

import numpy as np
from pydub import AudioSegment
from rapidfuzz import fuzz
from scipy.signal import butter, sosfilt

logger = logging.getLogger(__name__)

# Whisper language codes
LANGUAGE_CODES = {
    "telugu": "te",
    "assamese": "as",
    "english": "en",
}

# Map browser MIME types → file extensions pydub/ffmpeg understand
MIME_TO_EXT = {
    "audio/webm": "webm",
    "audio/webm;codecs=opus": "webm",
    "audio/webm;codecs=vp8": "webm",
    "audio/ogg": "ogg",
    "audio/ogg;codecs=opus": "ogg",
    "audio/mp4": "mp4",
    "audio/mp4;codecs=mp4a.40.2": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}

# Feature flag: enable spectral noise reduction on recorded audio
NOISE_REDUCTION_ENABLED = False

# Feature flag: use initial_prompt to guide Whisper toward expected word (helps Telugu/Assamese)
INITIAL_PROMPT_ENABLED = True

# Lazy-loaded Whisper model
_whisper_model = None
_whisper_model_size = "tiny"  # upgrade to "base" or "small" for better regional-language accuracy

# Feature flag: apply PyTorch dynamic quantization after model load (~2-3× CPU speedup)
# Quantizes all nn.Linear layers to int8; no accuracy loss on Whisper in practice.
QUANTIZE_MODEL = True


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info(f"Loading Whisper model '{_whisper_model_size}'…")
        model = whisper.load_model(_whisper_model_size)
        if QUANTIZE_MODEL:
            try:
                import torch
                logger.info("Applying PyTorch dynamic quantization (int8 Linear layers)…")
                model = torch.quantization.quantize_dynamic(
                    model,
                    {torch.nn.Linear},
                    dtype=torch.qint8,
                )
                logger.info("Model quantized.")
            except Exception as exc:
                logger.warning(f"Quantization skipped: {exc}")
        _whisper_model = model
        logger.info("Whisper model loaded.")
    return _whisper_model


def normalize_text(text: str) -> str:
    """Normalize unicode text for fuzzy comparison."""
    text = unicodedata.normalize("NFC", text)
    text = text.strip().lower()
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return text


def calculate_similarity(expected: str, actual: str) -> float:
    """Return fuzzy similarity ratio 0–100 between two strings."""
    exp_norm = normalize_text(expected)
    act_norm = normalize_text(actual)
    if not act_norm:
        return 0.0
    return fuzz.token_sort_ratio(exp_norm, act_norm)


def mime_to_ext(mime_type: str) -> str:
    """Convert a browser MIME type string to a file extension."""
    # Normalize: strip parameters like '; codecs=…' for the lookup, but try exact first
    mime_clean = mime_type.strip().lower()
    if mime_clean in MIME_TO_EXT:
        return MIME_TO_EXT[mime_clean]
    # Try base type only (before semicolon)
    base = mime_clean.split(";")[0].strip()
    return MIME_TO_EXT.get(base, "webm")  # default to webm


def _highpass_filter(samples: np.ndarray, sample_rate: int, cutoff_hz: int = 80) -> np.ndarray:
    """Remove low-frequency rumble below cutoff_hz (not speech)."""
    sos = butter(4, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return sosfilt(sos, samples)


def _reduce_noise(audio: AudioSegment) -> AudioSegment:
    """
    Apply spectral noise reduction then a high-pass filter.

    noisereduce estimates the noise floor from the whole clip (stationary=False for music/TV)
    and subtracts it via spectral gating — effective against fans, AC, and
    ambient room noise without distorting speech.
    """
    try:
        import noisereduce as nr

        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        sr = audio.frame_rate

        # 1. Spectral noise reduction (stationary=False: music, TV, varying background)
        cleaned = nr.reduce_noise(
            y=samples,
            sr=sr,
            stationary=False,
            prop_decrease=0.75,   # remove 75% of noise energy; keeps speech natural
            n_fft=1024,
            hop_length=256,
        )

        # 2. High-pass filter – cut everything below 80 Hz (rumble, table vibrations)
        cleaned = _highpass_filter(cleaned, sr, cutoff_hz=80)

        cleaned_int16 = np.clip(cleaned, -32768, 32767).astype(np.int16)
        result = AudioSegment(
            cleaned_int16.tobytes(),
            frame_rate=sr,
            sample_width=2,   # 16-bit
            channels=1,
        )
        logger.info("Noise reduction applied successfully.")
        return result

    except Exception as e:
        logger.warning(f"Noise reduction skipped (will use raw audio): {e}")
        return audio


async def _convert_to_wav(audio_data: bytes, ext: str = "webm") -> str:
    """Save audio_data to a temp file and convert to 16 kHz mono WAV. Returns WAV path."""
    loop = asyncio.get_event_loop()

    def _convert():
        # Write incoming bytes to a temp file with the correct extension
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp_in:
            tmp_in.write(audio_data)
            tmp_in_path = tmp_in.name

        logger.info(f"Audio temp file: {tmp_in_path}  size={len(audio_data)} bytes  ext=.{ext}")

        tmp_wav_path = tmp_in_path.rsplit(".", 1)[0] + ".wav"
        try:
            # Let ffmpeg auto-detect the actual format from file contents
            audio = AudioSegment.from_file(tmp_in_path)
            audio = audio.set_frame_rate(16000).set_channels(1)

            if NOISE_REDUCTION_ENABLED:
                audio = _reduce_noise(audio)

            # Whisper needs at least 1 second of audio; pad with silence if shorter
            # to avoid "reshape tensor of 0 elements" crash on very short recordings
            MIN_DURATION_MS = 1000
            if len(audio) < MIN_DURATION_MS:
                logger.warning(f"Audio too short ({len(audio)}ms), padding to {MIN_DURATION_MS}ms")
                silence = AudioSegment.silent(duration=MIN_DURATION_MS - len(audio), frame_rate=16000)
                audio = audio + silence

            audio.export(tmp_wav_path, format="wav")
            logger.info(f"WAV written: {tmp_wav_path}  duration={len(audio)/1000:.1f}s")
        finally:
            os.unlink(tmp_in_path)

        return tmp_wav_path

    return await loop.run_in_executor(None, _convert)


async def recognize_speech(
    audio_data: bytes,
    language: str,
    expected_word: str,
    romanized: str = "",
    mime_type: str = "audio/webm",
    similarity_threshold: float = 50.0,
) -> dict:
    """
    Transcribe audio with Whisper and compare to the expected word.

    Also tries romanized fallback comparison if native-script similarity is low,
    which handles cases where Whisper outputs Latin transliteration.
    """
    lang_code = LANGUAGE_CODES.get(language, "te")
    ext = mime_to_ext(mime_type)
    wav_path = None

    try:
        wav_path = await _convert_to_wav(audio_data, ext)

        loop = asyncio.get_event_loop()

        def _transcribe():
            model = get_whisper_model()

            # Optional prompt to bias Whisper toward expected word (when flag on)
            kw_native = {"initial_prompt": expected_word} if INITIAL_PROMPT_ENABLED else {}
            kw_roman = {"initial_prompt": (romanized or expected_word).strip()} if INITIAL_PROMPT_ENABLED else {}

            # Pass 1: force target language (native-script output, e.g. Telugu/Assamese glyphs).
            # The base model has sparse Indic training data so this can hallucinate, but it's
            # the best path when Whisper does know the word.
            result_native = model.transcribe(
                wav_path,
                language=lang_code,
                task="transcribe",
                fp16=False,
                **kw_native,
            )
            transcribed_native = result_native["text"].strip()
            logger.info(f"Whisper pass-1 (lang={lang_code}): '{transcribed_native}'")

            # Pass 2: force English — Whisper reliably produces a phonetic / romanized
            # approximation of Indic speech in English mode.  This is what we compare
            # against the `romanized` pronunciation guide.
            result_roman = model.transcribe(
                wav_path,
                language="en",
                task="transcribe",
                fp16=False,
                **kw_roman,
            )
            transcribed_roman = result_roman["text"].strip()
            logger.info(f"Whisper pass-2 (lang=en/phonetic): '{transcribed_roman}'")

            return transcribed_native, transcribed_roman

        transcribed_native, transcribed_roman = await loop.run_in_executor(None, _transcribe)

        # --- Primary comparison: native script (pass-1 output vs. e.g. పడవ) ---
        similarity = calculate_similarity(expected_word, transcribed_native)

        # --- Romanized comparison: phonetic English output (pass-2) vs. romanized guide ---
        roman_similarity = 0.0
        if romanized:
            roman_similarity = calculate_similarity(romanized, transcribed_roman)
            logger.info(
                f"Romanized: expected='{romanized}' heard='{transcribed_roman}' → {roman_similarity:.1f}%"
            )

        # Use pass-2 output as the displayed transcription when it produces a better score
        # (more useful to show "padava" than a hallucinated Telugu glyph sequence)
        transcribed = transcribed_roman if roman_similarity > similarity else transcribed_native

        best_similarity = max(similarity, roman_similarity)
        is_correct = best_similarity >= similarity_threshold

        logger.info(
            f"Expected: '{expected_word}' | Heard: '{transcribed}' | "
            f"Script sim: {similarity:.1f}% | Roman sim: {roman_similarity:.1f}% | "
            f"Best: {best_similarity:.1f}% | Correct: {is_correct}"
        )

        return {
            "transcribed": transcribed,
            "expected": expected_word,
            "similarity": round(best_similarity, 1),
            "script_similarity": round(similarity, 1),
            "roman_similarity": round(roman_similarity, 1),
            "is_correct": is_correct,
            "language": language,
        }

    except Exception as e:
        logger.error(f"Speech recognition error: {e}", exc_info=True)
        return {
            "transcribed": "",
            "expected": expected_word,
            "similarity": 0.0,
            "script_similarity": 0.0,
            "roman_similarity": 0.0,
            "is_correct": False,
            "language": language,
            "error": str(e),
        }
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)

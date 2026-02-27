import os
import tempfile
import asyncio
import logging
import time
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

# Feature flag: skip native-language pass1 entirely (set DISABLE_PASS1=true on GCP Cloud Run
# where pass1 produces hallucinations and takes 27–31s due to single vCPU).
# When disabled, only pass2 (romanized/English) is used for recognition.
DISABLE_PASS1: bool = os.environ.get("DISABLE_PASS1", "").lower() in ("1", "true", "yes")

# Whisper inference optimization flags applied to every transcribe() call.
# These collectively prevent runaway token generation and cut latency 3–5×.
_WHISPER_OPTS: dict = dict(
    beam_size=1,                       # greedy decoding — 3-5× faster than default beam_size=5
    without_timestamps=True,           # skip timestamp generation overhead
    condition_on_previous_text=False,  # prevents hallucination spirals on Indic scripts
    max_new_tokens=50,                 # single words need <10 tokens; hard cap prevents runaway
    vad_filter=True,                   # skip silent segments; reduces effective audio length
)

# Lazy-loaded faster-whisper model (CTranslate2 backend, ~4× faster on CPU)
_whisper_model = None
_whisper_model_size = "tiny"  # upgrade to "base" or "small" for better regional-language accuracy
_whisper_compute_type = "int8"  # int8 quantization for CPU; use "float16" for GPU


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info(
            f"Loading faster-whisper model '{_whisper_model_size}' "
            f"(device=cpu, compute={_whisper_compute_type})…"
        )
        _t0 = time.perf_counter()
        _whisper_model = WhisperModel(
            _whisper_model_size,
            device="cpu",
            compute_type=_whisper_compute_type,
        )
        logger.info(
            "[TIMING] step=whisper_model_load cold_start=true "
            f"duration_ms={1000*(time.perf_counter()-_t0):.1f}"
        )
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
        t_convert_start = time.perf_counter()

        # Write incoming bytes to a temp file with the correct extension
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp_in:
            tmp_in.write(audio_data)
            tmp_in_path = tmp_in.name

        logger.info(f"Audio temp file: {tmp_in_path}  size={len(audio_data)} bytes  ext=.{ext}")

        tmp_wav_path = tmp_in_path.rsplit(".", 1)[0] + ".wav"
        try:
            # Let ffmpeg auto-detect the actual format from file contents
            t0 = time.perf_counter()
            audio = AudioSegment.from_file(tmp_in_path)
            logger.info(
                f"[TIMING] step=audio_decode ext={ext} "
                f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
            )

            t0 = time.perf_counter()
            audio = audio.set_frame_rate(16000).set_channels(1)
            logger.info(
                f"[TIMING] step=audio_resample "
                f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
            )

            if NOISE_REDUCTION_ENABLED:
                t0 = time.perf_counter()
                audio = _reduce_noise(audio)
                logger.info(
                    f"[TIMING] step=noise_reduction "
                    f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
                )

            # Whisper needs at least 1 second of audio; pad with silence if shorter
            # to avoid "reshape tensor of 0 elements" crash on very short recordings
            MIN_DURATION_MS = 1000
            if len(audio) < MIN_DURATION_MS:
                logger.warning(f"Audio too short ({len(audio)}ms), padding to {MIN_DURATION_MS}ms")
                silence = AudioSegment.silent(duration=MIN_DURATION_MS - len(audio), frame_rate=16000)
                audio = audio + silence

            t0 = time.perf_counter()
            audio.export(tmp_wav_path, format="wav")
            logger.info(
                f"[TIMING] step=wav_export audio_duration_ms={len(audio)} "
                f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
            )

            logger.info(
                f"[TIMING] step=total_audio_convert size_bytes={len(audio_data)} "
                f"duration_ms={1000*(time.perf_counter()-t_convert_start):.1f}"
            )
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
    t_recognize_start = time.perf_counter()

    try:
        t0 = time.perf_counter()
        wav_path = await _convert_to_wav(audio_data, ext)
        logger.info(
            f"[TIMING] step=convert_to_wav lang={language} "
            f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
        )

        loop = asyncio.get_event_loop()

        def _transcribe():
            t_model = time.perf_counter()
            model = get_whisper_model()
            # Only emit a warm-model timing line (cold-start is logged inside get_whisper_model)
            logger.info(
                f"[TIMING] step=whisper_model_ready "
                f"duration_ms={1000*(time.perf_counter()-t_model):.1f}"
            )

            # Optional prompt to bias Whisper toward expected word (when flag on)
            kw_native = {"initial_prompt": expected_word} if INITIAL_PROMPT_ENABLED else {}
            kw_roman = {"initial_prompt": (romanized or expected_word).strip()} if INITIAL_PROMPT_ENABLED else {}

            # Pass 2 first: force English → fast, reliable phonetic/romanized output.
            # This is the primary success path (~200ms on CPU).
            t0_p2 = time.perf_counter()
            segments_roman, _ = model.transcribe(
                wav_path,
                language="en",
                task="transcribe",
                **{**_WHISPER_OPTS, **kw_roman},
            )
            transcribed_roman = " ".join(seg.text for seg in segments_roman).strip()
            logger.info(
                f"[TIMING] step=whisper_pass2 lang=en "
                f"result='{transcribed_roman}' "
                f"duration_ms={1000*(time.perf_counter()-t0_p2):.1f}"
            )

            # Short-circuit: if romanized match already meets threshold, skip native pass.
            # Saves ~6s on the happy path (pass1 was ~6262ms, pass2 is ~198ms).
            roman_sim_early = calculate_similarity(romanized, transcribed_roman) if romanized else 0.0
            if DISABLE_PASS1 or roman_sim_early >= similarity_threshold:
                logger.info(
                    f"[TIMING] step=whisper_pass1 lang={lang_code} result='' "
                    f"duration_ms=0 skipped=true"
                )
                return "", transcribed_roman

            # Pass 1 fallback: force target language (native-script output).
            # Only runs when pass-2 romanized score is below threshold (~5% of requests).
            t0_p1 = time.perf_counter()
            segments_native, _ = model.transcribe(
                wav_path,
                language=lang_code,
                task="transcribe",
                **{**_WHISPER_OPTS, **kw_native},
            )
            transcribed_native = " ".join(seg.text for seg in segments_native).strip()
            logger.info(
                f"[TIMING] step=whisper_pass1 lang={lang_code} "
                f"result='{transcribed_native}' "
                f"duration_ms={1000*(time.perf_counter()-t0_p1):.1f}"
            )

            return transcribed_native, transcribed_roman

        t0 = time.perf_counter()
        transcribed_native, transcribed_roman = await loop.run_in_executor(None, _transcribe)
        logger.info(
            f"[TIMING] step=total_transcribe lang={language} "
            f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
        )

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
        logger.info(
            f"[TIMING] step=total_recognize lang={language} correct={is_correct} "
            f"best_sim={best_similarity:.1f} "
            f"duration_ms={1000*(time.perf_counter()-t_recognize_start):.1f}"
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

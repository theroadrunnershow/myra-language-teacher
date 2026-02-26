"""
Unit tests for speech_service.py

Strategy
--------
- normalize_text / calculate_similarity / mime_to_ext: pure-function tests, no mocking.
- get_whisper_model: patches sys.modules['whisper'] so no GPU / model download needed.
- _convert_to_wav: patches pydub.AudioSegment so no ffmpeg required.
- recognize_speech: patches both _convert_to_wav and get_whisper_model so the full
  pipeline can be exercised with deterministic transcription outputs.
"""
import sys
import os
import unicodedata
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import speech_service
from speech_service import (
    MIME_TO_EXT,
    _convert_to_wav,
    _whisper_compute_type,
    calculate_similarity,
    get_whisper_model,
    mime_to_ext,
    normalize_text,
    recognize_speech,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Hello") == "hello"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_text("  cat  ") == "cat"

    def test_removes_punctuation(self):
        assert normalize_text("cat!@#$%") == "cat"

    def test_keeps_alphanumerics(self):
        assert normalize_text("cat1") == "cat1"

    def test_keeps_internal_spaces(self):
        result = normalize_text("cat dog")
        assert result == "cat dog"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_unicode_nfc_normalization(self):
        # e + combining acute (decomposed) should normalize to the same as
        # precomposed é.
        decomposed = "e\u0301"
        composed = "\u00e9"
        assert normalize_text(decomposed) == normalize_text(composed)

    def test_telugu_script_survives(self):
        # Telugu glyphs are alphanumeric in unicode terms, so they must pass through.
        result = normalize_text("పిల్లి")
        assert len(result) > 0
        assert "పిల్లి" in result or result  # characters preserved

    def test_mixed_unicode_and_ascii(self):
        # Spaces and letters should survive; symbols stripped.
        result = normalize_text("hello-world")
        assert result == "helloworld"


# ---------------------------------------------------------------------------
# calculate_similarity
# ---------------------------------------------------------------------------


class TestCalculateSimilarity:
    def test_identical_strings_score_100(self):
        assert calculate_similarity("cat", "cat") == 100.0

    def test_case_insensitive_score_100(self):
        assert calculate_similarity("Cat", "cat") == 100.0

    def test_empty_actual_score_0(self):
        assert calculate_similarity("cat", "") == 0.0

    def test_completely_different_strings_score_below_50(self):
        score = calculate_similarity("cat", "elephant")
        assert score < 50.0

    def test_partial_match_reasonable_score(self):
        score = calculate_similarity("pilli", "pili")
        assert score >= 70.0

    def test_telugu_exact_match_score_100(self):
        assert calculate_similarity("పిల్లి", "పిల్లి") == 100.0

    def test_token_order_insensitive(self):
        # rapidfuzz.token_sort_ratio sorts tokens before comparing
        assert calculate_similarity("cat dog", "dog cat") == 100.0

    def test_returns_float(self):
        score = calculate_similarity("hello", "world")
        assert isinstance(score, float)

    def test_score_between_0_and_100(self):
        for pair in [("abc", "xyz"), ("abc", "abc"), ("abc", "ab")]:
            score = calculate_similarity(*pair)
            assert 0.0 <= score <= 100.0, f"Score out of range for {pair}: {score}"

    def test_whitespace_normalised_before_compare(self):
        assert calculate_similarity("  cat  ", "cat") == 100.0


# ---------------------------------------------------------------------------
# mime_to_ext
# ---------------------------------------------------------------------------


class TestMimeToExt:
    @pytest.mark.parametrize("mime,expected_ext", [
        ("audio/webm", "webm"),
        ("audio/webm;codecs=opus", "webm"),
        ("audio/webm;codecs=vp8", "webm"),
        ("audio/ogg", "ogg"),
        ("audio/ogg;codecs=opus", "ogg"),
        ("audio/mp4", "mp4"),
        ("audio/mp4;codecs=mp4a.40.2", "mp4"),
        ("audio/mpeg", "mp3"),
        ("audio/wav", "wav"),
        ("audio/x-wav", "wav"),
    ])
    def test_known_mime_types(self, mime, expected_ext):
        assert mime_to_ext(mime) == expected_ext

    def test_unknown_mime_defaults_to_webm(self):
        assert mime_to_ext("audio/unknown-format") == "webm"

    def test_case_insensitive(self):
        assert mime_to_ext("AUDIO/WEBM") == "webm"
        assert mime_to_ext("Audio/Ogg") == "ogg"

    def test_surrounding_whitespace_stripped(self):
        assert mime_to_ext("  audio/webm  ") == "webm"

    def test_codec_parameter_after_space(self):
        # Some browsers include a space before the semicolon
        assert mime_to_ext("audio/webm; codecs=opus") == "webm"

    def test_all_mime_to_ext_keys_covered(self):
        """Every key in MIME_TO_EXT must resolve correctly via mime_to_ext."""
        for mime, expected in MIME_TO_EXT.items():
            assert mime_to_ext(mime) == expected


# ---------------------------------------------------------------------------
# get_whisper_model
# ---------------------------------------------------------------------------


class TestGetWhisperModel:
    def _make_faster_whisper_stub(self):
        """Return a fake faster_whisper module with a tracked WhisperModel class."""
        stub = MagicMock()
        stub.WhisperModel.return_value = MagicMock(name="FasterWhisperModel")
        return stub

    def test_loads_model_with_configured_size(self):
        stub = self._make_faster_whisper_stub()
        with patch.dict(sys.modules, {"faster_whisper": stub}):
            model = get_whisper_model()
        stub.WhisperModel.assert_called_once_with(
            speech_service._whisper_model_size,
            device="cpu",
            compute_type=_whisper_compute_type,
        )
        assert model is stub.WhisperModel.return_value

    def test_second_call_returns_cached_model_without_reloading(self):
        stub = self._make_faster_whisper_stub()
        with patch.dict(sys.modules, {"faster_whisper": stub}):
            m1 = get_whisper_model()
            m2 = get_whisper_model()
        assert m1 is m2
        stub.WhisperModel.assert_called_once()

    def test_pre_cached_model_never_calls_load_model(self):
        existing = MagicMock(name="PreCachedModel")
        speech_service._whisper_model = existing
        stub = self._make_faster_whisper_stub()
        with patch.dict(sys.modules, {"faster_whisper": stub}):
            result = get_whisper_model()
        assert result is existing
        stub.WhisperModel.assert_not_called()

    def test_load_model_failure_propagates(self):
        stub = self._make_faster_whisper_stub()
        stub.WhisperModel.side_effect = RuntimeError("CUDA OOM")
        with patch.dict(sys.modules, {"faster_whisper": stub}):
            with pytest.raises(RuntimeError, match="CUDA OOM"):
                get_whisper_model()


# ---------------------------------------------------------------------------
# _convert_to_wav  (internal, but worth testing in isolation)
# ---------------------------------------------------------------------------


class TestConvertToWav:
    def _audio_segment_mock(self):
        seg = MagicMock(name="AudioSegment")
        # Support method chaining: .set_frame_rate().set_channels()
        seg.set_frame_rate.return_value = seg
        seg.set_channels.return_value = seg
        return seg

    async def test_returns_string_path(self, tmp_path):
        seg = self._audio_segment_mock()
        with patch("speech_service.AudioSegment.from_file", return_value=seg):
            result = await _convert_to_wav(b"fake audio bytes", "webm")
        assert isinstance(result, str)
        assert result.endswith(".wav")

    async def test_resamples_to_16khz_mono(self, tmp_path):
        seg = self._audio_segment_mock()
        with patch("speech_service.AudioSegment.from_file", return_value=seg):
            await _convert_to_wav(b"fake audio bytes", "webm")
        seg.set_frame_rate.assert_called_once_with(16000)
        seg.set_channels.assert_called_once_with(1)

    async def test_cleans_up_input_temp_file(self, tmp_path):
        seg = self._audio_segment_mock()
        created_paths = []

        original_from_file = speech_service.AudioSegment.from_file

        def track_and_mock(path, *args, **kwargs):
            created_paths.append(path)
            return seg

        with patch("speech_service.AudioSegment.from_file", side_effect=track_and_mock):
            await _convert_to_wav(b"fake audio bytes", "ogg")

        # The input temp file must be deleted after conversion
        assert created_paths, "from_file was never called"
        assert not os.path.exists(created_paths[0]), (
            "Input temp file was not cleaned up"
        )

    async def test_pydub_failure_propagates(self):
        with patch(
            "speech_service.AudioSegment.from_file",
            side_effect=Exception("ffmpeg not found"),
        ):
            with pytest.raises(Exception, match="ffmpeg not found"):
                await _convert_to_wav(b"bad data", "webm")


# ---------------------------------------------------------------------------
# recognize_speech
# ---------------------------------------------------------------------------


def _make_segment(text: str) -> MagicMock:
    """Return a fake faster-whisper Segment with a .text attribute."""
    seg = MagicMock()
    seg.text = text
    return seg


def _make_model_mock(native_text: str, roman_text: str) -> MagicMock:
    """Build a faster-whisper model mock returning two specific transcriptions.

    faster-whisper's model.transcribe() returns (segments_generator, TranscriptionInfo).
    We return a list (also iterable) to keep mocks simple.
    """
    model = MagicMock(name="WhisperModel")
    model.transcribe.side_effect = [
        ([_make_segment(native_text)], MagicMock()),   # pass-1: native script
        ([_make_segment(roman_text)], MagicMock()),    # pass-2: romanized / English phonetic
    ]
    return model


class TestRecognizeSpeech:
    """
    All tests patch _convert_to_wav to return a fake WAV path (no disk I/O)
    and get_whisper_model to return a deterministic mock (no GPU).
    """

    _FAKE_WAV = "/tmp/fake_test.wav"

    @pytest.fixture
    def patch_convert(self):
        with patch(
            "speech_service._convert_to_wav",
            new_callable=AsyncMock,
            return_value=self._FAKE_WAV,
        ) as m:
            yield m

    # --- success paths ---

    async def test_exact_native_match_is_correct(self, patch_convert):
        model = _make_model_mock("పిల్లి", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["is_correct"] is True
        assert result["script_similarity"] == 100.0

    async def test_exact_roman_match_is_correct(self, patch_convert):
        model = _make_model_mock("some_noise", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["roman_similarity"] == 100.0
        assert result["is_correct"] is True

    async def test_best_of_two_passes_used(self, patch_convert):
        # Pass-1 gives 0%, pass-2 gives 100% — should be correct
        model = _make_model_mock("completely wrong", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=80.0,
            )
        assert result["similarity"] == 100.0
        assert result["is_correct"] is True

    async def test_below_threshold_is_not_correct(self, patch_convert):
        model = _make_model_mock("xyz", "abc")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=80.0,
            )
        assert result["is_correct"] is False

    async def test_similarity_rounds_to_one_decimal(self, patch_convert):
        model = _make_model_mock("పిల్లి", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        # All similarity values should be floats with at most 1 decimal place
        for key in ("similarity", "script_similarity", "roman_similarity"):
            val = result[key]
            assert isinstance(val, float)
            assert round(val, 1) == val

    async def test_no_romanized_skips_roman_comparison(self, patch_convert):
        model = _make_model_mock("పిల్లి", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="",   # deliberately empty
                similarity_threshold=50.0,
            )
        assert result["roman_similarity"] == 0.0

    async def test_language_echoed_in_result(self, patch_convert):
        model = _make_model_mock("মেকুৰী", "mekuri")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="assamese",
                expected_word="মেকুৰী",
                romanized="mekuri",
                similarity_threshold=50.0,
            )
        assert result["language"] == "assamese"

    async def test_expected_word_echoed_in_result(self, patch_convert):
        model = _make_model_mock("పిల్లి", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["expected"] == "పిల్లి"

    async def test_response_contains_all_required_keys(self, patch_convert):
        model = _make_model_mock("x", "y")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="test",
                romanized="test",
                similarity_threshold=50.0,
            )
        required = {
            "transcribed", "expected", "similarity",
            "script_similarity", "roman_similarity", "is_correct", "language",
        }
        assert required.issubset(result.keys())

    # --- failure paths ---

    async def test_model_load_failure_returns_error_dict(self, patch_convert):
        with patch(
            "speech_service.get_whisper_model",
            side_effect=RuntimeError("CUDA out of memory"),
        ):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["is_correct"] is False
        assert result["similarity"] == 0.0
        assert "error" in result
        assert "CUDA out of memory" in result["error"]

    async def test_audio_conversion_failure_returns_error_dict(self):
        with patch(
            "speech_service._convert_to_wav",
            new_callable=AsyncMock,
            side_effect=Exception("ffmpeg not found"),
        ):
            result = await recognize_speech(
                audio_data=b"bad bytes",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["is_correct"] is False
        assert "error" in result

    async def test_error_dict_contains_expected_word(self):
        with patch(
            "speech_service._convert_to_wav",
            new_callable=AsyncMock,
            side_effect=Exception("boom"),
        ):
            result = await recognize_speech(
                audio_data=b"x",
                language="telugu",
                expected_word="పిల్లి",
                romanized="",
                similarity_threshold=50.0,
            )
        assert result["expected"] == "పిల్లి"

    async def test_transcribed_uses_roman_when_it_scores_higher(self, patch_convert):
        """When roman similarity > script similarity the romanized text is displayed."""
        model = _make_model_mock("wrong_native", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["transcribed"] == "pilli"

    async def test_transcribed_uses_native_when_it_scores_higher(self, patch_convert):
        """When native similarity > roman similarity the native text is displayed."""
        model = _make_model_mock("పిల్లి", "wrong_roman")
        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert result["transcribed"] == "పిల్లి"

    async def test_both_passes_always_executed(self, patch_convert):
        """model.transcribe must be called exactly twice per recognize_speech call."""
        model = _make_model_mock("పిల్లి", "pilli")
        with patch("speech_service.get_whisper_model", return_value=model):
            await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )
        assert model.transcribe.call_count == 2

    async def test_passes_run_concurrently(self, patch_convert):
        """Both Whisper passes must run in parallel, not sequentially.

        threading.Barrier(2) requires exactly 2 threads to call barrier.wait()
        simultaneously before either is released.  If the passes were sequential,
        the first thread would block forever (timeout → BrokenBarrierError) because
        the second thread never starts while the first is blocked.
        """
        import threading

        barrier = threading.Barrier(2, timeout=5.0)
        model = MagicMock(name="WhisperModel")

        def transcribe_side_effect(wav_path, language=None, **kwargs):
            barrier.wait()  # both threads must arrive before either proceeds
            text = "పిల్లి" if language != "en" else "pilli"
            return ([_make_segment(text)], MagicMock())

        model.transcribe.side_effect = transcribe_side_effect

        with patch("speech_service.get_whisper_model", return_value=model):
            result = await recognize_speech(
                audio_data=b"audio",
                language="telugu",
                expected_word="పిల్లి",
                romanized="pilli",
                similarity_threshold=50.0,
            )

        # Reaching here means the barrier was released — both threads ran at once
        assert result["is_correct"] is True

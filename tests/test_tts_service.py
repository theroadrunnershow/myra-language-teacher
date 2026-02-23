"""
Unit tests for tts_service.py

Strategy
--------
- Patch `tts_service.gTTS` so no real network call is made.
- _generate_tts_sync: synchronous function, tested directly.
- generate_tts: async wrapper, tested with pytest-asyncio.
"""
import io
from unittest.mock import MagicMock, call, patch

import pytest

from tts_service import LANGUAGE_CODES, _generate_tts_sync, generate_tts


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

# A few bytes that look like an MP3 header (deterministic fake audio)
FAKE_MP3_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" + b"\x00" * 64


def _make_gtts_mock(data: bytes = FAKE_MP3_BYTES) -> MagicMock:
    """
    Build a gTTS instance mock whose write_to_fp writes *data* into the
    provided file-like object, mirroring real gTTS behaviour.
    """
    mock_instance = MagicMock(name="gTTSInstance")

    def _write(fp: io.IOBase) -> None:
        fp.write(data)

    mock_instance.write_to_fp.side_effect = _write
    return mock_instance


# ---------------------------------------------------------------------------
# LANGUAGE_CODES constant
# ---------------------------------------------------------------------------


class TestLanguageCodes:
    def test_telugu_maps_to_te(self):
        assert LANGUAGE_CODES["telugu"] == "te"

    def test_assamese_maps_to_as(self):
        assert LANGUAGE_CODES["assamese"] == "as"

    def test_english_maps_to_en(self):
        assert LANGUAGE_CODES["english"] == "en"

    def test_no_extra_unexpected_keys(self):
        assert set(LANGUAGE_CODES.keys()) == {"telugu", "assamese", "english"}


# ---------------------------------------------------------------------------
# _generate_tts_sync
# ---------------------------------------------------------------------------


class TestGenerateTtsSync:
    def test_returns_bytes_object(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()):
            result = _generate_tts_sync("hello", "en")
        assert isinstance(result, bytes)

    def test_returned_bytes_match_written_data(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock(FAKE_MP3_BYTES)):
            result = _generate_tts_sync("hello", "en")
        assert result == FAKE_MP3_BYTES

    def test_constructs_gtts_with_correct_args(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            _generate_tts_sync("cat", "te")
        mock_cls.assert_called_once_with(text="cat", lang="te", slow=True)

    def test_slow_false_passed_through(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            _generate_tts_sync("dog", "en", slow=False)
        mock_cls.assert_called_once_with(text="dog", lang="en", slow=False)

    def test_empty_text_accepted(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock(b"")):
            result = _generate_tts_sync("", "en")
        assert result == b""

    def test_gtts_write_error_propagates(self):
        mock_inst = MagicMock(name="gTTSBroken")
        mock_inst.write_to_fp.side_effect = IOError("write failed")
        with patch("tts_service.gTTS", return_value=mock_inst):
            with pytest.raises(IOError, match="write failed"):
                _generate_tts_sync("hello", "en")

    def test_gtts_construction_error_propagates(self):
        with patch("tts_service.gTTS", side_effect=Exception("network error")):
            with pytest.raises(Exception, match="network error"):
                _generate_tts_sync("hello", "en")

    def test_non_ascii_text_forwarded(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            _generate_tts_sync("పిల్లి", "te")
        mock_cls.assert_called_once_with(text="పిల్లి", lang="te", slow=True)


# ---------------------------------------------------------------------------
# generate_tts  (async)
# ---------------------------------------------------------------------------


class TestGenerateTts:
    async def test_telugu_language_maps_to_te(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            result = await generate_tts("పిల్లి", "telugu")
        mock_cls.assert_called_once_with(text="పిల్లి", lang="te", slow=True)
        assert isinstance(result, bytes)

    async def test_assamese_language_maps_to_as(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            await generate_tts("মেকুৰী", "assamese")
        mock_cls.assert_called_once_with(text="মেকুৰী", lang="as", slow=True)

    async def test_english_language_maps_to_en(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            await generate_tts("cat", "english")
        mock_cls.assert_called_once_with(text="cat", lang="en", slow=True)

    async def test_unknown_language_defaults_to_en(self):
        """Languages not in LANGUAGE_CODES should fall back to 'en'."""
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            await generate_tts("hello", "klingon")
        mock_cls.assert_called_once_with(text="hello", lang="en", slow=True)

    async def test_returns_audio_bytes(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock(FAKE_MP3_BYTES)):
            result = await generate_tts("cat", "english")
        assert result == FAKE_MP3_BYTES

    async def test_slow_parameter_forwarded(self):
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            await generate_tts("cat", "english", slow=False)
        mock_cls.assert_called_once_with(text="cat", lang="en", slow=False)

    async def test_fallback_to_english_when_primary_language_fails(self):
        """
        If the primary language call raises, generate_tts must retry in English
        before propagating the error.
        """
        call_log: list[dict] = []

        def gtts_side_effect(*args, **kwargs):
            call_log.append(kwargs)
            if kwargs.get("lang") == "te":
                raise Exception("Telugu TTS unavailable")
            return _make_gtts_mock()

        with patch("tts_service.gTTS", side_effect=gtts_side_effect):
            result = await generate_tts("పిల్లి", "telugu")

        assert isinstance(result, bytes)
        # First call was Telugu, second call must be English fallback
        assert call_log[0]["lang"] == "te"
        assert call_log[1]["lang"] == "en"

    async def test_raises_when_both_primary_and_fallback_fail(self):
        with patch("tts_service.gTTS", side_effect=Exception("total network failure")):
            with pytest.raises(Exception, match="total network failure"):
                await generate_tts("hello", "telugu")

    async def test_fallback_not_triggered_on_success(self):
        """When primary succeeds, gTTS should only be called once."""
        with patch("tts_service.gTTS", return_value=_make_gtts_mock()) as mock_cls:
            await generate_tts("hello", "telugu")
        assert mock_cls.call_count == 1

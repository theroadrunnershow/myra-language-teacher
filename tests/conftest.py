"""
Shared pytest fixtures and configuration for the myra-language-teacher test suite.
"""
import sys
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Provide a lightweight faster_whisper stub in sys.modules so that
#   import speech_service
# works even when faster-whisper is NOT installed (e.g. lightweight CI image).
# The real faster_whisper import happens lazily inside get_whisper_model(); tests
# that exercise that path patch sys.modules['faster_whisper'] themselves.
# ---------------------------------------------------------------------------
if "faster_whisper" not in sys.modules:
    _faster_whisper_stub = MagicMock()
    sys.modules["faster_whisper"] = _faster_whisper_stub

# ---------------------------------------------------------------------------
# noisereduce is optional; stub it out so importing speech_service never fails
# due to a missing optional dependency.
# ---------------------------------------------------------------------------
if "noisereduce" not in sys.modules:
    sys.modules["noisereduce"] = MagicMock()


import speech_service  # noqa: E402 – imported after stubs are in place


@pytest.fixture(autouse=True)
def _reset_whisper_model_cache():
    """
    Isolate every test from Whisper model state by resetting the module-level
    cache before and after each test.  Prevents a successful load in one test
    from bleeding into tests that expect load_model to be called fresh.
    """
    original = speech_service._whisper_model
    speech_service._whisper_model = None
    yield
    speech_service._whisper_model = original

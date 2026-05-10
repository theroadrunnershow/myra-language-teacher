"""Shared pytest fixtures and configuration for the myra-language-teacher test suite."""
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# face_recognition (dlib) takes ~10 minutes to build from source on first
# install and is robot-only. Stub it in sys.modules so face_service imports
# cleanly on a dev laptop. Tests that exercise the recognition path patch the
# stub's attributes directly.
# ---------------------------------------------------------------------------
if "face_recognition" not in sys.modules:
    sys.modules["face_recognition"] = MagicMock()

"""
Tests 2 & 3: Verify audio bridge functions work against the local server.
Run with: python test_bridge.py
Server must already be running on port 8765.
"""

import io
import sys

import numpy as np
import requests
import scipy.io.wavfile as wavfile
from pydub import AudioSegment

SERVER_URL = "http://localhost:8765"
SAMPLE_RATE = 16000


def test_audio_bridge():
    """Test 2: mic numpy array → WAV → POST /api/recognize"""
    print("\n── Test 2: Audio bridge (mic → WAV → API) ──")

    # Simulate 5s of mic output: shape (N, 2) float32 stereo
    fake_stereo = np.random.uniform(-0.1, 0.1, (SAMPLE_RATE * 5, 2)).astype(np.float32)

    # Same logic as mic_samples_to_wav_bytes in robot_teacher.py
    mono = fake_stereo.mean(axis=1)
    mono = np.clip(mono, -1.0, 1.0)
    pcm16 = (mono * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, pcm16)
    wav_bytes = buf.getvalue()

    print(f"  WAV blob: {len(wav_bytes):,} bytes  (expected ~160 KB)")
    assert len(wav_bytes) > 100_000, "WAV too small"

    r = requests.post(
        f"{SERVER_URL}/api/recognize",
        data={
            "language": "telugu",
            "expected_word": "పిల్లి",
            "romanized": "pilli",
            "audio_format": "audio/wav",
            "similarity_threshold": "0",  # 0 so even silence returns a result
        },
        files={"audio": ("audio.wav", wav_bytes, "audio/wav")},
        timeout=30,
    )
    print(f"  HTTP status: {r.status_code}")
    data = r.json()
    print(f"  Response: {data}")

    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert "is_correct" in data, "Missing is_correct field"
    assert "similarity" in data, "Missing similarity field"
    assert "transcribed" in data, "Missing transcribed field"
    print("  ✓ Test 2 passed\n")


def test_tts_bridge():
    """Test 3: GET /api/tts MP3 → numpy samples for robot speaker"""
    print("── Test 3: TTS bridge (API → MP3 → numpy) ──")

    r = requests.get(
        f"{SERVER_URL}/api/tts",
        params={"text": "పిల్లి", "language": "telugu", "slow": "true"},
        timeout=20,
    )
    print(f"  HTTP status: {r.status_code}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    mp3_bytes = r.content
    print(f"  MP3 size: {len(mp3_bytes):,} bytes")
    assert len(mp3_bytes) > 1000, "MP3 too small — TTS may have failed"

    # Same logic as mp3_bytes_to_robot_samples in robot_teacher.py
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(1)
    raw = np.array(seg.get_array_of_samples(), dtype=np.int16)
    samples = raw.astype(np.float32) / 32767.0
    samples = samples.reshape(-1, 1)

    print(f"  Shape: {samples.shape}   dtype: {samples.dtype}")
    print(f"  Duration: {len(samples) / SAMPLE_RATE:.2f}s")

    assert samples.shape[1] == 1, f"Expected (N, 1), got {samples.shape}"
    assert samples.dtype == np.float32, f"Expected float32, got {samples.dtype}"
    assert len(samples) > SAMPLE_RATE * 0.3, "Audio too short"
    print("  ✓ Test 3 passed\n")


if __name__ == "__main__":
    print(f"Testing against {SERVER_URL}")
    try:
        requests.get(f"{SERVER_URL}/health", timeout=3).raise_for_status()
        print("Server is up.\n")
    except Exception as e:
        print(f"Server not reachable at {SERVER_URL}: {e}")
        print("Start it first: DISABLE_PASS1=true python -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1")
        sys.exit(1)

    try:
        test_audio_bridge()
        test_tts_bridge()
        print("═" * 44)
        print("  All tests passed — audio bridge is working.")
        print("═" * 44)
    except AssertionError as e:
        print(f"\n✗ FAILED: {e}")
        sys.exit(1)

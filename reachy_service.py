"""
Reachy Mini Robot Integration Service
======================================
Handles:
  - SDK connection for arm/head movements (reachy2-sdk via gRPC)
  - SSH connection for audio playback on the robot's speaker (paramiko)
  - Celebration dance (correct word)
  - Sad dance (wrong word)
  - Playing TTS audio through the robot's speakers
"""

import asyncio
import io
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optional dependency guards ────────────────────────────────────────────────
try:
    from reachy2_sdk import ReachySDK  # type: ignore
    REACHY_SDK_AVAILABLE = True
except ImportError:
    REACHY_SDK_AVAILABLE = False
    logger.warning("reachy2-sdk not installed – arm movements will be skipped.")

try:
    import paramiko  # type: ignore
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    logger.warning("paramiko not installed – robot audio output will be skipped.")


# ── Connection state ──────────────────────────────────────────────────────────
_reachy: Optional[object] = None   # ReachySDK instance
_ssh: Optional[object] = None      # paramiko SSHClient
_connected_host: Optional[str] = None


# ── Public API ────────────────────────────────────────────────────────────────

async def connect(host: str, username: str = "bedrock", password: str = "bedrock") -> dict:
    """
    Connect to the Reachy Mini robot.
    Opens two connections:
      • reachy2-sdk  → gRPC port 50051 (arm / head movements)
      • paramiko SSH → port 22 (audio playback)
    Returns a status dict so the caller can surface errors to the UI.
    """
    global _reachy, _ssh, _connected_host

    await disconnect()   # close any stale connections first

    sdk_ok = False
    ssh_ok = False
    errors: list[str] = []
    loop = asyncio.get_event_loop()

    # ── SDK connection ──────────────────────────────────────────────────────
    if REACHY_SDK_AVAILABLE:
        try:
            def _sdk_connect():
                r = ReachySDK(host=host)
                time.sleep(1)   # give gRPC time to handshake
                return r
            _reachy = await loop.run_in_executor(None, _sdk_connect)
            sdk_ok = True
            logger.info("Reachy SDK connected to %s", host)
        except Exception as exc:
            errors.append(f"SDK: {exc}")
            logger.error("Reachy SDK connection failed: %s", exc)

    # ── SSH connection ──────────────────────────────────────────────────────
    if PARAMIKO_AVAILABLE:
        try:
            def _ssh_connect():
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(host, username=username, password=password, timeout=8)
                return client
            _ssh = await loop.run_in_executor(None, _ssh_connect)
            ssh_ok = True
            logger.info("SSH connected to Reachy at %s", host)
        except Exception as exc:
            errors.append(f"SSH: {exc}")
            logger.error("Reachy SSH connection failed: %s", exc)

    if sdk_ok or ssh_ok:
        _connected_host = host

    return {
        "connected": sdk_ok or ssh_ok,
        "sdk_connected": sdk_ok,
        "ssh_connected": ssh_ok,
        "sdk_available": REACHY_SDK_AVAILABLE,
        "ssh_available": PARAMIKO_AVAILABLE,
        "host": host,
        "errors": errors,
    }


async def disconnect():
    """Close all robot connections."""
    global _reachy, _ssh, _connected_host
    loop = asyncio.get_event_loop()

    if _reachy:
        try:
            await loop.run_in_executor(None, _reachy.disconnect)
        except Exception:
            pass
        _reachy = None

    if _ssh:
        try:
            _ssh.close()
        except Exception:
            pass
        _ssh = None

    _connected_host = None


def get_status() -> dict:
    """Return current connection status (no I/O)."""
    return {
        "connected": (_reachy is not None) or (_ssh is not None),
        "sdk_connected": _reachy is not None,
        "ssh_connected": _ssh is not None,
        "sdk_available": REACHY_SDK_AVAILABLE,
        "ssh_available": PARAMIKO_AVAILABLE,
        "host": _connected_host,
    }


# ── Audio playback ────────────────────────────────────────────────────────────

async def play_audio_on_robot(audio_bytes: bytes, mime_type: str = "audio/mpeg") -> bool:
    """
    Transfer audio bytes to the robot via SFTP then play on its speaker.
    Returns True if the command was dispatched successfully.
    """
    if not _ssh:
        logger.warning("Cannot play audio: not connected to robot via SSH")
        return False

    ext = "mp3" if "mpeg" in mime_type else "wav"
    remote_path = f"/tmp/myra_tts.{ext}"
    loop = asyncio.get_event_loop()

    try:
        # Upload via SFTP
        def _upload():
            sftp = _ssh.open_sftp()
            sftp.putfo(io.BytesIO(audio_bytes), remote_path)
            sftp.close()

        await loop.run_in_executor(None, _upload)

        # Choose player (prefer mpg123 for MP3, fall back to aplay)
        if ext == "mp3":
            cmd = f"mpg123 -q {remote_path} 2>/dev/null || ffplay -nodisp -autoexit -loglevel quiet {remote_path}"
        else:
            cmd = f"aplay -q {remote_path}"

        # Run in background so we don't block waiting for playback
        def _play():
            _ssh.exec_command(f"nohup sh -c '{cmd}' >/dev/null 2>&1 &")

        await loop.run_in_executor(None, _play)
        logger.info("Audio dispatched to robot speaker (%d bytes, %s)", len(audio_bytes), ext)
        return True

    except Exception as exc:
        logger.error("Failed to play audio on robot: %s", exc)
        return False


# ── Dances ────────────────────────────────────────────────────────────────────

async def celebration_dance() -> bool:
    """
    Happy celebration dance: both arms raise up, wave side-to-side 3×, return to rest.
    Runs asynchronously so it does not block the web server.
    """
    if not _reachy:
        logger.warning("Cannot perform celebration dance: SDK not connected")
        return False

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_celebration_dance)
        return True
    except Exception as exc:
        logger.error("Celebration dance error: %s", exc)
        return False


def _do_celebration_dance():
    """
    Synchronous celebration choreography.

    Joint order (Reachy 2 / Reachy Mini): 7 DOF per arm
      [shoulder_pitch, shoulder_roll, arm_yaw, elbow_pitch,
       forearm_yaw, wrist_pitch, wrist_roll]   (degrees)

    Starting position: arms by the side (all zeros).
    """
    try:
        # Enable motors
        _reachy.turn_on("r_arm")
        _reachy.turn_on("l_arm")
        time.sleep(0.4)

        # 1. Victory raise – both arms up
        _reachy.r_arm.goto([-30, -15, 0, -100, 0, 0, 0], duration=0.8)
        _reachy.l_arm.goto([-30,  15, 0, -100, 0, 0, 0], duration=0.8)
        time.sleep(0.9)

        # 2. Side-to-side wave (3 repetitions)
        for _ in range(3):
            _reachy.r_arm.goto([-30, -30, 0, -100, 0, 0, 0], duration=0.35)
            _reachy.l_arm.goto([-30,  10, 0, -100, 0, 0, 0], duration=0.35)
            time.sleep(0.4)
            _reachy.r_arm.goto([-30, -10, 0, -100, 0, 0, 0], duration=0.35)
            _reachy.l_arm.goto([-30,  30, 0, -100, 0, 0, 0], duration=0.35)
            time.sleep(0.4)

        # 3. Return to rest
        _reachy.r_arm.goto([0, 0, 0, 0, 0, 0, 0], duration=1.0)
        _reachy.l_arm.goto([0, 0, 0, 0, 0, 0, 0], duration=1.0)
        time.sleep(1.1)

        _reachy.turn_off_smoothly("r_arm")
        _reachy.turn_off_smoothly("l_arm")

    except Exception as exc:
        logger.error("_do_celebration_dance error: %s", exc)
        raise


async def sad_dance() -> bool:
    """
    Sad / disappointed dance: arms droop, slow side-to-side shake, return to rest.
    Runs asynchronously so it does not block the web server.
    """
    if not _reachy:
        logger.warning("Cannot perform sad dance: SDK not connected")
        return False

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_sad_dance)
        return True
    except Exception as exc:
        logger.error("Sad dance error: %s", exc)
        return False


def _do_sad_dance():
    """
    Synchronous sad choreography.
    Arms droop downward then do a slow disappointed sway.
    """
    try:
        _reachy.turn_on("r_arm")
        _reachy.turn_on("l_arm")
        time.sleep(0.4)

        # 1. Arms droop down / inward (slumped posture)
        _reachy.r_arm.goto([20, -5, 0, 30, 0, 0, 0], duration=1.2)
        _reachy.l_arm.goto([20,  5, 0, 30, 0, 0, 0], duration=1.2)
        time.sleep(1.3)

        # 2. Slow disappointed sway (2 repetitions)
        for _ in range(2):
            _reachy.r_arm.goto([20,  5, 0, 30, 0, 0, 0], duration=0.7)
            _reachy.l_arm.goto([20, -5, 0, 30, 0, 0, 0], duration=0.7)
            time.sleep(0.75)
            _reachy.r_arm.goto([20, -5, 0, 30, 0, 0, 0], duration=0.7)
            _reachy.l_arm.goto([20,  5, 0, 30, 0, 0, 0], duration=0.7)
            time.sleep(0.75)

        # 3. Return to rest
        _reachy.r_arm.goto([0, 0, 0, 0, 0, 0, 0], duration=1.2)
        _reachy.l_arm.goto([0, 0, 0, 0, 0, 0, 0], duration=1.2)
        time.sleep(1.3)

        _reachy.turn_off_smoothly("r_arm")
        _reachy.turn_off_smoothly("l_arm")

    except Exception as exc:
        logger.error("_do_sad_dance error: %s", exc)
        raise

"""SSH helpers for managing ``~/.myra/faces.pkl`` on a remote robot.

The Mac app shells out to ``ssh``/``scp`` (key-based auth, BatchMode) and
runs short ``python -`` scripts on the robot that import the project's
``face_service`` module. List + delete are pure pickle ops; enroll SCPs the
photo and runs ``face_service.enroll_from_frame`` remotely.

Every input is validated against shell metacharacters before it touches a
subprocess argv. Names embedded in remote scripts go through ``repr()`` so
quotes and backslashes can't break out of the Python literal.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Patch points for tests — kept module-private so production callers don't
# touch them but ``monkeypatch.setattr(face_admin_ssh, "_subprocess_run", ...)``
# can intercept all transport.
_subprocess_run = subprocess.run

_SSH_TIMEOUT_SEC = 30
_SCP_TIMEOUT_SEC = 60

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_USER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_./~ -]+$")
_PYTHON_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class SshError(Exception):
    """Raised for any SSH transport / parsing failure."""


@dataclass(frozen=True)
class SshConn:
    host: str
    user: str
    repo_path: str
    port: int = 22
    key_path: str | None = None
    python: str = "python3"

    @classmethod
    def parse(cls, raw: object) -> "SshConn":
        if not isinstance(raw, dict):
            raise SshError("connection must be a JSON object")
        host = str(raw.get("host") or "").strip()
        user = str(raw.get("user") or "").strip()
        repo_path = str(raw.get("repo_path") or "").strip()
        port_raw = raw.get("port", 22)
        key_path_raw = str(raw.get("key_path") or "").strip()
        python = str(raw.get("python") or "python3").strip()

        if not host or not _HOST_RE.fullmatch(host):
            raise SshError("invalid host")
        if not user or not _USER_RE.fullmatch(user):
            raise SshError("invalid user")
        if not repo_path or not _PATH_RE.fullmatch(repo_path):
            raise SshError("invalid repo_path")
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise SshError("port must be an integer") from None
        if not (1 <= port <= 65535):
            raise SshError("port out of range")
        key_path = key_path_raw or None
        if key_path and not _PATH_RE.fullmatch(key_path):
            raise SshError("invalid key_path")
        if not _PYTHON_RE.fullmatch(python):
            raise SshError("invalid python interpreter")
        return cls(
            host=host,
            user=user,
            repo_path=repo_path,
            port=port,
            key_path=key_path,
            python=python,
        )


def _ssh_argv(conn: SshConn) -> list[str]:
    args = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-p", str(conn.port),
    ]
    if conn.key_path:
        args += ["-i", os.path.expanduser(conn.key_path)]
    args.append(f"{conn.user}@{conn.host}")
    return args


def _scp_argv(conn: SshConn, src: str, dst: str) -> list[str]:
    args = [
        "scp",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-P", str(conn.port),
    ]
    if conn.key_path:
        args += ["-i", os.path.expanduser(conn.key_path)]
    args += [src, f"{conn.user}@{conn.host}:{dst}"]
    return args


def _run_remote_python(conn: SshConn, script: str) -> str:
    # Prefer the project venv's interpreter on the robot — the kids-teacher
    # deps (numpy, face_recognition) live there. Fall back to the
    # user-specified ``python`` only if ``venv/bin/python`` is absent.
    remote = (
        f"cd {shlex.quote(conn.repo_path)} && "
        f"if [ -x venv/bin/python ]; then venv/bin/python -; "
        f"else {shlex.quote(conn.python)} -; fi"
    )
    cmd = _ssh_argv(conn) + [remote]
    try:
        result = _subprocess_run(
            cmd,
            input=script,
            text=True,
            capture_output=True,
            timeout=_SSH_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise SshError("ssh timed out") from None
    except FileNotFoundError:
        raise SshError("ssh executable not found on local host") from None
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else "no stderr"
        raise SshError(f"ssh failed (rc={result.returncode}): {msg}")
    return result.stdout


_LIST_SCRIPT = """\
import json, sys
sys.path.insert(0, "src")
import face_service
enc = face_service.load_encodings()
print(json.dumps([{"name": n, "count": len(v)} for n, v in sorted(enc.items())]))
"""

_DELETE_SCRIPT_TPL = """\
import json, sys
sys.path.insert(0, "src")
import face_service
names = {names_repr}
enc = face_service.load_encodings()
deleted, not_found = [], []
for name in names:
    if name in enc:
        del enc[name]
        deleted.append(name)
    else:
        not_found.append(name)
if deleted:
    face_service.save_encodings(enc)
print(json.dumps({{"deleted": deleted, "not_found": not_found}}))
"""

_ENROLL_SCRIPT_TPL = """\
import json, os, sys
sys.path.insert(0, "src")
import face_service
photo = {photo_repr}
name = {name_repr}
try:
    if not face_service.HAS_FACE_REC:
        print(json.dumps({{"result": "library_missing"}}))
        sys.exit(0)
    import face_recognition
    try:
        frame = face_recognition.load_image_file(photo)
    except Exception as exc:
        print(json.dumps({{"result": "bad_image", "error": str(exc)}}))
        sys.exit(0)
    res = face_service.enroll_from_frame(name, frame)
    count = len(face_service.load_encodings().get(name, []))
    print(json.dumps({{"result": res.value, "count": count}}))
finally:
    try:
        os.unlink(photo)
    except OSError:
        pass
"""


def remote_list(conn: SshConn) -> list[dict]:
    out = _run_remote_python(conn, _LIST_SCRIPT)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise SshError(f"unexpected list output: {out[:200]!r}") from None
    if not isinstance(data, list):
        raise SshError("list output not a list")
    return data


def remote_delete(conn: SshConn, names: list[str]) -> dict:
    script = _DELETE_SCRIPT_TPL.format(names_repr=repr(list(names)))
    out = _run_remote_python(conn, script)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise SshError(f"unexpected delete output: {out[:200]!r}") from None


def remote_add(
    conn: SshConn, name: str, photo_bytes: bytes, photo_filename: str
) -> dict:
    """SCP photo to /tmp on the robot, run enrollment, clean up.

    Returns a dict with keys ``result`` (one of ``ok|no_face|multiple_faces|
    capacity_exceeded|library_missing|bad_image``), and on success
    ``count``; on ``bad_image`` an ``error`` field with the loader message.
    """
    suffix = Path(photo_filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        suffix = ".jpg"
    fd, local_path = tempfile.mkstemp(prefix="myra-face-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(photo_bytes)
        token = os.urandom(8).hex()
        remote_path = f"/tmp/myra_face_upload_{token}{suffix}"
        scp_cmd = _scp_argv(conn, local_path, remote_path)
        try:
            scp = _subprocess_run(
                scp_cmd, capture_output=True, text=True, timeout=_SCP_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            raise SshError("scp timed out") from None
        except FileNotFoundError:
            raise SshError("scp executable not found on local host") from None
        if scp.returncode != 0:
            tail = (scp.stderr or "").strip().splitlines()
            msg = tail[-1] if tail else "no stderr"
            raise SshError(f"scp failed (rc={scp.returncode}): {msg}")

        script = _ENROLL_SCRIPT_TPL.format(
            photo_repr=repr(remote_path), name_repr=repr(name)
        )
        out = _run_remote_python(conn, script)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise SshError(f"unexpected enroll output: {out[:200]!r}") from None
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass

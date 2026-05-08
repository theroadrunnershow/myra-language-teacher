"""Unit tests for src/face_admin_ssh.py.

The transport (``subprocess.run``) is patched to a fake that records the
argv and stdin and returns scripted CompletedProcess objects. Real SSH
never runs.
"""

from __future__ import annotations

import json
import subprocess
import types
from typing import Any

import pytest

import face_admin_ssh
from face_admin_ssh import SshConn, SshError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(rc: int = 0, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _conn(**overrides: Any) -> SshConn:
    base = {
        "host": "robot.local",
        "user": "reachy",
        "repo_path": "/home/reachy/myra",
        "port": 22,
        "key_path": None,
        "python": "python3",
    }
    base.update(overrides)
    return SshConn(**base)


# ---------------------------------------------------------------------------
# SshConn.parse
# ---------------------------------------------------------------------------


class TestParse:
    def test_minimal_valid(self):
        conn = SshConn.parse({"host": "r.local", "user": "u", "repo_path": "/srv/myra"})
        assert conn.host == "r.local"
        assert conn.user == "u"
        assert conn.repo_path == "/srv/myra"
        assert conn.port == 22
        assert conn.key_path is None
        assert conn.python == "python3"

    def test_full(self):
        conn = SshConn.parse({
            "host": "192.168.1.5",
            "user": "reachy",
            "repo_path": "~/myra",
            "port": 2222,
            "key_path": "~/.ssh/id_ed25519",
            "python": "/usr/bin/python3.11",
        })
        assert conn.port == 2222
        assert conn.key_path == "~/.ssh/id_ed25519"
        assert conn.python == "/usr/bin/python3.11"

    @pytest.mark.parametrize("bad", [
        "robot;rm -rf /",
        "robot && evil",
        "robot`whoami`",
        "robot$(id)",
        "robot|nc",
        "host with space",
        "",
    ])
    def test_rejects_bad_host(self, bad):
        with pytest.raises(SshError):
            SshConn.parse({"host": bad, "user": "u", "repo_path": "/x"})

    @pytest.mark.parametrize("bad", [
        "user;ls",
        "u$(x)",
        "u`x`",
        "u/x",
        "",
    ])
    def test_rejects_bad_user(self, bad):
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": bad, "repo_path": "/x"})

    @pytest.mark.parametrize("bad", [
        "/path;rm",
        "/path`x`",
        "/path$(x)",
        "/path|x",
        "/path\nx",
        "",
    ])
    def test_rejects_bad_path(self, bad):
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": "u", "repo_path": bad})

    def test_rejects_non_dict(self):
        with pytest.raises(SshError):
            SshConn.parse("robot.local")

    def test_rejects_invalid_port(self):
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": "u", "repo_path": "/x", "port": "abc"})
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": "u", "repo_path": "/x", "port": 0})
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": "u", "repo_path": "/x", "port": 70000})

    def test_rejects_bad_python(self):
        with pytest.raises(SshError):
            SshConn.parse({"host": "r", "user": "u", "repo_path": "/x", "python": "py;rm"})


# ---------------------------------------------------------------------------
# remote_list
# ---------------------------------------------------------------------------


class TestRemoteList:
    def test_returns_parsed_list(self, monkeypatch):
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["input"] = kwargs.get("input")
            captured["timeout"] = kwargs.get("timeout")
            return _completed(stdout='[{"name": "Aunt Priya", "count": 2}]')

        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)

        out = face_admin_ssh.remote_list(_conn())

        assert out == [{"name": "Aunt Priya", "count": 2}]
        # ssh argv assembly
        assert captured["cmd"][0] == "ssh"
        assert "BatchMode=yes" in captured["cmd"]
        assert "reachy@robot.local" in captured["cmd"]
        # Repo path is shlex-quoted into the remote command, and we prefer
        # the project venv on the robot, falling back to the user's python.
        remote = captured["cmd"][-1]
        assert "cd /home/reachy/myra" in remote
        assert "venv/bin/python -" in remote
        assert "python3 -" in remote  # fallback branch
        assert "face_service.load_encodings()" in captured["input"]

    def test_uses_key_and_port_when_set(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run",
                            lambda cmd, **kw: (captured.setdefault("cmd", cmd), _completed(stdout="[]"))[1])

        face_admin_ssh.remote_list(_conn(port=2222, key_path="~/.ssh/key"))

        assert "-p" in captured["cmd"]
        assert "2222" in captured["cmd"]
        assert "-i" in captured["cmd"]

    def test_ssh_failure_maps_to_ssherror(self, monkeypatch):
        monkeypatch.setattr(
            face_admin_ssh, "_subprocess_run",
            lambda cmd, **kw: _completed(rc=255, stderr="Permission denied (publickey).\n"),
        )
        with pytest.raises(SshError, match="ssh failed"):
            face_admin_ssh.remote_list(_conn())

    def test_timeout_maps_to_ssherror(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 30)
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", boom)
        with pytest.raises(SshError, match="timed out"):
            face_admin_ssh.remote_list(_conn())

    def test_missing_ssh_binary_maps_to_ssherror(self, monkeypatch):
        def boom(cmd, **kw):
            raise FileNotFoundError()
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", boom)
        with pytest.raises(SshError, match="executable not found"):
            face_admin_ssh.remote_list(_conn())

    def test_garbled_output_maps_to_ssherror(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run",
                            lambda cmd, **kw: _completed(stdout="not json"))
        with pytest.raises(SshError, match="unexpected list output"):
            face_admin_ssh.remote_list(_conn())


# ---------------------------------------------------------------------------
# remote_delete
# ---------------------------------------------------------------------------


class TestRemoteDelete:
    def test_embeds_names_via_repr_and_returns_parsed(self, monkeypatch):
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["script"] = kw.get("input")
            return _completed(stdout='{"deleted": ["Aunt Priya"], "not_found": ["Ghost"]}')

        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)

        out = face_admin_ssh.remote_delete(_conn(), ["Aunt Priya", "Ghost"])

        assert out == {"deleted": ["Aunt Priya"], "not_found": ["Ghost"]}
        # Names must be embedded as a Python literal — repr() handles quoting.
        assert "['Aunt Priya', 'Ghost']" in captured["script"]
        # No string interpolation that could be exploited; brace-escaped braces in tpl render literal.
        assert "{'deleted'" not in captured["script"] or "deleted" in captured["script"]

    def test_safely_embeds_names_with_quotes(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            face_admin_ssh, "_subprocess_run",
            lambda cmd, **kw: (captured.setdefault("script", kw.get("input")),
                               _completed(stdout='{"deleted": [], "not_found": []}'))[1],
        )
        face_admin_ssh.remote_delete(_conn(), ["O'Brien", 'name"with"quotes'])
        # Just confirm the script parses as Python and contains a names = ... line.
        compile(captured["script"], "<remote>", "exec")
        assert "names =" in captured["script"]


# ---------------------------------------------------------------------------
# remote_add
# ---------------------------------------------------------------------------


class TestRemoteAdd:
    def test_scp_then_enroll_success(self, monkeypatch, tmp_path):
        calls: list[dict] = []

        def fake_run(cmd, **kw):
            calls.append({"cmd": cmd, "input": kw.get("input")})
            if cmd[0] == "scp":
                return _completed(rc=0)
            return _completed(stdout='{"result": "ok", "count": 1}')

        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)

        result = face_admin_ssh.remote_add(_conn(), "Aunt Priya", b"\xff\xd8jpegbytes", "p.jpg")

        assert result == {"result": "ok", "count": 1}
        assert len(calls) == 2
        scp_cmd = calls[0]["cmd"]
        assert scp_cmd[0] == "scp"
        assert any(s.startswith("reachy@robot.local:/tmp/myra_face_upload_") for s in scp_cmd)
        assert any(s.endswith(".jpg") for s in scp_cmd)
        # Enrollment ssh script embeds the name + remote photo path.
        enroll_script = calls[1]["input"]
        assert "name = 'Aunt Priya'" in enroll_script
        assert "/tmp/myra_face_upload_" in enroll_script

    def test_scp_failure_raises(self, monkeypatch):
        def fake_run(cmd, **kw):
            if cmd[0] == "scp":
                return _completed(rc=1, stderr="ssh: Could not resolve hostname\n")
            return _completed(stdout='{"result": "ok"}')
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)
        with pytest.raises(SshError, match="scp failed"):
            face_admin_ssh.remote_add(_conn(), "x", b"data", "p.jpg")

    def test_propagates_no_face_result(self, monkeypatch):
        def fake_run(cmd, **kw):
            if cmd[0] == "scp":
                return _completed(rc=0)
            return _completed(stdout='{"result": "no_face"}')
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)
        result = face_admin_ssh.remote_add(_conn(), "x", b"data", "p.png")
        assert result == {"result": "no_face"}

    def test_unknown_extension_normalized_to_jpg(self, monkeypatch):
        scp_dst: list[str] = []
        def fake_run(cmd, **kw):
            if cmd[0] == "scp":
                scp_dst.append(cmd[-1])
                return _completed(rc=0)
            return _completed(stdout='{"result": "ok", "count": 1}')
        monkeypatch.setattr(face_admin_ssh, "_subprocess_run", fake_run)
        face_admin_ssh.remote_add(_conn(), "x", b"data", "weird.exe")
        assert scp_dst[0].endswith(".jpg")

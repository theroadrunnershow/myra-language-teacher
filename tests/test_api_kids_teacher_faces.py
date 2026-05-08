"""Tests for the /api/kids-teacher/faces SSH-driven admin endpoints.

The endpoints shell out to ``face_admin_ssh.remote_list`` /
``remote_delete`` / ``remote_add``; we patch those module-level functions
so the tests are pure unit tests over the request/response shape and the
error mapping. The SSH transport itself is exercised in
``test_face_admin_ssh.py``.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import face_admin_ssh
import kids_teacher_routes
from kids_teacher_routes import router as kids_teacher_router


_VALID_CONN = {
    "host": "robot.local",
    "user": "reachy",
    "repo_path": "/home/reachy/myra",
}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(kids_teacher_router)
    return app


# ---------------------------------------------------------------------------
# POST /faces/list
# ---------------------------------------------------------------------------


class TestList:
    def test_success(self, monkeypatch):
        seen: dict = {}
        def fake(conn):
            seen["conn"] = conn
            return [{"name": "Aunt Priya", "count": 1}]
        monkeypatch.setattr(face_admin_ssh, "remote_list", fake)

        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/list",
                json={"connection": _VALID_CONN},
            )
        assert resp.status_code == 200
        assert resp.json() == {"faces": [{"name": "Aunt Priya", "count": 1}]}
        assert seen["conn"].host == "robot.local"

    def test_invalid_connection_returns_400(self):
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/list",
                json={"connection": {"host": "robot;rm -rf /", "user": "u", "repo_path": "/x"}},
            )
        assert resp.status_code == 400
        assert "Invalid connection" in resp.json()["detail"]

    def test_missing_connection_returns_400(self):
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post("/api/kids-teacher/faces/list", json={})
        assert resp.status_code == 400

    def test_ssh_error_maps_to_502(self, monkeypatch):
        def boom(conn):
            raise face_admin_ssh.SshError("Permission denied")
        monkeypatch.setattr(face_admin_ssh, "remote_list", boom)
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/list",
                json={"connection": _VALID_CONN},
            )
        assert resp.status_code == 502
        assert "SSH error" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /faces/delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_success(self, monkeypatch):
        seen: dict = {}
        def fake(conn, names):
            seen["names"] = names
            return {"deleted": names[:1], "not_found": names[1:]}
        monkeypatch.setattr(face_admin_ssh, "remote_delete", fake)

        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/delete",
                json={"connection": _VALID_CONN, "names": ["Aunt Priya", "Ghost"]},
            )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": ["Aunt Priya"], "not_found": ["Ghost"]}
        assert seen["names"] == ["Aunt Priya", "Ghost"]

    def test_strips_whitespace_and_drops_empty(self, monkeypatch):
        captured: list = []
        monkeypatch.setattr(
            face_admin_ssh, "remote_delete",
            lambda conn, names: (captured.append(names), {"deleted": [], "not_found": []})[1],
        )
        app = _build_app()
        with TestClient(app) as client:
            client.post(
                "/api/kids-teacher/faces/delete",
                json={"connection": _VALID_CONN, "names": ["  Bob  ", "", "  ", "Zara"]},
            )
        assert captured[0] == ["Bob", "Zara"]

    def test_rejects_non_list_names(self):
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/delete",
                json={"connection": _VALID_CONN, "names": "Aunt Priya"},
            )
        assert resp.status_code == 400

    def test_ssh_error_maps_to_502(self, monkeypatch):
        monkeypatch.setattr(
            face_admin_ssh, "remote_delete",
            lambda conn, names: (_ for _ in ()).throw(face_admin_ssh.SshError("boom")),
        )
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/delete",
                json={"connection": _VALID_CONN, "names": ["x"]},
            )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /faces/add
# ---------------------------------------------------------------------------


def _post_add(client, *, name="Aunt Priya", connection=None, photo_bytes=b"\xff\xd8jpeg"):
    if connection is None:
        connection = _VALID_CONN
    return client.post(
        "/api/kids-teacher/faces/add",
        data={"connection": json.dumps(connection), "name": name},
        files={"photo": ("p.jpg", photo_bytes, "image/jpeg")},
    )


class TestAdd:
    def test_success(self, monkeypatch):
        seen: dict = {}
        def fake(conn, name, photo_bytes, photo_filename):
            seen.update(name=name, filename=photo_filename, size=len(photo_bytes))
            return {"result": "ok", "count": 3}
        monkeypatch.setattr(face_admin_ssh, "remote_add", fake)

        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "name": "Aunt Priya", "count": 3}
        assert seen["name"] == "Aunt Priya"
        assert seen["filename"] == "p.jpg"
        assert seen["size"] == len(b"\xff\xd8jpeg")

    def test_no_face_maps_to_422(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add",
                            lambda *a, **kw: {"result": "no_face"})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 422
        assert "no face" in resp.json()["detail"].lower()

    def test_multiple_faces_maps_to_422(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add",
                            lambda *a, **kw: {"result": "multiple_faces"})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 422

    def test_capacity_exceeded_maps_to_409(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add",
                            lambda *a, **kw: {"result": "capacity_exceeded"})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 409

    def test_library_missing_maps_to_503(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add",
                            lambda *a, **kw: {"result": "library_missing"})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 503

    def test_bad_image_maps_to_400(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add",
                            lambda *a, **kw: {"result": "bad_image", "error": "cannot identify"})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 400
        assert "cannot identify" in resp.json()["detail"]

    def test_invalid_connection_json_returns_400(self):
        app = _build_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/kids-teacher/faces/add",
                data={"connection": "not-json", "name": "x"},
                files={"photo": ("p.jpg", b"x", "image/jpeg")},
            )
        assert resp.status_code == 400

    def test_empty_name_returns_400(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add", lambda *a, **kw: {"result": "ok", "count": 0})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client, name="   ")
        assert resp.status_code == 400

    def test_empty_photo_returns_400(self, monkeypatch):
        monkeypatch.setattr(face_admin_ssh, "remote_add", lambda *a, **kw: {"result": "ok", "count": 0})
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client, photo_bytes=b"")
        assert resp.status_code == 400

    def test_ssh_error_maps_to_502(self, monkeypatch):
        def boom(*a, **kw):
            raise face_admin_ssh.SshError("connection refused")
        monkeypatch.setattr(face_admin_ssh, "remote_add", boom)
        app = _build_app()
        with TestClient(app) as client:
            resp = _post_add(client)
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Page route + local-only gate helper
# ---------------------------------------------------------------------------


def test_page_renders():
    app = _build_app()
    with TestClient(app) as client:
        resp = client.get("/kids-teacher/faces")
    assert resp.status_code == 200
    assert "Robot connection" in resp.text
    assert "Delete selected" in resp.text


def test_local_only_helper_blocks_external():
    from kids_teacher_routes import _is_local_request

    class _Req:
        def __init__(self, host: str) -> None:
            self.client = type("C", (), {"host": host})()

    assert _is_local_request(_Req("127.0.0.1"))
    assert _is_local_request(_Req("testclient"))
    assert not _is_local_request(_Req("203.0.113.5"))

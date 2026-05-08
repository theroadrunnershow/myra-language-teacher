"""FastAPI router for kids-teacher configuration, status, and review surfaces.

Pure configuration/status + gated review inspection. NO live realtime
endpoint in V1 — the realtime session runs on the robot, not over HTTP.

The one page route (``GET /kids-teacher``) lives here so all kids-teacher
surfaces are colocated in a single module. ``main.py`` mounts this router
unconditionally; individual endpoints self-gate on env toggles or local-only
access using the same ``_is_local_request`` heuristic as the rest of the app.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

import face_admin_ssh
from env_loader import load_project_dotenv
from kids_review_store import KidsReviewStore
from kids_teacher_backend import resolve_realtime_model
from kids_teacher_profile import DEFAULT_PROFILE_DIR, DEFAULT_VOICE, PROFILE_NAME, load_profile
from kids_teacher_types import KIDS_SUPPORTED_LANGUAGES

load_project_dotenv()

logger = logging.getLogger(__name__)


router = APIRouter(tags=["kids-teacher"])
_api = APIRouter(prefix="/api/kids-teacher", tags=["kids-teacher"])


# Template directory resolves to <repo-root>/templates, same as main.py.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_TEMPLATES = Jinja2Templates(directory=os.path.join(_REPO_ROOT, "templates"))
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_local_request(request: Request) -> bool:
    """Mirror of ``main._is_local_request`` to keep this router self-contained."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", "")
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _get_store(request: Request) -> Optional[KidsReviewStore]:
    return getattr(request.app.state, "kids_review_store", None)


def _get_model() -> str:
    try:
        return resolve_realtime_model()
    except Exception as exc:
        logger.warning("[kids_teacher_routes] resolve_realtime_model failed: %s", exc)
        # Fall back to the documented default so /status does not 500 when
        # the env value is temporarily invalid.
        return "gpt-realtime"


def _default_enabled_languages() -> list[str]:
    raw = os.environ.get("KIDS_ENABLED_LANGUAGES", "").strip()
    if not raw:
        return ["english", "telugu"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    # Filter to supported set; preserve order.
    return [p for p in parts if p in KIDS_SUPPORTED_LANGUAGES] or ["english"]


def _default_explanation_language(enabled: list[str]) -> str:
    value = os.environ.get("KIDS_DEFAULT_EXPLANATION_LANGUAGE", "").strip()
    if value and value in enabled:
        return value
    return enabled[0] if enabled else "english"


def _profile_summary() -> dict:
    """Return profile shape without the full instructions text.

    Instructions can be several KB. Including them in a status endpoint is
    mostly noise. Callers who need them should read the file directly.
    """
    try:
        profile = load_profile(DEFAULT_PROFILE_DIR)
        return {
            "name": profile.name,
            "voice": profile.voice,
            "locked": profile.locked,
            "tool_count": len(profile.allowed_tools),
        }
    except Exception as exc:
        logger.info(
            "[kids_teacher_routes] profile load failed (%s); using defaults", exc
        )
        return {
            "name": PROFILE_NAME,
            "voice": DEFAULT_VOICE,
            "locked": True,
            "tool_count": 0,
        }


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------


@router.get("/kids-teacher")
async def kids_teacher_page(request: Request):
    return _TEMPLATES.TemplateResponse(
        "kids_teacher.html",
        {"request": request},
        headers=_NO_CACHE,
    )


@router.get("/kids-teacher/faces")
async def kids_teacher_faces_page(request: Request):
    return _TEMPLATES.TemplateResponse(
        "kids_teacher_faces.html",
        {"request": request},
        headers=_NO_CACHE,
    )


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


@_api.get("/status")
async def kids_teacher_status(request: Request):
    store = _get_store(request)
    review_block = {
        "transcripts_enabled": bool(store.transcripts_enabled) if store else False,
        "audio_enabled": bool(store.audio_enabled) if store else False,
    }
    enabled_languages = _default_enabled_languages()
    return {
        "mode": "kids_teacher",
        "model": _get_model(),
        "enabled_languages": enabled_languages,
        "default_explanation_language": _default_explanation_language(enabled_languages),
        "review": review_block,
        "profile": _profile_summary(),
    }


# ---------------------------------------------------------------------------
# Review endpoints (local + env-gated)
# ---------------------------------------------------------------------------


def _require_review_available(request: Request) -> KidsReviewStore:
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Kids-teacher review is only available from the local machine.",
        )
    store = _get_store(request)
    if store is None or not store.is_enabled:
        raise HTTPException(
            status_code=404,
            detail="Kids-teacher review is disabled on this deployment.",
        )
    return store


@_api.get("/review/sessions")
async def kids_teacher_review_sessions(request: Request):
    store = _require_review_available(request)
    try:
        sessions = store.list_sessions()
    except Exception as exc:
        logger.warning("[kids_teacher_routes] list_sessions failed: %s", exc)
        sessions = []
    return {"sessions": sessions}


@_api.get("/review/sessions/{session_id}")
async def kids_teacher_review_session(request: Request, session_id: str):
    store = _require_review_available(request)
    data = store.read_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    if not store.transcripts_enabled:
        # Strip transcripts when transcripts are globally disabled, even if
        # a legacy session.json happens to still contain them.
        return JSONResponse(
            {
                "session_id": data.get("session_id", session_id),
                "started_at": data.get("started_at"),
                "ended_at": data.get("ended_at"),
                "transcripts_enabled": False,
                "audio_enabled": bool(store.audio_enabled),
                "audio_files": data.get("audio_files") or [],
            }
        )
    return data


# ---------------------------------------------------------------------------
# Faces admin endpoints (local-only HTTP gate; SSH to the robot for the data)
# ---------------------------------------------------------------------------

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_FACE_NAME_LEN = 100

_ENROLL_RESULT_TO_HTTP = {
    "no_face": (422, "No face detected in image."),
    "multiple_faces": (422, "Multiple faces detected; upload a photo with exactly one face."),
    "capacity_exceeded": (409, "Capacity exceeded; remove an existing name first."),
    "library_missing": (503, "face_recognition library is not installed on the robot."),
}


def _require_local(request: Request) -> None:
    """Gate admin endpoints to the local browser only — SSH creds stay on-host."""
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Faces admin is only available from the local machine.",
        )


def _parse_conn(raw: object) -> face_admin_ssh.SshConn:
    try:
        return face_admin_ssh.SshConn.parse(raw)
    except face_admin_ssh.SshError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid connection: {exc}")


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


@_api.post("/faces/list")
async def list_faces(request: Request):
    _require_local(request)
    body = await _json_body(request)
    conn = _parse_conn(body.get("connection"))
    try:
        faces = face_admin_ssh.remote_list(conn)
    except face_admin_ssh.SshError as exc:
        raise HTTPException(status_code=502, detail=f"SSH error: {exc}")
    return {"faces": faces}


@_api.post("/faces/delete")
async def delete_faces(request: Request):
    _require_local(request)
    body = await _json_body(request)
    conn = _parse_conn(body.get("connection"))
    names = body.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HTTPException(status_code=400, detail="'names' must be a list of strings")
    cleaned = [n.strip() for n in names if n.strip()]
    try:
        result = face_admin_ssh.remote_delete(conn, cleaned)
    except face_admin_ssh.SshError as exc:
        raise HTTPException(status_code=502, detail=f"SSH error: {exc}")
    return result


@_api.post("/faces/add")
async def add_face(
    request: Request,
    connection: str = Form(...),
    name: str = Form(...),
    photo: UploadFile = File(...),
):
    _require_local(request)
    try:
        conn_raw = json.loads(connection)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="connection field must be valid JSON")
    conn = _parse_conn(conn_raw)

    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(name) > MAX_FACE_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"name too long (max {MAX_FACE_NAME_LEN} chars)")

    data = await photo.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty photo upload")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"photo too large (max {MAX_PHOTO_BYTES // (1024 * 1024)} MB)",
        )

    try:
        result = face_admin_ssh.remote_add(conn, name, data, photo.filename or "upload.jpg")
    except face_admin_ssh.SshError as exc:
        raise HTTPException(status_code=502, detail=f"SSH error: {exc}")

    code = result.get("result")
    if code == "ok":
        return {"status": "ok", "name": name, "count": int(result.get("count", 0))}
    if code == "bad_image":
        raise HTTPException(
            status_code=400,
            detail=f"Could not read image: {result.get('error', 'unknown')}",
        )
    mapped = _ENROLL_RESULT_TO_HTTP.get(code)
    if mapped is None:
        raise HTTPException(status_code=502, detail=f"Unknown enroll result: {code!r}")
    status, detail = mapped
    raise HTTPException(status_code=status, detail=detail)


# Expose the combined router so main.py only needs one include_router call.
router.include_router(_api)

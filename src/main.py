from env_loader import load_project_dotenv

load_project_dotenv()

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from kids_review_store import KidsReviewStore
from kids_teacher_routes import router as kids_teacher_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r. Using default=%s", name, raw, default)
        return default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in allowed:
        return value
    logger.warning("Invalid value for %s=%r. Using default=%s", name, raw, default)
    return default


@asynccontextmanager
async def _lifespan(app: FastAPI):
    review_store = KidsReviewStore(
        transcripts_enabled=_env_bool("KIDS_REVIEW_TRANSCRIPTS_ENABLED", False),
        audio_enabled=_env_bool("KIDS_REVIEW_AUDIO_ENABLED", False),
        retention_days=_env_int("KIDS_REVIEW_RETENTION_DAYS", 30),
        local_dir=os.environ.get(
            "KIDS_REVIEW_LOCAL_DIR",
            os.path.join(BASE_DIR, "data", "kids_review.runtime.v1"),
        ).strip(),
        bucket_name=os.environ.get("KIDS_REVIEW_OBJECT_BUCKET", "").strip(),
        object_prefix=os.environ.get(
            "KIDS_REVIEW_OBJECT_PREFIX", "kids_review/v1"
        ).strip(),
        sync_to_gcs_policy=_env_choice(
            "KIDS_REVIEW_SYNC_TO_GCS", "never", {"never", "session_end", "shutdown"}
        ),
    )
    app.state.kids_review_store = review_store

    yield

    review_store = getattr(app.state, "kids_review_store", None)
    if review_store is not None and review_store.is_enabled:
        review_store.flush_if_needed(force=True)
        if review_store.should_sync_on_shutdown:
            review_store.sync_to_object_store(force=True)


app = FastAPI(title="Myra Language Teacher", lifespan=_lifespan)
app.state.kids_review_store = None
app.include_router(kids_teacher_router)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiter (replaces Cloud Armor). Active only when APP_ENV=production."""

    # (path_prefix, requests_per_minute) — first match wins
    _RULES = [
        ("/api/", 100),
    ]

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self._enabled = enabled
        self._windows: dict = defaultdict(list)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)

        client = getattr(request, "client", None)
        ip = getattr(client, "host", "unknown") if client else "unknown"
        path = request.url.path
        now = time.time()
        cutoff = now - 60.0

        for prefix, limit in self._RULES:
            if path.startswith(prefix):
                key = (ip, prefix)
                async with self._lock:
                    ts = self._windows[key]
                    self._windows[key] = [t for t in ts if t > cutoff]
                    if len(self._windows[key]) >= limit:
                        return Response(
                            content='{"detail":"Too many requests"}',
                            status_code=429,
                            media_type="application/json",
                        )
                    self._windows[key].append(now)
                break

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "media-src 'self' blob:; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting — enabled only in production (APP_ENV=production).
# Disabled in dev/test so the test suite is unaffected.
_rate_limit_enabled = os.environ.get("APP_ENV") == "production"
app.add_middleware(RateLimitMiddleware, enabled=_rate_limit_enabled)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/health")
async def health():
    """Lightweight probe endpoint for Cloud Run startup/liveness checks."""
    return {"status": "ok"}


@app.get("/")
async def home():
    return RedirectResponse(url="/kids-teacher", status_code=302)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

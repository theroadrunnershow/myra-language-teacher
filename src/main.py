from env_loader import load_project_dotenv

load_project_dotenv()

import asyncio
import io
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from speech_service import recognize_speech
from dynamic_words_store import DynamicWordsStore
from kids_review_store import KidsReviewStore
from kids_teacher_routes import router as kids_teacher_router
from language_config import SUPPORTED_LESSON_LANGUAGES, VALID_LANGUAGES
from translate_service import set_dynamic_words_store, translate_word
from tts_service import generate_tts
from words_db import ALL_CATEGORIES, WORD_DATABASE, get_random_word

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Security constants ────────────────────────────────────────────────────────
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB — prevents OOM on Cloud Run
MAX_TEXT_LEN = 200                   # characters — prevents gTTS quota abuse
MAX_CONFIG_BODY = 4096               # 4 KB — prevents large JSON body abuse
MAX_TRANSLATE_WORD_LEN = 50          # characters — prevents abuse of /api/translate

@asynccontextmanager
async def _lifespan(app: FastAPI):
    enabled = _env_bool("WORDS_STORE_ENABLED", True)
    store = DynamicWordsStore(
        enabled=enabled,
        local_path=os.environ.get("WORDS_LOCAL_PATH", DEFAULT_WORDS_LOCAL_PATH).strip(),
        bucket_name=os.environ.get("WORDS_OBJECT_BUCKET", "").strip(),
        object_key=os.environ.get("WORDS_OBJECT_KEY", DEFAULT_WORDS_OBJECT_KEY).strip(),
        sync_to_gcs_policy=_env_choice("WORDS_SYNC_TO_GCS", "never", {"never", "session_end", "shutdown"}),
        flush_interval_sec=_env_int("WORDS_FLUSH_INTERVAL_SEC", 21600),
        flush_max_new_words=_env_int("WORDS_FLUSH_MAX_NEW_WORDS", 50),
        refresh_interval_sec=_env_int("WORDS_REFRESH_INTERVAL_SEC", 3600),
    )
    store.load_snapshot()
    app.state.dynamic_words_store = store
    set_dynamic_words_store(store)
    app.state.words_flush_task = asyncio.create_task(_flush_words_loop())

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

    flush_task = getattr(app.state, "words_flush_task", None)
    if flush_task is not None:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass

    store = getattr(app.state, "dynamic_words_store", None)
    if store is not None:
        store.flush_if_needed(force=True)
        if store.should_sync_on_shutdown:
            store.sync_to_object_store(force=True)

    review_store = getattr(app.state, "kids_review_store", None)
    if review_store is not None and review_store.is_enabled:
        review_store.flush_if_needed(force=True)
        if review_store.should_sync_on_shutdown:
            review_store.sync_to_object_store(force=True)


app = FastAPI(title="Myra Language Teacher", lifespan=_lifespan)
app.state.dynamic_words_store = None
app.state.kids_review_store = None
app.include_router(kids_teacher_router)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiter (replaces Cloud Armor). Active only when APP_ENV=production."""

    # (path_prefix, requests_per_minute) — first match wins
    _RULES = [
        ("/api/recognize", 10),
        ("/api/tts", 30),
        ("/api/dino-voice", 30),
        ("/api/translate", 20),
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

# ── Static files & templates ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WORDS_LOCAL_PATH = os.path.join(BASE_DIR, "data", "custom_words.runtime.v1.json")
DEFAULT_WORDS_OBJECT_KEY = "words/custom_words.v1.json"
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ── Configuration (client-side sessionStorage; these are defaults only) ────────
DEFAULT_CONFIG = {
    "languages": list(SUPPORTED_LESSON_LANGUAGES),
    "categories": ALL_CATEGORIES,
    "child_name": "",
    "show_romanized": True,
    "similarity_threshold": 50,  # % match required
    "max_attempts": 3,
    "theme": "pink",
    "mascot": "dino",
}

VALID_THEMES = {"pink", "blue", "green", "purple", "orange", "yellow"}
VALID_MASCOTS = {"dino", "cat", "dog", "panda", "fox", "rabbit"}


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


async def _flush_words_loop() -> None:
    try:
        while True:
            await asyncio.sleep(30)
            store = getattr(app.state, "dynamic_words_store", None)
            if store is not None:
                store.flush_if_needed(force=False)
    except asyncio.CancelledError:
        return


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Lightweight probe endpoint for Cloud Run startup/liveness checks."""
    return {"status": "ok"}


# ── Page routes ───────────────────────────────────────────────────────────────
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


def _is_local_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", "")
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "config": DEFAULT_CONFIG},
        headers=_NO_CACHE,
    )


@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": DEFAULT_CONFIG, "all_categories": ALL_CATEGORIES},
        headers=_NO_CACHE,
    )


# ── API: configuration (returns defaults; client stores in sessionStorage) ─────
@app.get("/api/config")
async def api_get_config():
    return dict(DEFAULT_CONFIG)


@app.post("/api/config")
async def api_save_config(request: Request):
    """Validate config. Client persists to localStorage; no server-side storage."""
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except (ValueError, TypeError):
        content_length = 0
    if content_length > MAX_CONFIG_BODY:
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if "languages" in body and not isinstance(body["languages"], list):
        raise HTTPException(status_code=400, detail="'languages' must be a list")
    if "categories" in body and not isinstance(body["categories"], list):
        raise HTTPException(status_code=400, detail="'categories' must be a list")
    if "theme" in body and body["theme"] not in VALID_THEMES:
        raise HTTPException(status_code=400, detail=f"'theme' must be one of {sorted(VALID_THEMES)}")
    if "mascot" in body and body["mascot"] not in VALID_MASCOTS:
        raise HTTPException(status_code=400, detail=f"'mascot' must be one of {sorted(VALID_MASCOTS)}")
    merged = {**DEFAULT_CONFIG, **body}
    return {"status": "ok", "config": merged}


# ── API: words ────────────────────────────────────────────────────────────────
@app.get("/api/word")
async def api_get_word(
    languages: str = "",  # comma-separated, e.g. "telugu,assamese"
    categories: str = "",  # comma-separated, e.g. "animals,colors"
):
    langs = [s.strip() for s in languages.split(",") if s.strip()] if languages else DEFAULT_CONFIG["languages"]
    cats = [s.strip() for s in categories.split(",") if s.strip()] if categories else DEFAULT_CONFIG["categories"]

    if not langs:
        raise HTTPException(status_code=400, detail="No languages configured. Go to Settings.")
    if not cats:
        raise HTTPException(status_code=400, detail="No categories configured. Go to Settings.")

    language = random.choice(langs)
    category = random.choice(cats)
    word = get_random_word(category, language)
    return word


# ── API: on-demand translation ────────────────────────────────────────────────
@app.get("/api/translate")
async def api_translate(word: str = "", language: str = "telugu"):
    """Translate any English word to a supported lesson language on demand.
    Checks words_db first; falls back to Google Cloud Translate API."""
    word = word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="word parameter is required")
    if len(word) > MAX_TRANSLATE_WORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"word too long (max {MAX_TRANSLATE_WORD_LEN} characters)",
        )
    if language not in SUPPORTED_LESSON_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid language '{language}'. Must be one of "
                f"{', '.join(SUPPORTED_LESSON_LANGUAGES)}."
            ),
        )
    try:
        return await translate_word(word, language)
    except ValueError as exc:
        logger.error(f"Translate config error: {exc}")
        raise HTTPException(status_code=503, detail="Translation service not configured.")
    except Exception as exc:
        logger.error(f"Translate error for '{word}': {exc}")
        raise HTTPException(status_code=503, detail=f"Translation failed: {exc}")


@app.post("/api/internal/words/sync")
async def api_sync_words(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Word sync is only available from the local machine.")

    store = getattr(app.state, "dynamic_words_store", None)
    if store is None or not store.is_configured:
        return {"status": "disabled", "synced": False}

    store.flush_if_needed(force=True)
    synced = store.sync_to_object_store(force=True)
    status = "ok" if synced else "skipped"
    return {
        "status": status,
        "synced": synced,
        "policy": store.sync_to_gcs_policy,
    }


# ── API: TTS ──────────────────────────────────────────────────────────────────
@app.get("/api/tts")
async def api_tts(text: str, language: str = "telugu", slow: bool = False):
    if len(text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"text too long (max {MAX_TEXT_LEN} characters)")
    if language not in VALID_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Invalid language '{language}'")
    t0 = time.perf_counter()
    try:
        audio_bytes = await generate_tts(text, language, slow)
        logger.info(
            f"[TIMING] step=api_tts lang={language} text_len={len(text)} "
            f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
        )
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")


# ── API: Roo dino voice lines (English TTS for character voice) ────────────────
@app.get("/api/dino-voice")
async def api_dino_voice(text: str, slow: bool = False):
    """TTS for Roo's English voice lines. Strips leading/trailing whitespace."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text parameter is required")
    if len(text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"text too long (max {MAX_TEXT_LEN} characters)")
    try:
        audio_bytes = await generate_tts(text.strip(), "english", slow)
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        logger.error(f"Dino voice TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"Dino voice failed: {e}")


# ── API: speech recognition ───────────────────────────────────────────────────
@app.post("/api/recognize")
async def api_recognize(
    audio: UploadFile = File(...),
    language: str = Form(...),
    expected_word: str = Form(...),
    romanized: str = Form(default=""),
    audio_format: str = Form(default="audio/webm"),
    similarity_threshold: str = Form(default="50"),  # from client sessionStorage
):
    if language not in VALID_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Invalid language '{language}'")

    try:
        threshold = float(similarity_threshold)
    except ValueError:
        raise HTTPException(status_code=400, detail="similarity_threshold must be a number")
    if not (0.0 <= threshold <= 100.0):
        raise HTTPException(status_code=400, detail="similarity_threshold must be between 0 and 100")

    t_request_start = time.perf_counter()

    t0 = time.perf_counter()
    audio_data = await audio.read()
    logger.info(
        f"[TIMING] step=audio_upload_read size_bytes={len(audio_data)} "
        f"duration_ms={1000*(time.perf_counter()-t0):.1f}"
    )

    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file received.")
    if len(audio_data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio file too large (max {MAX_AUDIO_BYTES // (1024 * 1024)} MB)",
        )

    logger.info(f"Recognize request: language={language}, expected='{expected_word}', "
                f"mime='{audio_format}', audio_size={len(audio_data)} bytes")

    result = await recognize_speech(
        audio_data=audio_data,
        language=language,
        expected_word=expected_word,
        romanized=romanized,
        mime_type=audio_format,
        similarity_threshold=threshold,
    )
    logger.info(
        f"[TIMING] step=api_recognize lang={language} correct={result.get('is_correct')} "
        f"duration_ms={1000*(time.perf_counter()-t_request_start):.1f}"
    )
    return result


# ── API: word list (for progress tracking) ────────────────────────────────────
@app.get("/api/words/all")
async def api_all_words(
    languages: str = "",
    categories: str = "",
):
    langs = [s.strip() for s in languages.split(",") if s.strip()] if languages else DEFAULT_CONFIG["languages"]
    cats = [s.strip() for s in categories.split(",") if s.strip()] if categories else DEFAULT_CONFIG["categories"]
    result = {}
    for lang in langs:
        result[lang] = []
        for cat in cats:
            if cat in WORD_DATABASE:
                for word in WORD_DATABASE[cat]:
                    result[lang].append({
                        "english": word["english"],
                        "translation": word.get(lang, word["english"]),
                        "emoji": word.get("emoji", ""),
                        "category": cat,
                    })
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

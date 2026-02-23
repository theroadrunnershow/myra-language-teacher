import asyncio
import io
import logging
import os
import random

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from speech_service import recognize_speech
from tts_service import generate_tts
import reachy_service
from words_db import ALL_CATEGORIES, WORD_DATABASE, get_random_word

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Myra Language Teacher")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files & templates ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ── Configuration (client-side sessionStorage; these are defaults only) ────────
DEFAULT_CONFIG = {
    "languages": ["telugu", "assamese"],
    "categories": ALL_CATEGORIES,
    "child_name": "Myra",
    "show_romanized": True,
    "similarity_threshold": 50,  # % match required
    "max_attempts": 3,
    # Reachy Mini robot integration
    "reachy_enabled": False,
    "reachy_host": "reachy.local",
    "reachy_username": "bedrock",
}


# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "config": DEFAULT_CONFIG})


@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": DEFAULT_CONFIG, "all_categories": ALL_CATEGORIES},
    )


# ── API: configuration (returns defaults; client stores in sessionStorage) ─────
@app.get("/api/config")
async def api_get_config():
    return dict(DEFAULT_CONFIG)


@app.post("/api/config")
async def api_save_config(request: Request):
    """Validate config. Client persists to sessionStorage; no server-side storage."""
    body = await request.json()
    if "languages" in body and not isinstance(body["languages"], list):
        raise HTTPException(status_code=400, detail="'languages' must be a list")
    if "categories" in body and not isinstance(body["categories"], list):
        raise HTTPException(status_code=400, detail="'categories' must be a list")
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


# ── API: TTS ──────────────────────────────────────────────────────────────────
@app.get("/api/tts")
async def api_tts(text: str, language: str = "telugu"):
    try:
        audio_bytes = await generate_tts(text, language)
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")


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
    threshold = float(similarity_threshold)

    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file received.")

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


# ── API: Reachy Mini robot ────────────────────────────────────────────────────

class ReachyConnectRequest(dict):
    pass


@app.post("/api/reachy/connect")
async def api_reachy_connect(request: Request):
    """Connect to Reachy Mini robot. Body: {host, username, password}."""
    body = await request.json()
    host = body.get("host", "reachy.local")
    username = body.get("username", "bedrock")
    password = body.get("password", "bedrock")

    if not host:
        raise HTTPException(status_code=400, detail="Robot host/IP is required.")

    status = await reachy_service.connect(host, username=username, password=password)
    return status


@app.post("/api/reachy/disconnect")
async def api_reachy_disconnect():
    """Disconnect from Reachy Mini robot."""
    await reachy_service.disconnect()
    return {"status": "disconnected"}


@app.get("/api/reachy/status")
async def api_reachy_status():
    """Return current robot connection status."""
    return reachy_service.get_status()


@app.post("/api/reachy/tts")
async def api_reachy_tts(request: Request):
    """
    Generate TTS audio and play it through the robot's speaker.
    Body: {text, language}
    Returns: {ok, duration_ms} – frontend uses duration_ms to time its next action.
    """
    body = await request.json()
    text = body.get("text", "")
    language = body.get("language", "english")

    if not text:
        raise HTTPException(status_code=400, detail="'text' is required.")

    try:
        audio_bytes = await generate_tts(text, language)
    except Exception as exc:
        logger.error("TTS generation for Reachy failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}")

    ok = await reachy_service.play_audio_on_robot(audio_bytes, mime_type="audio/mpeg")

    # Estimate playback duration: ~150 bytes/ms for MP3 at 32 kbps (gTTS default)
    estimated_ms = max(1000, int(len(audio_bytes) / 150))

    return {"ok": ok, "duration_ms": estimated_ms, "bytes": len(audio_bytes)}


@app.post("/api/reachy/dance")
async def api_reachy_dance(request: Request):
    """
    Trigger a robot dance.
    Body: {type: "celebrate" | "sad"}
    The dance runs asynchronously (fire-and-forget) so this returns immediately.
    """
    body = await request.json()
    dance_type = body.get("type", "celebrate")

    if dance_type == "celebrate":
        asyncio.create_task(reachy_service.celebration_dance())
    elif dance_type == "sad":
        asyncio.create_task(reachy_service.sad_dance())
    else:
        raise HTTPException(status_code=400, detail="type must be 'celebrate' or 'sad'")

    return {"status": "dance_started", "type": dance_type}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

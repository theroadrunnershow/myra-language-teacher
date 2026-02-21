import io
import json
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

# ── Configuration helpers ─────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "languages": ["telugu", "assamese"],
    "categories": ALL_CATEGORIES,
    "child_name": "Myra",
    "show_romanized": True,
    "similarity_threshold": 50,  # % match required
    "max_attempts": 3,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults so new keys are always present
        return {**DEFAULT_CONFIG, **data}
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/")
async def home(request: Request):
    config = load_config()
    return templates.TemplateResponse("index.html", {"request": request, "config": config})


@app.get("/settings")
async def settings_page(request: Request):
    config = load_config()
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": config, "all_categories": ALL_CATEGORIES},
    )


# ── API: configuration ────────────────────────────────────────────────────────
@app.get("/api/config")
async def api_get_config():
    return load_config()


@app.post("/api/config")
async def api_save_config(request: Request):
    body = await request.json()
    # Validate
    if "languages" in body and not isinstance(body["languages"], list):
        raise HTTPException(status_code=400, detail="'languages' must be a list")
    if "categories" in body and not isinstance(body["categories"], list):
        raise HTTPException(status_code=400, detail="'categories' must be a list")
    current = load_config()
    current.update(body)
    save_config(current)
    return {"status": "ok", "config": current}


# ── API: words ────────────────────────────────────────────────────────────────
@app.get("/api/word")
async def api_get_word():
    config = load_config()
    langs = config.get("languages", [])
    cats = config.get("categories", ALL_CATEGORIES)

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
):
    config = load_config()
    threshold = float(config.get("similarity_threshold", 50))

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
async def api_all_words():
    config = load_config()
    langs = config.get("languages", [])
    cats = config.get("categories", ALL_CATEGORIES)
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

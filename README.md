# 🦕 Myra Language Teacher

A fun, toddler-friendly web app that teaches **Telugu** and **Assamese** to your 4-year-old through a cute animated pink dino mascot!

## ✨ Features

- 🦕 Animated **pink dino** mascot with expressions (celebrate, shake, talk, idle bounce)
- 🔊 **Hear It!** – plays just the target-language word via TTS
- 🎤 **Say It!** – dino says *"Myra, repeat after me! \<word\>"* then listens via microphone
- 🔁 After a wrong answer the prompt replays automatically before the next attempt
- 📚 **60+ words** across 6 categories (Animals, Colors, Body Parts, Numbers, Food, Objects)
- 🌟 Score tracking with confetti celebrations on correct answers
- 🐛 **Debug panel** – shows exactly what Whisper heard and the match score (useful while tuning)
- ⚙️ **Settings page** – configure child's name, languages, categories, and difficulty

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / FastAPI |
| Speech-to-Text | OpenAI Whisper `20250625` (offline, no API key needed) |
| Text-to-Speech | gTTS (Google TTS, requires internet) |
| Audio conversion | pydub + ffmpeg (WebM / MP4 / OGG → WAV) |
| Fuzzy matching | rapidfuzz (`token_sort_ratio`, 0–100 scale) |
| Frontend | Vanilla HTML / CSS / JS + Jinja2 templates |

---

## 🚀 Local Setup

### 1. Prerequisites

```bash
# Python 3.10+
python3 --version

# ffmpeg (required by pydub for audio conversion)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian
# Windows: https://ffmpeg.org/download.html
```

### 2. Create a virtual environment

```bash
cd myra-language-teacher
python3 -m venv venv
source venv/bin/activate     # macOS/Linux
# venv\Scripts\activate      # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> The Whisper `base` model (~140 MB) downloads automatically on the **first** speech recognition call.

### 4. Run the server

```bash
python main.py
```

Open **http://localhost:8000** in your browser.

---

## 📖 How It Works

### Learning loop

1. App picks a random word (e.g. "cat") and shows it in English + the target language (e.g. **పిల్లి** in Telugu).
2. Press **🔊 Hear It!** to play just the word pronunciation (no prompt).
3. Press **🎤 Say It!** — the dino says *"Myra, repeat after me! పిల్లి"* then the microphone opens automatically.
4. Whisper transcribes what the child says and fuzzy-matches it against the expected word.
   - ✅ **Correct** → confetti + dino dances → auto-advances to next word
   - ❌ **Wrong** → dino shakes → replays *"repeat after me!"* prompt → child tries again
   - ❌ **Out of attempts** → shows the correct answer → moves on after 3 seconds

### Speech recognition details

- Audio MIME type is detected from the browser (`audio/webm`, `audio/mp4`, etc.) and the correct extension is used when converting with ffmpeg.
- Whisper is run with the target language forced (`te` for Telugu, `as` for Assamese). If the output is too short, it falls back to auto-detect.
- Matching runs twice: once against the **native script** (Telugu/Assamese characters) and once against the **romanized pronunciation** (e.g. "pilli"). The higher of the two scores is used — this handles cases where Whisper outputs Latin transliteration instead of the native script.

---

## ⚙️ Settings

Visit **http://localhost:8000/settings** to configure:

| Setting | Description |
|---------|-------------|
| Child's name | Used in the spoken prompt ("Myra, repeat after me!") and header |
| Languages | Telugu, Assamese, or both (randomly alternated per word) |
| Categories | Animals, Colors, Body Parts, Numbers, Food, Common Objects |
| Show romanized | Show phonetic pronunciation guide below the translation |
| Accuracy required | Minimum fuzzy-match score to count as correct (30–90%) |
| Max attempts | How many tries before auto-advancing (2–5) |

Settings are saved to `config.json` and take effect immediately.

---

## 🐛 Debug Panel

After each recording attempt a dark panel appears showing:

```
🎙️ Whisper heard: "పిల్లి"
📊 Match score: 87%
```

Use this to quickly diagnose problems:

| What you see | Likely cause | Fix |
|---|---|---|
| `⚠️ Error: … ffmpeg …` | ffmpeg not installed | `brew install ffmpeg` |
| `Whisper heard: ""` | Audio conversion failed or mic too quiet | Check ffmpeg; speak closer to mic |
| `Whisper heard: "pilli"` (Latin) | Whisper output romanized text | Already handled via romanized fallback |
| `Match score: 0%` with non-empty heard | Child said the English word, not the target language word | Encourage them to say the Telugu/Assamese word |
| Low score despite correct pronunciation | Threshold too high | Settings → lower "Accuracy required" |

---

## 🗂 Project Structure

```
myra-language-teacher/
├── main.py              # FastAPI app & all API routes
├── words_db.py          # Word database (Telugu + Assamese + romanized)
├── speech_service.py    # Whisper STT, MIME detection, romanized fallback
├── tts_service.py       # gTTS text-to-speech (async wrapper)
├── requirements.txt
├── config.json          # Auto-created on first run; stores your settings
├── templates/
│   ├── index.html       # Main learning page (pink dino SVG + lesson UI)
│   └── config.html      # Settings page
└── static/
    ├── css/style.css    # All styles + animations
    └── js/app.js        # Recording, TTS playback, confetti, dino expressions
```

---

## 🔮 Future: AWS Deployment

| Component | AWS target |
|-----------|-----------|
| Backend | ECS Fargate (Whisper needs persistent memory; Lambda cold-starts are too slow) |
| Frontend | S3 + CloudFront |
| STT upgrade | Amazon Transcribe (Telugu supported; keep Whisper for Assamese) |
| TTS upgrade | Amazon Polly Neural voices |
| Config storage | DynamoDB or S3 (replace `config.json`) |

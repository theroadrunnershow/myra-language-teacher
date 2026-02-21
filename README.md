# 🦕 Myra Language Teacher

A fun, toddler-friendly web app that teaches **Telugu** and **Assamese** to your 4-year-old through a cute pink dino mascot!

## ✨ Features

- 🦕 Animated **pink dino** mascot with expressions (celebrate, shake, talk)
- 🔊 **Listens** to the word in the target language (text-to-speech)
- 🎤 **Records** the toddler's speech and checks pronunciation
- 📚 **60+ words** across 6 categories (Animals, Colors, Body Parts, Numbers, Food, Objects)
- 🌟 Score tracking with confetti celebrations
- ⚙️ **Settings page** – configure child's name, language selection, categories, difficulty

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / FastAPI |
| Speech-to-Text | OpenAI Whisper (offline, local) |
| Text-to-Speech | gTTS (Google TTS) |
| Audio conversion | pydub + ffmpeg |
| Fuzzy matching | rapidfuzz |
| Frontend | Vanilla HTML/CSS/JS |

---

## 🚀 Local Setup

### 1. Prerequisites

```bash
# Install Python 3.10+
python3 --version

# Install ffmpeg (required by pydub)
# macOS:
brew install ffmpeg

# Ubuntu/Debian:
sudo apt install ffmpeg

# Windows: Download from https://ffmpeg.org/download.html
```

### 2. Create a virtual environment

```bash
cd myra-language-teacher
python3 -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** First install downloads the Whisper `base` model (~140MB) automatically on first run.

### 4. Run the server

```bash
python main.py
```

The app will be available at **http://localhost:8000**

---

## 📖 How It Works

1. **Load a word** – the app picks a random word (e.g. "cat") and shows it in English + the target language (e.g. పిల్లి in Telugu).
2. **Hear it** – press **🔊 Hear It!** to play the pronunciation.
3. **Say it** – press **🎤 Say It!** and speak into the microphone.
4. **Feedback** – Whisper transcribes the speech and fuzzy-matches it against the expected word.
   - ✅ Correct → confetti + dino dances → next word
   - ❌ Wrong → dino shakes + tries again (up to configured max attempts)
   - ❌ Max attempts reached → shows the answer + moves on

---

## ⚙️ Settings

Visit **http://localhost:8000/settings** to configure:

| Setting | Description |
|---------|-------------|
| Child's name | Displayed in the header |
| Languages | Telugu, Assamese, or both (randomly alternated) |
| Categories | Animals, Colors, Body Parts, Numbers, Food, Objects |
| Show romanized | Show phonetic pronunciation guide below translation |
| Accuracy required | How closely the speech must match (30–90%) |
| Max attempts | How many tries before auto-advancing (2–5) |

---

## 🗂 Project Structure

```
myra-language-teacher/
├── main.py              # FastAPI app & routes
├── words_db.py          # Word database (Telugu + Assamese translations)
├── speech_service.py    # Whisper STT + fuzzy matching
├── tts_service.py       # gTTS text-to-speech
├── requirements.txt
├── config.json          # Auto-created; your saved settings
├── templates/
│   ├── index.html       # Main learning page
│   └── config.html      # Settings page
└── static/
    ├── css/style.css    # All styles
    └── js/app.js        # Frontend logic
```

---

## 🔮 Future: AWS Deployment

When ready to move to AWS, the natural targets are:
- **Backend**: AWS Lambda + API Gateway (or ECS Fargate for Whisper model)
- **Frontend**: S3 + CloudFront static hosting
- **STT upgrade**: Amazon Transcribe (supports Telugu; Assamese via Whisper Lambda layer)
- **TTS upgrade**: Amazon Polly (Neural voices)

---

## 🐛 Troubleshooting

| Problem | Fix |
|---------|-----|
| `ffmpeg not found` | Install ffmpeg (see Prerequisites) |
| Whisper model download slow | It downloads once on first speech recognition; ~140MB for `base` |
| Microphone not working | Allow microphone in browser popup; use HTTPS or localhost |
| gTTS fails | Requires internet connection for Assamese/Telugu TTS |
| Low recognition accuracy | Go to Settings → lower the "Accuracy required" slider |
